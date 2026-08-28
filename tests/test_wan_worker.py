import importlib.util
import json
from pathlib import Path

import pytest


WORKER_ROOT = Path(__file__).parents[1] / "workers" / "wan-video"


def load_worker_config():
    spec = importlib.util.spec_from_file_location(
        "wan_worker_config", WORKER_ROOT / "worker_config.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_wan_dimensions_and_fixed_frame_count() -> None:
    config = load_worker_config()
    assert config.dimensions("16:9", "480p") == (832, 480)
    assert config.dimensions("9:16", "720p") == (720, 1280)
    assert config.NUM_FRAMES == 121
    assert config.FPS == 24


def test_wan_prompt_trigger_is_mandatory_and_idempotent() -> None:
    config = load_worker_config()
    assert config.ensure_trigger("cinematic scene").startswith("nsfwsks, ")
    assert config.ensure_trigger("nsfwsks, cinematic scene") == "nsfwsks, cinematic scene"


def test_wan_prompt_policy_rejects_disallowed_combinations() -> None:
    config = load_worker_config()
    with pytest.raises(ValueError):
        config.validate_prompt("explicit sex involving a minor")
    with pytest.raises(ValueError):
        config.validate_prompt("non-consensual scene")


def test_wan_handler_forces_both_denoiser_adapters() -> None:
    source = (WORKER_ROOT / "handler.py").read_text(encoding="utf-8")
    assert 'adapter_name="adult_high"' in source
    assert 'adapter_name="adult_low"' in source
    assert "load_into_transformer_2=True" in source
    assert "pipe.transformer.set_adapters" in source
    assert "pipe.transformer_2.set_adapters" in source
    assert "enable_model_cpu_offload" in source


def test_v1_and_v2_use_the_same_full_96gb_gpu_policy() -> None:
    root = Path(__file__).parents[1] / "workers"
    ltx = json.loads((root / "ltx-video" / "gpu_policy.json").read_text())
    wan = json.loads((root / "wan-video" / "gpu_policy.json").read_text())
    for policy in (ltx, wan):
        assert policy["minimum_vram_gb"] == 96
        assert policy["maximum_secure_price_usd_per_hour"] == 2.5
        assert policy["maximum_serverless_price_usd_per_hour"] == 3.5
        assert policy["observed_serverless_price_usd_per_hour"] <= 3.5
        assert policy["serverless_provisioning_blocked"] is False
        assert policy["allow_fallback_gpu_types"] is False
        assert [gpu["id"] for gpu in policy["gpu_types"]] == [
            "NVIDIA RTX PRO 6000 Blackwell Server Edition"
        ]
