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


def test_wan_token_budget_blocks_int32_overflow_combinations() -> None:
    config = load_worker_config()
    # 720p/12s (176k tokens) crossed the int32 FFN indexing limit and rendered
    # black on every attention backend; 720p/10s and all 480p durations render.
    with pytest.raises(ValueError):
        config.validate_token_budget(1280, 720, 12 * 16 + 1)
    config.validate_token_budget(1280, 720, 10 * 16 + 1)
    config.validate_token_budget(832, 480, 15 * 16 + 1)
    assert config.latent_tokens(1280, 720, 193) > config.MAX_LATENT_TOKENS
    source = (WORKER_ROOT / "handler.py").read_text(encoding="utf-8")
    assert "validate_token_budget(width, height, num_frames)" in source


def test_wan_handler_forces_both_denoiser_adapters() -> None:
    source = (WORKER_ROOT / "handler.py").read_text(encoding="utf-8")
    assert "DiffGenerator.from_pretrained" in source
    assert 'lora_names = ["adult_high", "adult_low"]' in source
    assert 'lora_targets = ["transformer", "transformer_2"]' in source
    assert "lora_strengths = [ADAPTER_STRENGTH, ADAPTER_STRENGTH]" in source
    assert 'lora_merge_mode = "merge" if LIGHTNING_ENABLED else "dynamic"' in source
    assert "merge_mode=lora_merge_mode" in source
    assert "dit_cpu_offload=False" in source
    assert 'performance_mode="speed"' in source
    assert '"torch_sdpa"' in source
    assert "text_encoder_cpu_offload=aux_cpu_offload" in source


def test_wan_image_installs_complete_video_export_backend() -> None:
    dockerfile = (WORKER_ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert "lmsysorg/sglang:v0.5.16@sha256:" in dockerfile
    assert '"imageio-ffmpeg==0.6.0"' in dockerfile
    assert '"imageio==2.37.0"' in dockerfile
    assert '"scipy==1.17.0"' in dockerfile


def test_wan_image_installs_accelerate_for_audio_cpu_offload() -> None:
    dockerfile = (WORKER_ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert '"accelerate>=1.6,<2"' in dockerfile


def test_wan_image_ships_sage_attention_for_blackwell() -> None:
    dockerfile = (WORKER_ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert (
        "SageAttention.git@eb615cf6cf4d221338033340ee2de1c37fbdba4a" in dockerfile
    )
    assert 'TORCH_CUDA_ARCH_LIST="12.0"' in dockerfile
    source = (WORKER_ROOT / "handler.py").read_text(encoding="utf-8")
    assert "import sageattention" in source


def test_wan_handler_reports_live_stage_progress_and_split_timings() -> None:
    source = (WORKER_ROOT / "handler.py").read_text(encoding="utf-8")
    assert '"POD_PROGRESS_CALLBACK_URL"' in source
    assert '_progress(job, "model_load_start")' in source
    assert '_progress(job, "audio_model_load_start")' in source
    assert '"model_load_seconds": model_load_seconds' in source
    assert '"audio_model_load_seconds": audio_model_load_seconds' in source
    assert '"upload_seconds": upload_seconds' in source
    assert '"attention_backend": os.getenv("WAN_ATTENTION_BACKEND"' in source


def test_wan_handler_generates_and_muxes_prompt_conditioned_audio() -> None:
    source = (WORKER_ROOT / "handler.py").read_text(encoding="utf-8")
    assert "AudioLDM2Pipeline" in source
    assert "_generate_audio_hidden_states" in source
    assert "pipe.generate_language_model = MethodType" in source
    assert '"cvssp/audioldm2"' in source
    assert '"-c:a",' in source
    assert '"aac",' in source
    assert '"has_audio": generate_audio' in source


def test_v1_and_v2_use_the_same_pro6000_pod_policy_under_three_dollars() -> None:
    root = Path(__file__).parents[1] / "workers"
    ltx = json.loads((root / "ltx-video" / "gpu_policy.json").read_text())
    wan = json.loads((root / "wan-video" / "gpu_policy.json").read_text())
    for policy in (ltx, wan):
        assert policy["minimum_vram_gb"] == 96
        assert policy["maximum_secure_price_usd_per_hour"] == 3.0
        assert policy["maximum_serverless_price_usd_per_hour"] == 3.0
        assert policy["observed_serverless_price_usd_per_hour"] > 3.0
        assert policy["serverless_provisioning_blocked"] is True
        assert policy["allow_fallback_gpu_types"] is False
        assert [gpu["id"] for gpu in policy["gpu_types"]] == [
            "NVIDIA RTX PRO 6000 Blackwell Server Edition"
        ]
        assert policy["gpu_types"][0]["secure_price_usd_per_hour"] < 3.0


def test_wan_handler_supports_optional_lightning_fast_profile() -> None:
    source = (WORKER_ROOT / "handler.py").read_text(encoding="utf-8")
    assert '"WAN_LIGHTNING_ENABLED"' in source
    assert '"lightning_high", "lightning_low"' in source
    assert '"WAN_FLOW_SHIFT"' in source
    assert '"lightning_enabled": LIGHTNING_ENABLED' in source
    lock = json.loads((WORKER_ROOT / "models.lock.json").read_text())
    lightning = [
        a for a in lock["artifacts"] if a["repo"] == "lightx2v/Wan2.2-Lightning"
    ]
    assert len(lightning) == 2
    assert all(a["sha256"] and a["size"] > 1_000_000_000 for a in lightning)
    assert all(
        a["revision"] == "18bccf8884ec0a078eed79785eb4ef13ea16ce1e" for a in lightning
    )


def test_wan_fp8_model_is_pinned_and_stage_timings_are_reported() -> None:
    source = (WORKER_ROOT / "handler.py").read_text(encoding="utf-8")
    script = (WORKER_ROOT / "download_models.sh").read_text(encoding="utf-8")
    lock = json.loads((WORKER_ROOT / "models.lock.json").read_text())
    assert "nvidia/Wan2.2-T2V-A14B-Diffusers-FP8" in source
    assert "2c5a06469cd2255816eb2e46b8e11600ed435d52" in script
    assert lock["expected_download_bytes"] < 60_000_000_000
    assert '"video_inference_seconds": video_seconds' in source
    assert '"audio_inference_seconds": audio_seconds' in source
    assert '_progress(job, "video_start"' in source
    assert '_progress(job, "complete"' in source


def test_wan_handler_can_be_imported_by_one_shot_pod_smoke_runner() -> None:
    source = (WORKER_ROOT / "handler.py").read_text(encoding="utf-8")
    smoke = (WORKER_ROOT / "smoke.py").read_text(encoding="utf-8")
    dockerfile = (WORKER_ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert 'if __name__ == "__main__":' in source
    assert 'from handler import handler' in smoke
    assert '"event": "smoke_complete"' in smoke
    assert '"POD_RESULT_CALLBACK_URL"' in smoke
    assert '"pod_callback_complete"' in smoke
    assert "_await_deletion" in smoke
    assert "COPY handler.py worker_config.py smoke.py download_models.sh" in dockerfile
