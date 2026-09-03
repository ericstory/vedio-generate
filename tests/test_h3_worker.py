import importlib.util
import json
from pathlib import Path

import pytest


WORKER_ROOT = Path(__file__).parents[1] / "workers" / "minimax-h3"


def load_worker_config():
    spec = importlib.util.spec_from_file_location(
        "h3_worker_config", WORKER_ROOT / "worker_config.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_h3_geometry_follows_the_published_contract() -> None:
    config = load_worker_config()
    # 24 fps and the 4-15 second window are fixed by the checkpoint; SGLang
    # rejects frame interpolation and upscaling outright.
    assert config.FPS == 24
    assert config.frames_for_duration(5) == 120
    assert config.frames_for_duration(15) == 360
    assert config.short_edge_for("768p") == 768
    assert config.short_edge_for("480p") == 480
    for duration in (3, 16):
        with pytest.raises(ValueError):
            config.frames_for_duration(duration)
    with pytest.raises(ValueError):
        config.short_edge_for("1080p")
    with pytest.raises(ValueError):
        config.validate_aspect_ratio("32:9")


def test_h3_target_block_matches_the_sglang_schema() -> None:
    config = load_worker_config()
    assert config.build_target(ratio="16:9", resolution="768p", duration=5) == {
        "short_edge": 768,
        "aspect_ratio": "16:9",
        "duration_seconds": 5.0,
    }


def test_h3_canvas_respects_the_pixel_budget() -> None:
    config = load_worker_config()
    assert config.expected_canvas("1:1", "768p") == (768, 768)
    # 1344x768 is the canvas every published H3 benchmark uses for 16:9.
    assert config.expected_canvas("16:9", "768p") == (1344, 768)
    assert config.expected_canvas("9:16", "768p") == (768, 1344)
    # 21:9 would want a 1792 long edge; the area cap holds it at 1344 instead of
    # shrinking the short edge the caller asked for.
    assert config.expected_canvas("21:9", "768p") == (1344, 768)
    for ratio in config.SUPPORTED_ASPECT_RATIOS:
        width, height = config.expected_canvas(ratio, "768p")
        assert width * height <= config.MAX_PIXELS
        assert width % 16 == 0 and height % 16 == 0
    assert config.is_verified_configuration("768p") is True
    assert config.is_verified_configuration("480p") is False


def test_h3_runtime_budget_guard_stays_off_until_calibrated() -> None:
    config = load_worker_config()
    # Nothing has timed H3 on the production GPU yet, so an uncalibrated guard
    # must admit every job instead of inventing a projection.
    assert (
        config.validate_runtime_budget(
            ratio="16:9",
            resolution="768p",
            duration=15,
            steps=50,
            seconds_per_megapixel_step=0,
            budget_seconds=1500,
        )
        is None
    )
    # Once calibrated it rejects before any GPU time is billed.
    with pytest.raises(ValueError, match="exceeds the"):
        config.validate_runtime_budget(
            ratio="16:9",
            resolution="768p",
            duration=15,
            steps=50,
            seconds_per_megapixel_step=0.01,
            budget_seconds=60,
        )
    assert (
        config.validate_runtime_budget(
            ratio="16:9",
            resolution="768p",
            duration=4,
            steps=8,
            seconds_per_megapixel_step=0.0001,
            budget_seconds=1500,
        )
        is not None
    )


def test_h3_trigger_is_optional_and_idempotent() -> None:
    config = load_worker_config()
    assert config.ensure_trigger("cinematic scene", "") == "cinematic scene"
    assert config.ensure_trigger("cinematic scene", "pnkchry").startswith("pnkchry, ")
    assert config.ensure_trigger("pnkchry, scene", "pnkchry") == "pnkchry, scene"


def test_private_adult_research_prompt_is_allowed() -> None:
    config = load_worker_config()
    config.validate_prompt(
        "Consensual erotic scene between two fictional adults, cinematic"
    )


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


def test_h3_handler_uses_native_audio_and_no_comfyui() -> None:
    source = (WORKER_ROOT / "handler.py").read_text(encoding="utf-8")
    # H3 muxes 32 kHz stereo AAC in the same forward pass, so this lane must not
    # grow Wan's second audio model, its mux step or a ComfyUI dependency.
    assert "AudioLDM2" not in source
    assert "import comfy" not in source
    assert '"-c:a"' not in source
    assert "from sglang.multimodal_gen import DiffGenerator" in source
    assert '"task": "t2va"' in source
    assert '"audio_flow_shift": AUDIO_FLOW_SHIFT' in source
    # The distilled checkpoint has one positive branch: no CFG, no negative
    # prompt. Sending either is rejected by the pipeline.
    assert "negative_prompt" not in source
    assert "guidance_scale" not in source
    # PinkCherry is a full fine-tuned DiT, loaded through the single-file
    # transformer override rather than as an adapter.
    assert "transformer_weights_path" in source
    # Nothing in the image may pull a ComfyUI runtime in.
    dockerfile = (WORKER_ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert "comfy" not in dockerfile.lower()


def test_h3_gpu_policy_spans_every_architecture_the_image_supports() -> None:
    policy = json.loads((WORKER_ROOT / "gpu_policy.json").read_text(encoding="utf-8"))
    dockerfile = (WORKER_ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert policy["maximum_secure_price_usd_per_hour"] == 3.0
    assert policy["serverless_provisioning_blocked"] is True
    assert policy["allow_fallback_gpu_types"] is True
    gpus = policy["gpu_types"]
    # Preference order must be explicit and dense: the provider walks it in
    # order, so a gap or a tie would make placement non-deterministic.
    assert [g["priority"] for g in gpus] == list(range(1, len(gpus) + 1))
    assert all(g["secure_price_usd_per_hour"] < 3.0 for g in gpus)
    assert all(g["vram_gb"] >= policy["minimum_vram_gb"] for g in gpus)
    # Every listed card must have kernels in the image, or the Pod boots into a
    # silent attention fallback rather than the backend we measured.
    built = {
        arch.strip()
        for arch in dockerfile.split('TORCH_CUDA_ARCH_LIST="')[1].split('"')[0].split(";")
    }
    # Only the arch list that has actually built. Widening it is a change to make
    # deliberately, with the policy updated in the same commit.
    assert built == {"12.0"}
    assert {g["compute_capability"] for g in gpus} <= built
    # Online FP8 needs SM >= 8.9, which rules out the A100 generation.
    assert all(float(g["compute_capability"]) >= 8.9 for g in gpus)
    assert policy["minimum_vram_gb"] == 32


def test_h3_residency_profile_tracks_the_card_it_lands_on() -> None:
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "h3_handler_probe", WORKER_ROOT / "handler.py"
    )
    assert spec and spec.loader
    source = (WORKER_ROOT / "handler.py").read_text(encoding="utf-8")
    namespace: dict = {}
    # Executing the profile function alone avoids importing torch/sglang here.
    start = source.index("def residency_profile")
    end = source.index("def _generator")
    exec("from typing import Any\n" + source[start:end], namespace)
    profile = namespace["residency_profile"]

    resident = profile(96.0)
    assert resident["performance_mode"] == "speed"
    assert resident["dit_layerwise_offload"] is False

    mid = profile(48.0)
    assert mid["performance_mode"] == "memory"
    assert mid["layerwise_offload_components"] == ["dit", "text_encoder"]

    small = profile(32.0)
    assert small["performance_mode"] == "memory"
    assert small["layerwise_offload_components"] == ["dit", "text_encoder", "vae"]
    assert small["dit_layerwise_resident_layers"] < mid["dit_layerwise_resident_layers"]

    # The 32B text encoder never fits beside the DiT in BF16, on any tier.
    for vram in (96.0, 48.0, 32.0, 24.0):
        assert profile(vram)["text_encoder_cpu_offload"] is True


def test_h3_model_lock_pins_loadable_weight_formats() -> None:
    lock = json.loads((WORKER_ROOT / "models.lock.json").read_text(encoding="utf-8"))
    by_path = {artifact["path"]: artifact for artifact in lock["artifacts"]}
    pinkcherry = by_path[
        "beta-0.6-fl2va/PinkCherry_fl2va_MiniMax_H3_bf16_beta-0.6.safetensors"
    ]
    assert pinkcherry["repo"] == "SexGod1979/PinkCherry_MiniMax-H3"
    assert pinkcherry["sha256"] == (
        "16f1950cc83bd686106d49588c8611281fbb5e9ae46f8cd1ae7945fd4e00357d"
    )
    turbo = by_path["minimax_h3_fl2v_turbo_8step_v1.0_768p_bf16.safetensors"]
    assert turbo["repo"] == "lightx2v/Minimax-h3-Turbo"
    # The ComfyUI-quantized PinkCherry exports and the _comfyui_ turbo twins use
    # key conventions SGLang's loader does not implement, so they must never be
    # pinned here.
    assert not any(
        "int8_convrot" in artifact["path"] or "comfyui" in artifact["path"]
        for artifact in lock["artifacts"]
    )
    # The default layout drops the stock DiT shards PinkCherry replaces.
    optional = {a["path"] for a in lock["artifacts"] if a.get("optional")}
    assert "FL2VA/transformer/*.safetensors" in optional
    required = sum(
        artifact["size"] for artifact in lock["artifacts"] if not artifact.get("optional")
    )
    assert lock["expected_download_bytes"] == required
    assert required < lock["recommended_network_volume_gb"] * 1_000_000_000


def test_h3_download_skips_the_replaced_transformer_by_default() -> None:
    source = (WORKER_ROOT / "download_models.py").read_text(encoding="utf-8")
    assert 'INCLUDE_STOCK_TRANSFORMER", "0"' in source
    assert 'ignore.append("FL2VA/transformer/*.safetensors")' in source
    assert '"Ref2VA/*"' in source
    assert "minimax_h3_fl2v_turbo_8step_v1.0_768p_bf16.safetensors" in source


def test_h3_image_does_not_disturb_the_base_huggingface_hub() -> None:
    """Upgrading it broke the SageAttention build; the download path must not need to.

    Installing huggingface_hub[cli] for the `hf` command pulled in a release whose
    strict dataclass validator raises on a `str | None` annotation while
    SageAttention prepares its metadata, failing the image build eight seconds in.
    """
    dockerfile = (WORKER_ROOT / "Dockerfile").read_text(encoding="utf-8")
    install_lines = [
        line for line in dockerfile.splitlines()
        if "huggingface_hub" in line and not line.strip().startswith("#")
    ]
    assert install_lines == []
    source = (WORKER_ROOT / "download_models.py").read_text(encoding="utf-8")
    # The Python API ships with SGLang's own dependency; the CLI does not.
    assert "from huggingface_hub import snapshot_download" in source
    assert "hf download" not in source


def test_h3_handler_hands_sglang_the_snapshot_root_not_the_partition() -> None:
    """SGLang appends the FL2VA partition itself; pointing model_path at it doubled it.

    MiniMaxH3Pipeline (SGLang v0.5.18) forces model_subfolder from model_variant and
    joins it onto model_path, so the first real run died inside the spawned scheduler
    with `.../FL2VA/FL2VA does not contain model_index.json` and surfaced only as a
    bare EOFError. The repository's root model_index.json names the diffusers Modular
    class SGLang has no entry for, so the native pipeline class is named explicitly.
    """
    source = (WORKER_ROOT / "handler.py").read_text(encoding="utf-8")
    assert "PIPELINE_ROOT = BASE_MODEL_ROOT.parent" in source
    assert '"model_path": str(PIPELINE_ROOT)' in source
    assert '"model_path": str(BASE_MODEL_ROOT)' not in source
    assert '"pipeline_class_name": PIPELINE_CLASS_NAME' in source
    assert 'PIPELINE_CLASS_NAME = "MiniMaxH3Pipeline"' in source
    assert '"model_variant": MODEL_VARIANT' in source
    # The partition directory name is part of SGLang's contract, so a misconfigured
    # H3_BASE_MODEL_ROOT fails loudly before any weights are touched.
    assert 'BASE_MODEL_ROOT.name != "FL2VA"' in source


def test_h3_smoke_failure_callback_carries_the_worker_log_tail() -> None:
    """A dead scheduler child must not cost a second Pod just to read its traceback."""
    source = (WORKER_ROOT / "smoke.py").read_text(encoding="utf-8")
    assert '["tee", "-a", str(WORKER_LOG)]' in source
    assert "os.dup2(tee.stdin.fileno(), 1)" in source
    assert "os.dup2(tee.stdin.fileno(), 2)" in source
    assert "--- worker log tail ---" in source
    # Tee is wired up before the handler runs, so the child's output is captured.
    assert source.index("_tee_process_output()\n") < source.index('handler({"id": "h3-pod-smoke"')


def test_h3_handler_trusts_the_pinned_snapshot_code_and_takes_template_overrides() -> None:
    """The FL2VA VAEs and encoder are custom-code modules; load-time knobs must not need a rebuild."""
    source = (WORKER_ROOT / "handler.py").read_text(encoding="utf-8")
    assert '"trust_remote_code": True' in source
    assert 'os.getenv("H3_EXTRA_SERVER_ARGS_JSON", "")' in source
    # Overrides land after every built-in kwarg so a template can win any argument.
    assert source.index("kwargs.update(overrides)") < source.index("DiffGenerator.from_pretrained(**kwargs)")


def test_h3_worker_probes_the_gpu_before_downloading_anything() -> None:
    """A broken RunPod host must cost seconds, not a 16-minute download plus the Pod."""
    source = (WORKER_ROOT / "handler.py").read_text(encoding="utf-8")
    assert "def assert_gpu_healthy" in source
    assert "torch.cuda.init()" in source
    assert '"GPU host unhealthy:' in source
    body = source[source.index("def handler("):]
    assert body.index("assert_gpu_healthy()") < body.index("ensure_models(job)")


def test_h3_snapshot_root_mirrors_the_hf_repo_id() -> None:
    """SGLang selects the native H3 pipeline config by the directory's short name.

    With any other basename get_model_info falls back to the generic diffusers
    config, and pipeline assembly dies on `no attribute 'audio_vae_config'`.
    """
    handler = (WORKER_ROOT / "handler.py").read_text(encoding="utf-8")
    download = (WORKER_ROOT / "download_models.py").read_text(encoding="utf-8")
    assert '"/models/MiniMaxAI/MiniMax-H3"' in handler
    assert 'Path("/models/MiniMaxAI/MiniMax-H3")' in download
    assert "MiniMax-H3-PinkCherry" not in handler
    assert "MiniMax-H3-PinkCherry" not in download
    assert 'PIPELINE_ROOT.name.lower() != "minimax-h3"' in handler


def test_h3_only_the_dit_runs_on_the_fast_attention_backend() -> None:
    """SGLang's Qwen3-VL encoder and H3 audio VAE reject sage_attn outright."""
    source = (WORKER_ROOT / "handler.py").read_text(encoding="utf-8")
    block = source[source.index('"component_attention_backends"'):]
    block = block[: block.index("}")]
    for component in ("text_encoder", "audio_vae", "video_vae"):
        assert f'"{component}": "torch_sdpa"' in block
    assert '"transformer"' not in block
