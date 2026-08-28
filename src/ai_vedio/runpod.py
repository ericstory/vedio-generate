from __future__ import annotations

from time import sleep
from typing import Any

import httpx

from .config import RunPodSettings
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
            "inference_seconds",
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
