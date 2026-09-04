from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Callable

import httpx

from handler import handler


WORKER_LOG = Path(os.environ.get("H3_WORKER_LOG", "/tmp/h3-worker.log"))
# How often a warm worker asks the control plane for its next job.
JOB_POLL_SECONDS = float(os.environ.get("POD_JOB_POLL_SECONDS", "5"))


def jobs_url() -> str:
    """Where this Pod pulls its next job, or "" when it runs one shot.

    The control plane sets POD_JOBS_BASE_URL only when keep-warm is on for the
    lane; RunPod injects RUNPOD_POD_ID into every Pod, and the control plane
    keys its Pod records by that id.
    """
    base = os.environ.get("POD_JOBS_BASE_URL", "").strip().rstrip("/")
    pod_id = os.environ.get("RUNPOD_POD_ID", "").strip()
    if not base or not pod_id:
        return ""
    return f"{base}/{pod_id}/next"


def _tee_process_output() -> None:
    """Mirror this process tree's stdout/stderr into a file.

    SGLang loads the model in a spawned scheduler process. When that child dies
    the parent only sees a bare EOFError off the pipe, and RunPod discards the
    container log together with the one-shot Pod -- so the first real run's
    failure had to be reproduced on a second Pod just to read the traceback.
    Spawned children inherit fds 1 and 2, so routing both through `tee` keeps the
    container log intact while giving the failure callback something to quote.
    """
    try:
        tee = subprocess.Popen(["tee", "-a", str(WORKER_LOG)], stdin=subprocess.PIPE)
    except OSError:
        return
    assert tee.stdin is not None
    sys.stdout.flush()
    sys.stderr.flush()
    os.dup2(tee.stdin.fileno(), 1)
    os.dup2(tee.stdin.fileno(), 2)


def _log_tail(limit: int = 3000) -> str:
    try:
        sys.stdout.flush()
        sys.stderr.flush()
        # Give tee a moment to drain what the dying child wrote.
        time.sleep(1)
        return WORKER_LOG.read_bytes()[-limit:].decode("utf-8", "replace")
    except OSError:
        return ""


def _post_result(payload: dict) -> bool:
    callback_url = os.environ.get("POD_RESULT_CALLBACK_URL", "").strip()
    if not callback_url:
        return False
    token = os.environ.get("POD_RESULT_CALLBACK_TOKEN", "")
    if not token:
        raise RuntimeError("POD_RESULT_CALLBACK_TOKEN is required with a callback URL")
    # Tells the control plane whether this Pod is worth keeping warm: a worker
    # that will ask for more work, or a one-shot that should be deleted now.
    payload = {**payload, "worker": {"pulls_jobs": bool(jobs_url())}}
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


def run_job(job_id: str, params: dict) -> bool:
    """Run one job and deliver its terminal callback. True on success.

    A failure is reported with the worker log tail and returns False: the
    control plane deletes the Pod on failure (a bad host is the usual cause),
    so the caller must stop asking for work and wait to be deleted.
    """
    try:
        result = handler({"id": job_id, "input": params})
    except Exception as exc:
        error = f"{type(exc).__name__}: {str(exc)[:500]}"
        tail = _log_tail()
        if tail:
            error = f"{error}\n--- worker log tail ---\n{tail}"
        if _post_result({"status": "failed", "content": {}, "error": error}):
            print(json.dumps({"event": "pod_callback_complete", "status": "failed"}), flush=True)
            return False
        raise
    # The prompt and credentials are deliberately excluded from process logs.
    print(json.dumps({"event": "smoke_complete", "result": result}), flush=True)
    if _post_result({"status": "succeeded", "content": result, "error": None}):
        print(json.dumps({"event": "pod_callback_complete", "status": "succeeded"}), flush=True)
    return True


def pull_jobs(url: str, token: str, run: Callable[[str, dict], bool], *, sleep=time.sleep) -> None:
    """Keep the loaded model busy: ask for the next job until told to stop.

    200 carries a job, 204 means nothing is queued yet, and 404/410 mean the
    control plane has retired this Pod. Everything else is treated as a blip.
    The control plane owns deletion: it removes the Pod after the idle window,
    so this loop never exits on its own -- an exited container still bills.
    """
    while True:
        try:
            response = httpx.post(
                url,
                headers={"Authorization": f"Bearer {token}"},
                json={"poll_seconds": JOB_POLL_SECONDS},
                timeout=30,
            )
        except httpx.HTTPError as exc:
            print(json.dumps({"event": "job_poll_error", "error": str(exc)[:200]}), flush=True)
            sleep(JOB_POLL_SECONDS)
            continue
        if response.status_code in (404, 410):
            print(json.dumps({"event": "job_poll_retired", "status": response.status_code}), flush=True)
            return
        if response.status_code != 200:
            sleep(JOB_POLL_SECONDS)
            continue
        job = (response.json() or {}).get("job") or {}
        task_id = str(job.get("task_id") or "")
        params = job.get("input")
        if not task_id or not isinstance(params, dict):
            sleep(JOB_POLL_SECONDS)
            continue
        # The handler and the result poster read these at call time.
        os.environ["POD_RESULT_CALLBACK_URL"] = str(job.get("result_url") or "")
        os.environ["POD_PROGRESS_CALLBACK_URL"] = str(job.get("progress_url") or "")
        print(json.dumps({"event": "job_pulled", "task_id": task_id}), flush=True)
        if not run(task_id, params):
            return


def main() -> None:
    raw_input = os.environ.get("SMOKE_INPUT_JSON", "").strip()
    if not raw_input:
        raise RuntimeError("SMOKE_INPUT_JSON is required")
    params = json.loads(raw_input)
    _tee_process_output()
    if not run_job("h3-pod-smoke", params):
        _await_deletion()
    url = jobs_url()
    if url and os.environ.get("POD_RESULT_CALLBACK_URL", "").strip():
        # The model is loaded and paid for; take the queue while it is warm.
        pull_jobs(url, os.environ.get("POD_RESULT_CALLBACK_TOKEN", ""), run_job)
    _await_deletion()


if __name__ == "__main__":
    main()
