from __future__ import annotations

import json
import os
import time

import httpx

from handler import handler


def _post_result(payload: dict) -> bool:
    callback_url = os.environ.get("POD_RESULT_CALLBACK_URL", "").strip()
    if not callback_url:
        return False
    token = os.environ.get("POD_RESULT_CALLBACK_TOKEN", "")
    if not token:
        raise RuntimeError("POD_RESULT_CALLBACK_TOKEN is required with a callback URL")
    last_error: Exception | None = None
    for delay in (0, 2, 5, 10, 20, 30):
        if delay:
            time.sleep(delay)
        try:
            response = httpx.post(
                callback_url,
                headers={"Authorization": f"Bearer {token}"},
                json=payload,
                timeout=30,
            )
            response.raise_for_status()
            return True
        except httpx.HTTPError as exc:
            last_error = exc
    raise RuntimeError("Pod result callback failed after retries") from last_error


def _await_deletion() -> None:
    # Railway deletes the billed Pod after committing the callback. Keeping the
    # container alive prevents RunPod from restarting a completed one-shot job.
    while True:
        time.sleep(60)


def main() -> None:
    raw_input = os.environ.get("SMOKE_INPUT_JSON", "").strip()
    if not raw_input:
        raise RuntimeError("SMOKE_INPUT_JSON is required")
    params = json.loads(raw_input)
    try:
        result = handler({"id": "wan-pod-smoke", "input": params})
    except Exception as exc:
        if _post_result(
            {
                "status": "failed",
                "content": {},
                "error": f"{type(exc).__name__}: {str(exc)[:500]}",
            }
        ):
            print(json.dumps({"event": "pod_callback_complete", "status": "failed"}), flush=True)
            _await_deletion()
        raise
    # The prompt and credentials are deliberately excluded from process logs.
    print(json.dumps({"event": "smoke_complete", "result": result}), flush=True)
    if _post_result({"status": "succeeded", "content": result, "error": None}):
        print(json.dumps({"event": "pod_callback_complete", "status": "succeeded"}), flush=True)
        _await_deletion()


if __name__ == "__main__":
    main()
