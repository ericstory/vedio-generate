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
    assert policy["minimum_vram_gb"] == 48
    assert policy["maximum_secure_price_usd_per_hour"] == 3.0
    assert policy["maximum_serverless_price_usd_per_hour"] == 3.0
    assert policy["allow_fallback_gpu_types"] is False
    assert [gpu["id"] for gpu in policy["gpu_types"]] == [
        "NVIDIA L40"
    ]
    assert all(gpu["vram_gb"] == 48 for gpu in policy["gpu_types"])
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
