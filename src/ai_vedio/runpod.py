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
        try:
            response = self._management_client.patch(
                f"/endpoints/{self.settings.endpoint_id}",
                json={"workersMax": workers_max, "workersMin": 0},
            )
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

    def create_text_video(self, *, prompt: str, model: str, **options: Any) -> dict[str, Any]:
        if model != self.settings.ui_model_id:
            raise ValueError(f"Unknown self-hosted Pod model: {model}")
        task_id = str(options.get("task_id") or "").strip()
        if not task_id:
            raise ValueError("task_id is required for the Wan Pod callback")
        callback_url = f"{self.settings.callback_url.rstrip('/')}/{task_id}"
        smoke_input = {
            "prompt": prompt.strip(),
            "model_id": self.settings.model_id,
            "model_version": self.settings.model_version,
            "workflow_version": self.settings.workflow_version,
            "ratio": options.get("ratio", "16:9"),
            "resolution": options.get("resolution", "480p"),
            "duration": options.get("duration", 5),
            "generate_audio": options.get("generate_audio", True),
            "adult_adapter_id": self.settings.adult_adapter_id,
            "adult_adapter_version": self.settings.adult_adapter_version,
            "adult_adapter_strength": self.settings.adult_adapter_strength,
        }
        template = self._request("GET", f"/templates/{self.settings.template_id}")
        template_env = template.get("env") if isinstance(template.get("env"), dict) else {}
        pod_env = {
            **template_env,
            "SMOKE_INPUT_JSON": json.dumps(smoke_input, ensure_ascii=False),
            "POD_RESULT_CALLBACK_URL": callback_url,
            "POD_RESULT_CALLBACK_TOKEN": self.settings.callback_token,
        }
        payload = {
            "name": f"papa-wan-{task_id[:12]}",
            "templateId": self.settings.template_id,
            "cloudType": "SECURE",
            "computeType": "GPU",
            "gpuTypeIds": [self.settings.gpu_id],
            "gpuTypePriority": "custom",
            "gpuCount": 1,
            "dataCenterPriority": "custom",
            "allowedCudaVersions": ["13.0"],
            "volumeMountPath": self.settings.volume_mount_path,
            "containerDiskInGb": 20,
            "volumeInGb": 0,
            "env": pod_env,
        }
        lanes = [(self.settings.data_center_id, self.settings.network_volume_id)]
        if (
            self.settings.fallback_data_center_id
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
        for data_center_id, network_volume_id in lanes:
            lane_payload = {
                **payload,
                "dataCenterIds": [data_center_id],
                "networkVolumeId": network_volume_id,
            }
            try:
                pod = self._request("POST", "/pods", json=lane_payload)
                selected_data_center = data_center_id
                break
            except RunPodError as exc:
                if "no instances currently available" not in exc.upstream_message.lower():
                    raise
                last_capacity_error = exc
        if pod is None:
            assert last_capacity_error is not None
            raise last_capacity_error
        pod_id = str(pod.get("id") or "")
        if not pod_id:
            raise RunPodError("RunPod 创建 Pod 后未返回编号")
        try:
            price = float(pod.get("adjustedCostPerHr") or pod.get("costPerHr"))
        except (TypeError, ValueError):
            self.delete_pod(pod_id)
            raise RunPodError("RunPod 创建 Pod 后未返回可验证价格")
        if price > self.settings.maximum_price_per_hour:
            self.delete_pod(pod_id)
            raise RunPodError(
                f"RunPod Pod price ${price:.2f}/h exceeds the configured cap"
            )
        machine = pod.get("machine") if isinstance(pod.get("machine"), dict) else {}
        actual_gpu = str(machine.get("gpuId") or "")
        if actual_gpu and actual_gpu != self.settings.gpu_id:
            self.delete_pod(pod_id)
            raise RunPodError(f"RunPod allocated unexpected GPU type: {actual_gpu}")
        return {
            "id": pod_id,
            "status": "queued",
            "content": {
                "video_url": None,
                "gpu_name": self.settings.gpu_id,
                "pod_price_per_hour": price,
                "pod_data_center_id": selected_data_center,
            },
            "error": None,
        }

    def get_task(self, pod_id: str) -> dict[str, Any]:
        pod = self._request("GET", f"/pods/{pod_id}")
        runtime = str(pod.get("runtimeStatus") or pod.get("desiredStatus") or "").lower()
        status = "processing" if runtime in {"running", "initializing", "created"} else "queued"
        return {"id": pod_id, "status": status, "content": {}, "error": None}
