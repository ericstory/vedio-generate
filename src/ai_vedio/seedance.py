from __future__ import annotations

from pathlib import Path
from time import monotonic, sleep
from typing import Any, Iterable

import httpx

from .capabilities import DEFAULTS, TERMINAL_STATUSES
from .config import Settings


class SeedanceError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        error: Any = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.error = error

    @property
    def code(self) -> str | None:
        if isinstance(self.error, dict):
            value = self.error.get("code") or self.error.get("type")
            return str(value) if value else None
        return None

    @property
    def upstream_message(self) -> str:
        if isinstance(self.error, dict):
            value = self.error.get("message") or self.error.get("msg")
            if value:
                return str(value)
        return str(self)


class SeedanceClient:
    def __init__(self, settings: Settings, *, timeout: float = 60.0) -> None:
        self.settings = settings
        self._client = httpx.Client(
            base_url=settings.modelark_base_url,
            headers={
                "Authorization": f"Bearer {settings.modelark_api_key}",
                "Content-Type": "application/json",
            },
            timeout=timeout,
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "SeedanceClient":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def _request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        response = self._client.request(method, path, **kwargs)
        try:
            body = response.json()
        except ValueError as exc:
            raise SeedanceError(f"ModelArk returned HTTP {response.status_code} with invalid JSON") from exc
        if response.is_error:
            error = body.get("error", body) if isinstance(body, dict) else body
            raise SeedanceError(
                f"ModelArk HTTP {response.status_code}: {error}",
                status_code=response.status_code,
                error=error,
            )
        return body

    def list_tasks(self, *, page_num: int = 1, page_size: int = 10) -> dict[str, Any]:
        return self._request(
            "GET",
            "/contents/generations/tasks",
            params={"page_num": page_num, "page_size": page_size},
        )

    def create_task(
        self,
        *,
        model: str,
        content: Iterable[dict[str, Any]],
        resolution: str = DEFAULTS.resolution,
        ratio: str = DEFAULTS.ratio,
        duration: int = DEFAULTS.duration,
        generate_audio: bool = DEFAULTS.generate_audio,
        watermark: bool = DEFAULTS.watermark,
        **options: Any,
    ) -> dict[str, Any]:
        payload = {
            "model": self.settings.endpoint_for(model),
            "content": list(content),
            "resolution": resolution,
            "ratio": ratio,
            "duration": duration,
            "generate_audio": generate_audio,
            "watermark": watermark,
            **options,
        }
        return self._request("POST", "/contents/generations/tasks", json=payload)

    def create_text_video(self, *, prompt: str, model: str, **options: Any) -> dict[str, Any]:
        prompt = prompt.strip()
        if not prompt:
            raise ValueError("prompt cannot be empty")
        return self.create_task(
            model=model,
            content=[{"type": "text", "text": prompt}],
            **options,
        )

    def create_reference_video(
        self,
        *,
        prompt: str,
        image_url: str,
        model: str,
        **options: Any,
    ) -> dict[str, Any]:
        """Create a video guided by a reference image URL or data URL."""
        prompt = prompt.strip()
        if not prompt:
            raise ValueError("prompt cannot be empty")
        if not image_url:
            raise ValueError("image_url cannot be empty")
        return self.create_task(
            model=model,
            content=[
                {"type": "text", "text": prompt},
                {
                    "type": "image_url",
                    "image_url": {"url": image_url},
                    "role": "reference_image",
                },
            ],
            **options,
        )

    def get_task(self, task_id: str) -> dict[str, Any]:
        return self._request("GET", f"/contents/generations/tasks/{task_id}")

    def wait_for_task(
        self,
        task_id: str,
        *,
        poll_interval: float = 5.0,
        timeout: float = 30 * 60,
    ) -> dict[str, Any]:
        deadline = monotonic() + timeout
        while True:
            task = self.get_task(task_id)
            status = task.get("status")
            if status in TERMINAL_STATUSES:
                if status != "succeeded":
                    raise SeedanceError(f"Video task {task_id} ended as {status}: {task.get('error')}")
                return task
            if monotonic() >= deadline:
                raise TimeoutError(f"Timed out waiting for video task {task_id}")
            sleep(poll_interval)

    @staticmethod
    def video_url(task: dict[str, Any]) -> str:
        url = (task.get("content") or {}).get("video_url")
        if not url:
            raise SeedanceError("Task response does not contain content.video_url")
        return str(url)

    def download_video(self, task: dict[str, Any], destination: str | Path) -> Path:
        path = Path(destination)
        path.parent.mkdir(parents=True, exist_ok=True)
        with httpx.stream("GET", self.video_url(task), timeout=120.0, follow_redirects=True) as response:
            response.raise_for_status()
            with path.open("wb") as output:
                for chunk in response.iter_bytes():
                    output.write(chunk)
        return path
