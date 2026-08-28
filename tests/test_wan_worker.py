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


def test_wan_dimensions_and_dynamic_frame_count() -> None:
    config = load_worker_config()
    assert config.dimensions("16:9", "480p") == (832, 480)
    assert config.dimensions("9:16", "720p") == (720, 1280)
    assert config.dimensions("1:1", "720p") == (720, 720)
    assert config.dimensions("4:3", "480p") == (640, 480)
    assert config.dimensions("3:4", "480p") == (480, 640)
    assert config.dimensions("21:9", "720p") == (1680, 720)
    assert config.frames_for_duration(4) == 65
    assert config.frames_for_duration(15) == 241
    assert config.FPS == 16
    with pytest.raises(ValueError):
        config.frames_for_duration(7)


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


def test_wan_image_installs_complete_video_export_backend() -> None:
    dockerfile = (WORKER_ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert '"transformers==5.16.1"' in dockerfile
    assert '"huggingface-hub==1.29.0"' in dockerfile
    assert '"imageio-ffmpeg==0.6.0"' in dockerfile
    assert '"imageio==2.37.0"' in dockerfile
    assert '"scipy==1.17.0"' in dockerfile


def test_wan_handler_generates_and_muxes_prompt_conditioned_audio() -> None:
    source = (WORKER_ROOT / "handler.py").read_text(encoding="utf-8")
    assert "AudioLDM2Pipeline" in source
    assert "_update_audio_model_kwargs" in source
    assert "MethodType" in source
    assert '"cvssp/audioldm2"' in source
    assert '"-c:a",' in source
    assert '"aac",' in source
    assert '"has_audio": generate_audio' in source


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
