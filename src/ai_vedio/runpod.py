from __future__ import annotations

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
        self._client = httpx.Client(
            base_url=f"{settings.api_base_url}/{settings.endpoint_id}",
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
        return {
            "id": str(body.get("id") or ""),
            "status": _STATUS_MAP.get(str(body.get("status") or "").upper(), "processing"),
            "content": {"video_url": output.get("video_url")},
            "error": error,
        }

    def create_text_video(self, *, prompt: str, model: str, **options: Any) -> dict[str, Any]:
        if model != "pinkcherry-ltx-2.3-v1.8":
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
        # RunPod's per-job TTL query value is milliseconds. Execution timeout is
        # configured on the endpoint so queue policy remains an infrastructure concern.
        return self._normalize(self._request("POST", "/run?ttl=7200000", json=payload))

    def get_task(self, task_id: str) -> dict[str, Any]:
        return self._normalize(self._request("GET", f"/status/{task_id}"))
