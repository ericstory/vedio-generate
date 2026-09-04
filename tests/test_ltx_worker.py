from __future__ import annotations

import importlib.util
import ast
import json
from pathlib import Path

import pytest


ROOT = Path(__file__).parents[1]
WORKER_ROOT = ROOT / "workers" / "ltx-video"


def load_worker_config():
    spec = importlib.util.spec_from_file_location(
        "ltx_worker_config", WORKER_ROOT / "worker_config.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_two_stage_dimensions_are_divisible_by_64() -> None:
    config = load_worker_config()
    for resolution in ("480p", "720p"):
        for ratio in ("16:9", "9:16", "1:1", "4:3", "3:4", "21:9"):
            width, height = config.dimensions(ratio, resolution)
            assert width % 64 == 0
            assert height % 64 == 0


def test_frame_count_uses_ltx_temporal_grid() -> None:
    config = load_worker_config()
    assert config.frame_count(4) == 97
    assert config.frame_count(6) == 145
    assert config.frame_count(15) % 8 == 1


def test_quantization_adapts_to_gpu_generation() -> None:
    config = load_worker_config()
    assert config.quantization_for_compute_capability("auto", 8, 9) == "fp8-cast"
    assert config.quantization_for_compute_capability("auto", 9, 0) == "fp8-cast"
    assert config.quantization_for_compute_capability("auto", 12, 0) == "fp8-cast"
    assert config.quantization_for_compute_capability("auto", 8, 0) == ""
    assert config.quantization_for_compute_capability("fp8-cast", 8, 0) == "fp8-cast"


def test_gpu_policy_stays_inside_cost_and_vram_limits() -> None:
    policy = json.loads((WORKER_ROOT / "gpu_policy.json").read_text(encoding="utf-8"))
    assert policy["minimum_vram_gb"] == 96
    assert policy["maximum_secure_price_usd_per_hour"] == 3.0
    assert policy["maximum_serverless_price_usd_per_hour"] == 3.0
    assert policy["allow_fallback_gpu_types"] is False
    assert [gpu["id"] for gpu in policy["gpu_types"]] == [
        "NVIDIA RTX PRO 6000 Blackwell Server Edition"
    ]
    assert all(gpu["vram_gb"] == 96 for gpu in policy["gpu_types"])
    assert all(gpu["secure_price_usd_per_hour"] <= 3.0 for gpu in policy["gpu_types"])


def test_private_adult_research_prompt_is_allowed() -> None:
    config = load_worker_config()
    config.validate_prompt("Consensual erotic scene between two fictional adults, cinematic")


@pytest.mark.parametrize(
    "prompt",
    [
        "explicit sex involving an underage person",
        "non-consensual forced sex scene",
        "explicit celebrity deepfake",
        "兽交视频",
    ],
)
def test_hard_policy_boundaries_are_rejected(prompt: str) -> None:
    config = load_worker_config()
    with pytest.raises(ValueError, match="content policy"):
        config.validate_prompt(prompt)


def test_model_lock_matches_expected_storage_budget() -> None:
    lock = json.loads((WORKER_ROOT / "models.lock.json").read_text(encoding="utf-8"))
    assert sum(item["size"] for item in lock["artifacts"]) == lock["expected_download_bytes"]
    assert lock["expected_download_bytes"] == 79_155_298_327
    assert all(len(item["revision"]) == 40 for item in lock["artifacts"])


def test_pipeline_runs_under_torch_inference_mode() -> None:
    tree = ast.parse((WORKER_ROOT / "handler.py").read_text(encoding="utf-8"))
    inference_contexts = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.With)
        and any(
            isinstance(item.context_expr, ast.Call)
            and isinstance(item.context_expr.func, ast.Attribute)
            and isinstance(item.context_expr.func.value, ast.Name)
            and item.context_expr.func.value.id == "torch"
            and item.context_expr.func.attr == "inference_mode"
            for item in node.items
        )
    ]
    assert inference_contexts, "LTX pipeline must match the official inference-mode entrypoint"
    context = inference_contexts[0]
    calls = {
        node.func.id
        for node in ast.walk(context)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    assert "encode_video" in calls, "lazy VAE decode must remain inside inference mode"


def test_ltx_has_pod_runner_and_split_timing_metadata() -> None:
    source = (WORKER_ROOT / "handler.py").read_text(encoding="utf-8")
    smoke = (WORKER_ROOT / "smoke.py").read_text(encoding="utf-8")
    dockerfile = (WORKER_ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert 'if __name__ == "__main__":' in source
    assert '"video_inference_seconds": video_seconds' in source
    assert '"model_load_seconds": model_load_seconds' in source
    assert '"model_download_seconds": download_seconds' in source
    assert '"upload_seconds": upload_seconds' in source
    assert '"peak_memory_mb": peak_memory_mb' in source
    assert '"gpu_name": torch.cuda.get_device_name()' in source
    assert '"event": "smoke_complete"' in smoke
    assert "COPY smoke.py /app/smoke.py" in dockerfile
    assert "COPY download_models.py /app/download_models.py" in dockerfile


def test_ltx_handler_reports_the_pod_lane_stages_in_order() -> None:
    """The control plane's stage texts already exist; the worker must emit them like H3 does."""
    source = (WORKER_ROOT / "handler.py").read_text(encoding="utf-8")
    assert '"POD_PROGRESS_CALLBACK_URL"' in source
    body = source[source.index("def handler("):]
    stages = ["gpu_probe", "model_load_start", "model_load_done", "video_start", "video_done", "upload_start", "complete"]
    positions = [body.index(f'_progress(job, "{stage}"') for stage in stages]
    assert positions == sorted(positions)
    # A broken host must fail before the 79 GB download, and both before loading.
    assert body.index("assert_gpu_healthy()") < body.index("ensure_models(job)") < body.index("_pipeline()")


def test_ltx_worker_downloads_pinned_weights_when_no_volume_supplied_them() -> None:
    """Volume-free like H3: the lock file and the downloader must agree on every pin."""
    lock = json.loads((WORKER_ROOT / "models.lock.json").read_text(encoding="utf-8"))
    download = (WORKER_ROOT / "download_models.py").read_text(encoding="utf-8")
    handler = (WORKER_ROOT / "handler.py").read_text(encoding="utf-8")
    for artifact in lock["artifacts"]:
        assert artifact["repo"] in download
        assert artifact["revision"] in download
        if artifact["path"] != "*":
            assert artifact["path"] in download
    assert "snapshot_download(" in download
    assert "huggingface_hub[cli]" not in (WORKER_ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert 'os.getenv("LTX_DOWNLOAD_ON_START", "1")' in handler
    assert 'Path("/models/PinkCherry-LTX-2.3-v1.8")' in handler
    assert 'Path("/models/PinkCherry-LTX-2.3-v1.8")' in download


def load_smoke(monkeypatch):
    """Import smoke.py with the GPU handler stubbed out."""
    import sys
    import types

    stub = types.ModuleType("handler")
    stub.handler = lambda job: {"video_url": "/generate/media/x.mp4", "job": job["id"]}
    monkeypatch.setitem(sys.modules, "handler", stub)
    spec = importlib.util.spec_from_file_location("ltx_smoke", WORKER_ROOT / "smoke.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _Response:
    def __init__(self, status_code: int, body: dict | None = None):
        self.status_code = status_code
        self._body = body

    def json(self):
        return self._body

    def raise_for_status(self):
        if self.status_code >= 400:
            import httpx

            raise httpx.HTTPStatusError(f"HTTP {self.status_code}", request=None, response=None)  # type: ignore[arg-type]


def test_ltx_smoke_pulls_jobs_while_warm_and_stops_when_retired(monkeypatch) -> None:
    smoke = load_smoke(monkeypatch)
    monkeypatch.setenv("POD_JOBS_BASE_URL", "https://host.example/generate/api/internal/pod-jobs")
    monkeypatch.setenv("RUNPOD_POD_ID", "pod-7")
    assert smoke.jobs_url() == "https://host.example/generate/api/internal/pod-jobs/pod-7/next"
    answers = [
        _Response(204),
        _Response(200, {"job": {"task_id": "t1", "input": {"prompt": "one"}, "result_url": "https://host.example/r/t1", "progress_url": "https://host.example/p/t1"}}),
        _Response(404),
    ]
    monkeypatch.setattr(smoke.httpx, "post", lambda url, headers=None, json=None, timeout=None: answers.pop(0))
    ran: list[tuple[str, str]] = []
    slept: list[float] = []
    smoke.pull_jobs(
        smoke.jobs_url(), "tok",
        lambda task_id, params: ran.append((task_id, smoke.os.environ["POD_RESULT_CALLBACK_URL"])) or True,
        sleep=slept.append,
    )
    assert ran == [("t1", "https://host.example/r/t1")]
    assert slept == [smoke.JOB_POLL_SECONDS]
    assert answers == []


def test_ltx_smoke_result_declares_whether_the_worker_pulls_jobs(monkeypatch) -> None:
    smoke = load_smoke(monkeypatch)
    monkeypatch.setenv("POD_RESULT_CALLBACK_URL", "https://host.example/r/t1")
    monkeypatch.setenv("POD_RESULT_CALLBACK_TOKEN", "tok")
    sent: list[dict] = []

    def fake_post(url, headers=None, json=None, timeout=None):
        sent.append(json)
        return _Response(200, {"ok": True})

    monkeypatch.setattr(smoke.httpx, "post", fake_post)
    monkeypatch.delenv("POD_JOBS_BASE_URL", raising=False)
    assert smoke.run_job("ltx-pod-smoke", {"prompt": "x"}) is True
    assert sent[-1]["status"] == "succeeded"
    assert sent[-1]["worker"] == {"pulls_jobs": False}
    assert sent[-1]["content"]["job"] == "ltx-pod-smoke"
    monkeypatch.setenv("POD_JOBS_BASE_URL", "https://host.example/generate/api/internal/pod-jobs")
    monkeypatch.setenv("RUNPOD_POD_ID", "pod-7")
    assert smoke._post_result({"status": "succeeded", "content": {}, "error": None})
    assert sent[-1]["worker"] == {"pulls_jobs": True}


def test_ltx_smoke_failure_callback_carries_the_worker_log_tail(monkeypatch, tmp_path) -> None:
    smoke = load_smoke(monkeypatch)
    smoke.WORKER_LOG = tmp_path / "ltx-worker.log"
    smoke.WORKER_LOG.write_text("natten: kernel launch failed\n")
    monkeypatch.setattr(smoke.time, "sleep", lambda seconds: None)
    monkeypatch.setenv("POD_RESULT_CALLBACK_URL", "https://host.example/r/t1")
    monkeypatch.setenv("POD_RESULT_CALLBACK_TOKEN", "tok")
    sent: list[dict] = []

    def fake_post(url, headers=None, json=None, timeout=None):
        sent.append(json)
        return _Response(200, {"ok": True})

    monkeypatch.setattr(smoke.httpx, "post", fake_post)

    def boom(job):
        raise RuntimeError("CUDA error: an illegal memory access was encountered")

    monkeypatch.setattr(smoke, "handler", boom)
    assert smoke.run_job("ltx-pod-smoke", {"prompt": "x"}) is False
    assert sent[-1]["status"] == "failed"
    assert "RuntimeError: CUDA error" in sent[-1]["error"]
    assert "--- worker log tail ---" in sent[-1]["error"]
    assert "natten: kernel launch failed" in sent[-1]["error"]
