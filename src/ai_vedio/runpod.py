from __future__ import annotations

import json
from time import sleep
from typing import Any

import httpx

from .config import RunPodPodSettings, RunPodSettings
from .seedance import SeedanceError


_STATUS_MAP = {
    "IN_QUEUE": "queued",
    "IN_PROGRESS": "processing",
    "COMPLETED": "succeeded",
    "FAILED": "failed",
    "TIMED_OUT": "failed",
    "CANCELLED": "cancelled",
}


class RunPodError(SeedanceError):
    """Provider error with the same safe web-facing shape as SeedanceError."""


class RunPodClient:
    """Independent adapter for the self-hosted RunPod Serverless chain."""

    def __init__(self, settings: RunPodSettings, *, timeout: float = 60.0) -> None:
        self.settings = settings
        self._activation_retry_delays = (0.5, 1.0, 2.0, 3.0, 4.0)
        self._client = httpx.Client(
            base_url=f"{settings.api_base_url}/{settings.endpoint_id}",
            headers={
                "Authorization": f"Bearer {settings.api_key}",
                "Content-Type": "application/json",
            },
            timeout=timeout,
        )
        self._management_client = httpx.Client(
            base_url=settings.management_api_base_url,
            headers={
                "Authorization": f"Bearer {settings.api_key}",
                "Content-Type": "application/json",
            },
            timeout=timeout,
        )

    def close(self) -> None:
        self._client.close()
        self._management_client.close()

    def set_workers_max(self, workers_max: int) -> None:
        """Explicit endpoint gate used as a cost guard around private jobs."""
        if workers_max not in {0, 1}:
            raise ValueError("private endpoint workers_max must be 0 or 1")
        if self.settings.use_management_api_v1:
            # rp-migrate: keep-v1 start
            path = f"/endpoints/{self.settings.endpoint_id}"  # rp-migrate: keep-v1
            payload = {"workersMax": workers_max, "workersMin": 0}
            # rp-migrate: keep-v1 end
        else:
            path = f"/serverless/{self.settings.endpoint_id}"
            payload = {"workers": {"max": workers_max, "min": 0}}
        try:
            response = self._management_client.patch(path, json=payload)
        except httpx.HTTPError as exc:
            raise RunPodError(
                "云 GPU 成本控制接口连接失败", error={"message": str(exc)}
            ) from exc
        if response.is_error:
            try:
                error = response.json().get("error", response.json())
            except ValueError:
                error = f"HTTP {response.status_code}"
            raise RunPodError(
                f"RunPod endpoint update returned HTTP {response.status_code}",
                status_code=response.status_code,
                error=error,
            )

    def _request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        try:
            response = self._client.request(method, path, **kwargs)
        except httpx.HTTPError as exc:
            raise RunPodError("云 GPU 服务连接失败", error={"message": str(exc)}) from exc
        try:
            body = response.json()
        except ValueError as exc:
            raise RunPodError(
                f"RunPod returned HTTP {response.status_code} with invalid JSON",
                status_code=response.status_code,
            ) from exc
        if response.is_error:
            error = body.get("error", body) if isinstance(body, dict) else body
            raise RunPodError(
                f"RunPod HTTP {response.status_code}: {error}",
                status_code=response.status_code,
                error=error,
            )
        return body

    @staticmethod
    def _normalize(body: dict[str, Any]) -> dict[str, Any]:
        output = body.get("output") if isinstance(body.get("output"), dict) else {}
        error = body.get("error") or output.get("error")
        content = {"video_url": output.get("video_url")}
        for key in (
            "seed",
            "model_id",
            "model_version",
            "workflow_version",
            "adult_adapter_id",
            "adult_adapter_version",
            "adult_adapter_strength",
            "gpu_name",
            "engine",
            "engine_version",
            "quantization",
            "inference_seconds",
            "video_inference_seconds",
            "audio_inference_seconds",
            "model_load_seconds",
            "audio_model_load_seconds",
            "upload_seconds",
            "attention_backend",
            "inference_steps",
            "guidance_scale",
            "guidance_scale_2",
            "lightning_enabled",
            "lightning_strength",
            "flow_shift",
            "peak_memory_mb",
            "duration",
            "fps",
            "frame_count",
            "width",
            "height",
            "has_audio",
            "audio_model_id",
            "audio_sample_rate",
        ):
            if output.get(key) is not None:
                content[key] = output[key]
        return {
            "id": str(body.get("id") or ""),
            "status": _STATUS_MAP.get(str(body.get("status") or "").upper(), "processing"),
            "content": content,
            "error": error,
        }

    def create_text_video(self, *, prompt: str, model: str, **options: Any) -> dict[str, Any]:
        if model != self.settings.ui_model_id:
            raise ValueError(f"Unknown self-hosted model: {model}")
        payload = {
            "input": {
                "prompt": prompt.strip(),
                "model_id": self.settings.model_id,
                "model_version": self.settings.model_version,
                "workflow_version": self.settings.workflow_version,
                "ratio": options.get("ratio", "16:9"),
                "resolution": options.get("resolution", "720p"),
                "duration": options.get("duration", 6),
                "generate_audio": options.get("generate_audio", True),
            },
        }
        if self.settings.adult_adapter_id:
            payload["input"].update(
                {
                    "adult_adapter_id": self.settings.adult_adapter_id,
                    "adult_adapter_version": self.settings.adult_adapter_version,
                    "adult_adapter_strength": self.settings.adult_adapter_strength,
                }
            )
        # Keep the endpoint hard-disabled between jobs because RunPod's idle scaler
        # has occasionally left this private worker allocated. A failed submission
        # closes the gate immediately; successful jobs are closed by the web guard.
        self.set_workers_max(1)
        try:
            # RunPod's per-job TTL query value is milliseconds. Execution timeout is
            # configured on the endpoint so queue policy remains an infrastructure concern.
            for attempt, delay in enumerate((0.0, *self._activation_retry_delays)):
                if delay:
                    sleep(delay)
                try:
                    return self._normalize(
                        self._request("POST", "/run?ttl=7200000", json=payload)
                    )
                except RunPodError as exc:
                    final_attempt = attempt == len(self._activation_retry_delays)
                    if exc.code != "ENDPOINT_PAUSED" or final_attempt:
                        raise
            raise RuntimeError("unreachable RunPod activation retry state")
        except Exception:
            self.set_workers_max(0)
            raise

    def get_task(self, task_id: str) -> dict[str, Any]:
        return self._normalize(self._request("GET", f"/status/{task_id}"))


class RunPodPodClient:
    """Launch one exact, price-capped GPU Pod for a private Wan task."""

    def __init__(self, settings: RunPodPodSettings, *, timeout: float = 60.0) -> None:
        self.settings = settings
        self._client = httpx.Client(
            base_url=settings.api_base_url,
            headers={
                "Authorization": f"Bearer {settings.api_key}",
                "Content-Type": "application/json",
            },
            timeout=timeout,
        )

    def close(self) -> None:
        self._client.close()

    def _request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        try:
            response = self._client.request(method, path, **kwargs)
        except httpx.HTTPError as exc:
            raise RunPodError("云 GPU Pod 管理接口连接失败", error={"message": str(exc)}) from exc
        if response.status_code == 204:
            return {}
        try:
            body = response.json()
        except ValueError as exc:
            raise RunPodError(
                f"RunPod Pod API returned HTTP {response.status_code} with invalid JSON",
                status_code=response.status_code,
            ) from exc
        if response.is_error:
            error = body.get("error", body) if isinstance(body, dict) else body
            raise RunPodError(
                f"RunPod Pod API HTTP {response.status_code}: {error}",
                status_code=response.status_code,
                error=error,
            )
        return body

    def delete_pod(self, pod_id: str) -> None:
        try:
            self._request("DELETE", f"/pods/{pod_id}")
        except RunPodError as exc:
            if exc.status_code != 404:
                raise

    def list_pods(self) -> list[dict[str, Any]]:
        """Every Pod on the account, reduced to id, name and status.

        The guard loop compares this against the Pods it knows about: a Pod
        wearing this lane's production name that the control plane is not
        tracking is billing for nobody and gets deleted.
        """
        body = self._request("GET", "/pods")  # rp-migrate: ignore
        rows: Any = body.get("pods") if isinstance(body, dict) else body
        pods: list[dict[str, Any]] = []
        for row in rows or []:
            if not isinstance(row, dict) or not row.get("id"):
                continue
            pods.append(
                {
                    "id": str(row["id"]),
                    "name": str(row.get("name") or ""),
                    "status": str(
                        row.get("status") or row.get("desiredStatus") or ""
                    ).lower(),
                }
            )
        return pods

    def callback_urls(self, task_id: str) -> tuple[str, str]:
        """(terminal-result URL, live-progress URL) for one task.

        The live-progress route lives next to the terminal-result route; a
        customized callback URL without the standard suffix opts out cleanly.
        """
        callback_url = f"{self.settings.callback_url.rstrip('/')}/{task_id}"
        progress_url = ""
        if "/pod-result" in callback_url:
            progress_url = callback_url.replace("/pod-result", "/pod-progress")
        return callback_url, progress_url

    def jobs_base_url(self) -> str:
        """Where a warm worker asks for its next job; empty when keep-warm is off."""
        if self.settings.keep_warm_idle_seconds <= 0:
            return ""
        base = self.settings.callback_url.rstrip("/")
        if "/pod-result" not in base:
            return ""
        return base.replace("/pod-result", "/pod-jobs")

    def job_input(
        self,
        *,
        prompt: str,
        ratio: str = "16:9",
        resolution: str = "480p",
        duration: int = 5,
        generate_audio: bool = True,
    ) -> dict[str, Any]:
        """The worker's job input: the request plus this lane's pinned weights."""
        job: dict[str, Any] = {
            "prompt": prompt.strip(),
            "model_id": self.settings.model_id,
            "model_version": self.settings.model_version,
            "workflow_version": self.settings.workflow_version,
            "ratio": ratio,
            "resolution": resolution,
            "duration": duration,
            "generate_audio": generate_audio,
        }
        # Each lane pins its adult layer with exactly one of these shapes, and
        # the Worker rejects the job when the submitted pin disagrees with the
        # weights it actually loaded. Wan adapts a base model with a LoRA; H3
        # swaps the whole transformer for a fine-tuned checkpoint.
        if self.settings.adult_adapter_id:
            job["adult_adapter_id"] = self.settings.adult_adapter_id
            job["adult_adapter_version"] = self.settings.adult_adapter_version
            job["adult_adapter_strength"] = self.settings.adult_adapter_strength
        if self.settings.adult_model_id:
            job["adult_model_id"] = self.settings.adult_model_id
            job["adult_model_version"] = self.settings.adult_model_version
        return job

    def create_text_video(self, *, prompt: str, model: str, **options: Any) -> dict[str, Any]:
        if model != self.settings.ui_model_id:
            raise ValueError(f"Unknown self-hosted Pod model: {model}")
        task_id = str(options.get("task_id") or "").strip()
        if not task_id:
            raise ValueError("task_id is required for the Wan Pod callback")
        callback_url, progress_url = self.callback_urls(task_id)
        smoke_input = self.job_input(
            prompt=prompt,
            ratio=options.get("ratio", "16:9"),
            resolution=options.get("resolution", "480p"),
            duration=options.get("duration", 5),
            generate_audio=options.get("generate_audio", True),
        )
        template = self._request("GET", f"/templates/{self.settings.template_id}")
        template_env = template.get("env") if isinstance(template.get("env"), dict) else {}
        pod_env = {
            **template_env,
            "SMOKE_INPUT_JSON": json.dumps(smoke_input, ensure_ascii=False),
            "POD_RESULT_CALLBACK_URL": callback_url,
            "POD_RESULT_CALLBACK_TOKEN": self.settings.callback_token,
        }
        if progress_url:
            pod_env["POD_PROGRESS_CALLBACK_URL"] = progress_url
        # With keep-warm on, the worker appends its own RUNPOD_POD_ID and asks
        # here for the next job once the first one is delivered.
        jobs_base_url = self.jobs_base_url()
        if jobs_base_url:
            pod_env["POD_JOBS_BASE_URL"] = jobs_base_url
        if self.settings.use_management_api_v1:
            # rp-migrate: keep-v1 start
            payload = {  # rp-migrate: keep-v1
                "name": f"{self.settings.name_prefix}-{task_id[:12]}",
                "templateId": self.settings.template_id,
                "cloudType": "SECURE",
                "computeType": "GPU",
                "gpuTypeIds": [self.settings.gpu_id],
                "gpuTypePriority": "custom",
                "gpuCount": 1,
                "dataCenterPriority": "custom",
                "allowedCudaVersions": ["13.0"],
                "volumeMountPath": self.settings.volume_mount_path,
                "containerDiskInGb": self.settings.container_disk_gb,
                "volumeInGb": 0,
                "env": pod_env,
            }
            # rp-migrate: keep-v1 end
        else:
            # rp-migrate: ignore start
            payload = {
                "name": f"{self.settings.name_prefix}-{task_id[:12]}",
                "templateId": self.settings.template_id,
                "cloud": "SECURE",
                "gpu": {
                    "id": self.settings.gpu_id,
                    "count": 1,
                    "minCudaVersion": "13.0",
                },
                "disk": self.settings.container_disk_gb,
                "env": pod_env,
            }
            # rp-migrate: ignore end
        # An empty volume id means the weights come down to container disk at
        # start instead of off a regional volume. That drops the data-centre pin
        # entirely, which is the difference between "any suitable card anywhere"
        # and "the two data centres our volumes happen to live in" -- and on
        # 2026-09-01 those two had zero suitable cards for hours.
        if not self.settings.network_volume_id:
            # Volume-free: the preferred data centres first (fast, known image
            # pulls), then anywhere at all. The first LTX Pod landed in
            # EUR-IS-2 and spent over an hour fetching an 8.75 GB image.
            lanes: list[tuple[str, str]] = [
                (dc, "") for dc in self.settings.preferred_data_center_ids
            ] + [("", "")]
        else:
            lanes = [(self.settings.data_center_id, self.settings.network_volume_id)]
        if (
            self.settings.network_volume_id
            and self.settings.fallback_data_center_id
            and self.settings.fallback_network_volume_id
        ):
            lanes.append(
                (
                    self.settings.fallback_data_center_id,
                    self.settings.fallback_network_volume_id,
                )
            )
        for lane in self.settings.additional_region_volumes:
            if lane not in lanes:
                lanes.append(lane)
        pod: dict[str, Any] | None = None
        selected_data_center = ""
        last_capacity_error: RunPodError | None = None
        # Exact-GPU secure stock is thin enough that one sweep over the lanes
        # regularly loses the race even when every lane reports stock. A capacity
        # rejection creates no Pod and bills nothing, so a few more sweeps are
        # free; they just cost the caller a little latency.
        # Pinning one exact GPU model is what turns a busy hour into an outage:
        # the RTX PRO 6000 family went unavailable across every data centre,
        # secure and community alike, while other cards still had capacity.
        # Candidates are tried in preference order, so the best card still wins
        # whenever it is there.
        gpu_candidates = [self.settings.gpu_id, *self.settings.additional_gpu_ids]
        # The guard loop retries on its own cadence and asks for a single sweep
        # per pass; direct callers keep the configured count.
        sweeps = int(options.get("capacity_retry_sweeps") or self.settings.capacity_retry_sweeps)
        attempts = [
            (gpu_id, dc, vol)
            for _ in range(max(1, sweeps))
            for gpu_id in gpu_candidates
            for dc, vol in lanes
        ]
        per_sweep = len(gpu_candidates) * len(lanes)
        for index, (gpu_id, data_center_id, network_volume_id) in enumerate(attempts):
            if index and index % per_sweep == 0:
                sleep(self.settings.capacity_retry_delay_seconds)
            lane_payload = {**payload}
            if data_center_id:
                lane_payload["dataCenterIds"] = [data_center_id]
            if self.settings.use_management_api_v1:
                # rp-migrate: keep-v1 start
                lane_payload["gpuTypeIds"] = [gpu_id]  # rp-migrate: keep-v1
                # rp-migrate: keep-v1 end
            else:
                lane_payload["gpu"] = {**payload["gpu"], "id": gpu_id}
            if not network_volume_id:
                pass
            elif self.settings.use_management_api_v1:
                # rp-migrate: keep-v1 start
                lane_payload["networkVolumeId"] = network_volume_id  # rp-migrate: keep-v1
                # rp-migrate: keep-v1 end
            else:
                lane_payload["mounts"] = {
                    "network": [
                        {
                            "volumeId": network_volume_id,
                            "path": self.settings.volume_mount_path,
                        }
                    ]
                }
            try:
                pod = self._request("POST", "/pods", json=lane_payload)  # rp-migrate: ignore
                selected_data_center = data_center_id
                break
            except RunPodError as exc:
                capacity_message = exc.upstream_message.lower()
                if not any(
                    phrase in capacity_message
                    for phrase in (
                        "no instances currently available",
                        "no longer any instances available",
                    )
                ):
                    raise
                last_capacity_error = exc
        if pod is None:
            assert last_capacity_error is not None
            raise last_capacity_error
        pod_id = str(pod.get("id") or "")
        if not pod_id:
            raise RunPodError("RunPod 创建 Pod 后未返回编号")
        try:
            if self.settings.use_management_api_v1:
                # rp-migrate: keep-v1 start
                raw_price = pod.get("adjustedCostPerHr") or pod.get("costPerHr")
                # rp-migrate: keep-v1 end
            else:
                raw_price = pod.get("cost")
            price = float(raw_price)
        except (TypeError, ValueError):
            self.delete_pod(pod_id)
            raise RunPodError("RunPod 创建 Pod 后未返回可验证价格")
        if price > self.settings.maximum_price_per_hour:
            self.delete_pod(pod_id)
            raise RunPodError(
                f"RunPod Pod price ${price:.2f}/h exceeds the configured cap"
            )
        if self.settings.use_management_api_v1:
            machine = pod.get("machine") if isinstance(pod.get("machine"), dict) else {}
            actual_gpu = str(machine.get("gpuId") or "")
        else:
            gpu = pod.get("gpu") if isinstance(pod.get("gpu"), dict) else {}
            actual_gpu = str(gpu.get("id") or "")
        allowed_gpus = {self.settings.gpu_id, *self.settings.additional_gpu_ids}
        if actual_gpu and actual_gpu not in allowed_gpus:
            self.delete_pod(pod_id)
            raise RunPodError(f"RunPod allocated unexpected GPU type: {actual_gpu}")
        return {
            "id": pod_id,
            "status": "queued",
            "content": {
                "video_url": None,
                # Report what RunPod actually allocated, not what was asked
                # for: with a candidate list those are no longer the same thing.
                "gpu_name": actual_gpu or self.settings.gpu_id,
                "pod_price_per_hour": price,
                "pod_data_center_id": selected_data_center,
            },
            "error": None,
        }

    def get_task(self, pod_id: str) -> dict[str, Any]:
        pod = self._request("GET", f"/pods/{pod_id}")  # rp-migrate: ignore
        if self.settings.use_management_api_v1:
            # rp-migrate: keep-v1 start
            runtime = str(  # rp-migrate: keep-v1
                pod.get("runtimeStatus") or pod.get("desiredStatus") or ""
            ).lower()
            # rp-migrate: keep-v1 end
        else:
            runtime = str(pod.get("status") or "").lower()
        status = "processing" if runtime in {"running", "initializing", "created"} else "queued"
        return {"id": pod_id, "status": status, "content": {}, "error": None}
