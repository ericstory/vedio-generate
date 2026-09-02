from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any
from uuid import uuid4

import boto3
import httpx
import runpod
import torch
from sglang.multimodal_gen import DiffGenerator

from worker_config import (
    FPS,
    build_target,
    ensure_trigger,
    expected_canvas,
    frames_for_duration,
    is_verified_configuration,
    short_edge_for,
    validate_prompt,
    validate_runtime_budget,
)


MODEL_ROOT = Path(os.getenv("MODEL_ROOT", "/runpod-volume/models/MiniMax-H3-PinkCherry"))
# The stock MiniMax FL2VA partition supplies the text encoder, both VAEs, the
# tokenizer/processor and every config. FL2VA serves t2va as well as first- and
# last-frame conditioning, so one partition covers the whole product surface.
BASE_MODEL_ROOT = Path(os.getenv("H3_BASE_MODEL_ROOT", str(MODEL_ROOT / "FL2VA")))
MODEL_VARIANT = os.getenv("H3_MODEL_VARIANT", "fl2va")
# PinkCherry ships a full fine-tuned DiT, not an adapter. Its tensor names and
# shapes are identical to the stock FL2VA transformer, so SGLang loads it
# through the single-file transformer override and every other component stays
# on the official weights.
NSFW_TRANSFORMER = Path(
    os.getenv(
        "H3_NSFW_TRANSFORMER_PATH",
        str(
            MODEL_ROOT
            / "pinkcherry"
            / "PinkCherry_fl2va_MiniMax_H3_bf16_beta-0.6.safetensors"
        ),
    )
)
# Empty disables the override and runs the stock MiniMax transformer, which is
# what the quality A/B needs on the other side.
NSFW_TRANSFORMER_ENABLED = os.getenv("H3_NSFW_TRANSFORMER_ENABLED", "1") == "1"
TURBO_LORA = Path(
    os.getenv(
        "H3_TURBO_LORA_PATH",
        str(
            MODEL_ROOT
            / "turbo-lora"
            / "minimax_h3_fl2v_turbo_8step_v1.0_768p_bf16.safetensors"
        ),
    )
)
TURBO_LORA_ENABLED = os.getenv("H3_TURBO_LORA_ENABLED", "1") == "1"
TURBO_LORA_STRENGTH = float(os.getenv("H3_TURBO_LORA_STRENGTH", "1.0"))
# lightx2v ships the training alpha in safetensors metadata for the plain
# (non-ComfyUI) exports, so this override normally stays unset.
TURBO_LORA_ALPHA = os.getenv("H3_TURBO_LORA_ALPHA", "").strip()
# Online post-load FP8 is the only quantization SGLang can derive from a BF16
# checkpoint without a pre-quantized export, and SM12.x has native FP8 tensor
# cores. Clear the variable to run the checkpoint at BF16 instead.
QUANTIZATION = os.getenv("H3_QUANTIZATION", "fp8").strip()
ATTENTION_BACKEND = os.getenv("H3_ATTENTION_BACKEND", "sage_attn").strip()
# Every published turbo recipe requests one more step than the LoRA's name: the
# 4-step export is driven at 5 and the balanced profile at 9, because the flow
# schedule needs N+1 sigmas for N denoise intervals. So the 8-step LoRA runs at 9.
INFERENCE_STEPS = int(os.getenv("H3_INFERENCE_STEPS", "9"))
FLOW_SHIFT = float(os.getenv("H3_FLOW_SHIFT", "12.0"))
AUDIO_FLOW_SHIFT = float(os.getenv("H3_AUDIO_FLOW_SHIFT", "3.0"))
# "lossless" is the pipeline default; "high" enables the audited Cache-DiT
# profile (1.40x, SSIM 0.931 against lossless on MiniMax's own workload).
QUALITY = os.getenv("H3_QUALITY", "lossless").strip()
# No measured H3 timing exists on this GPU yet, so the pod-timeout projection
# stays disabled until a real run supplies the constant.
SECONDS_PER_MPIXEL_STEP = float(os.getenv("H3_SECONDS_PER_MPIXEL_STEP", "0"))
DENOISE_BUDGET_SECONDS = float(os.getenv("H3_DENOISE_BUDGET_SECONDS", "1500"))

_GENERATOR: DiffGenerator | None = None


def _progress(job: dict[str, Any], stage: str, **details: Any) -> None:
    """Emit stage timing without logging the user's prompt."""
    payload = {"stage": stage, **details}
    print(json.dumps(payload, ensure_ascii=False), flush=True)
    progress_update = getattr(runpod.serverless, "progress_update", None)
    # A one-shot GPU Pod has no Serverless heartbeat callback. Calling the SDK
    # there only spawns a thread that retries the literal JOB_DONE_URL.
    if os.getenv("RUNPOD_WEBHOOK_PING") and callable(progress_update):
        progress_update(job, payload)
    # Best-effort live stage reporting to the control plane. Generation must
    # never fail or stall because the progress channel is down.
    progress_url = os.getenv("POD_PROGRESS_CALLBACK_URL", "").strip()
    token = os.getenv("POD_RESULT_CALLBACK_TOKEN", "")
    if progress_url and token:
        try:
            httpx.post(
                progress_url,
                headers={"Authorization": f"Bearer {token}"},
                json=payload,
                timeout=5,
            )
        except httpx.HTTPError:
            pass


DOWNLOAD_MARKER = MODEL_ROOT / ".download-complete"
DOWNLOAD_SCRIPT = Path(__file__).with_name("download_models.py")


def _required_model_files() -> list[Path]:
    required = [BASE_MODEL_ROOT / "model_index.json"]
    if NSFW_TRANSFORMER_ENABLED:
        required.append(NSFW_TRANSFORMER)
    if TURBO_LORA_ENABLED:
        required.append(TURBO_LORA)
    return required


def ensure_models(job: dict[str, Any] | None = None) -> float:
    """Fetch the weights to container disk when no network volume supplied them.

    A regional volume pins the Pod to the one data centre that holds it, and
    that pin is what made this lane unschedulable: every card we can run was
    available somewhere, just never in those two data centres. Pulling ~145 GB
    at start costs a few minutes and buys placement anywhere. It is also not
    obviously slower -- Wan's volume loads were measured anywhere from 90 to 805
    seconds, while this download measured about 5 minutes.
    """
    if all(path.is_file() for path in _required_model_files()):
        return 0.0
    if os.getenv("H3_DOWNLOAD_ON_START", "1") != "1":
        missing = [str(p) for p in _required_model_files() if not p.is_file()]
        raise RuntimeError(
            "MiniMax H3 weights are missing and start-up download is disabled: "
            + ", ".join(missing)
        )
    if job is not None:
        _progress(job, "model_download_start")
    started = time.monotonic()
    env = {**os.environ, "MODEL_ROOT": str(MODEL_ROOT)}
    # An anonymous pull is rate limited by Hugging Face, and every cold start
    # repeats it, so the token is worth having even for public repositories.
    subprocess.run([sys.executable, str(DOWNLOAD_SCRIPT)], check=True, env=env)
    DOWNLOAD_MARKER.touch()
    seconds = round(time.monotonic() - started, 3)
    if job is not None:
        _progress(job, "model_download_done", seconds=seconds)
    return seconds


def _require_models() -> None:
    missing = [str(path) for path in _required_model_files() if not path.is_file()]
    if missing:
        raise RuntimeError(
            "MiniMax H3 requires the FL2VA partition and every enabled weight "
            "override: " + ", ".join(missing)
        )


def residency_profile(total_vram_gb: float) -> dict[str, Any]:
    """Pick a residency plan for whatever GPU this Pod actually landed on.

    The 96 GB requirement this lane inherited came from Wan, which peaked at
    91.3 GB. H3 is nowhere near that: MiniMax and SGLang publish single-GPU
    recipes down to a 24 GB RTX 4090, trading resident weights for layerwise
    offload. Adapting here is what lets the provider accept a wider GPU list
    instead of queueing behind one sold-out model.

    The 96 GB plan is the one this repository intends to run and measure. The
    smaller plans mirror the published SGLang consumer recipes and are not
    something we have timed ourselves.
    """
    if total_vram_gb >= 80:
        # Resident DiT. The 32B text encoder never fits alongside it in BF16,
        # so it streams from host memory on every tier.
        return {
            "performance_mode": "speed",
            "dit_cpu_offload": False,
            "dit_layerwise_offload": False,
            "text_encoder_cpu_offload": True,
            "vae_cpu_offload": False,
        }
    if total_vram_gb >= 40:
        # 48 GB class: the quantized DiT fits but leaves little room for 768p
        # activations, so keep most layers resident and stream the tail.
        return {
            "performance_mode": "memory",
            "layerwise_offload_components": ["dit", "text_encoder"],
            "dit_offload_prefetch_size": 1,
            "dit_layerwise_resident_layers": 30,
            "text_encoder_cpu_offload": True,
            "vae_cpu_offload": False,
        }
    # 32 GB class and below: follow the shape of the published 1x RTX 4090
    # recipe, with a few more resident layers than a 24 GB card can afford.
    return {
        "performance_mode": "memory",
        "layerwise_offload_components": ["dit", "text_encoder", "vae"],
        "dit_offload_prefetch_size": 1,
        "dit_layerwise_resident_layers": 8,
        "text_encoder_cpu_offload": True,
        "vae_cpu_offload": True,
    }


def _generator() -> DiffGenerator:
    """Load H3 with a residency plan matched to this Pod's GPU."""
    global _GENERATOR
    if _GENERATOR is None:
        _require_models()
        if ATTENTION_BACKEND == "sage_attn":
            # SGLang silently falls back to a different backend when
            # sageattention is missing, and that fallback is unreliable on
            # SM12.x. Surface a provisioning error instead of quietly
            # benchmarking the wrong kernel.
            import sageattention  # noqa: F401

        total_vram_gb = torch.cuda.get_device_properties(0).total_memory / (1024**3)
        profile = residency_profile(total_vram_gb)
        override = os.getenv("H3_PERFORMANCE_MODE", "").strip()
        if override:
            profile["performance_mode"] = override
        print(
            json.dumps(
                {
                    "stage": "residency_profile",
                    "gpu": torch.cuda.get_device_name(),
                    "total_vram_gb": round(total_vram_gb, 1),
                    "profile": {k: v for k, v in profile.items()},
                }
            ),
            flush=True,
        )
        kwargs: dict[str, Any] = {
            "model_path": str(BASE_MODEL_ROOT),
            "model_variant": MODEL_VARIANT,
            "num_gpus": 1,
            "attention_backend": ATTENTION_BACKEND,
            "pin_cpu_memory": True,
            "enable_torch_compile": False,
            "warmup_mode": "off",
            **profile,
        }
        if QUANTIZATION:
            kwargs["quantization"] = QUANTIZATION
        if NSFW_TRANSFORMER_ENABLED:
            kwargs["transformer_weights_path"] = str(NSFW_TRANSFORMER)
        generator = DiffGenerator.from_pretrained(**kwargs)
        if TURBO_LORA_ENABLED:
            lora_kwargs: dict[str, Any] = {
                "lora_nickname": "h3_turbo",
                "lora_path": str(TURBO_LORA),
                # "all", not "transformer": the turbo export also carries
                # token_refiner.refiner_blocks.* tensors, and narrowing the
                # target would silently drop them. This is also the default the
                # published recipes rely on.
                "target": os.getenv("H3_LORA_TARGET", "all"),
                "strength": TURBO_LORA_STRENGTH,
                "merge_mode": os.getenv("H3_LORA_MERGE_MODE", "auto"),
            }
            if TURBO_LORA_ALPHA:
                lora_kwargs["lora_alpha"] = int(TURBO_LORA_ALPHA)
            generator.set_lora(**lora_kwargs)
        _GENERATOR = generator
    return _GENERATOR


def _strip_audio(source: Path, destination: Path) -> None:
    """Drop H3's native soundtrack when the request opted out of audio."""
    subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-i",
            str(source),
            "-map",
            "0:v:0",
            "-c:v",
            "copy",
            "-an",
            "-movflags",
            "+faststart",
            str(destination),
        ],
        check=True,
    )


def _upload(path: Path, key: str) -> str:
    upload_url = os.getenv("VIDEO_UPLOAD_URL", "").strip()
    if upload_url:
        token = os.environ["VIDEO_UPLOAD_TOKEN"]
        with path.open("rb") as stream:
            response = httpx.post(
                upload_url,
                headers={"Authorization": f"Bearer {token}"},
                files={"video": (path.name, stream, "video/mp4")},
                timeout=float(os.getenv("VIDEO_UPLOAD_TIMEOUT_SECONDS", "300")),
            )
        response.raise_for_status()
        video_url = str(response.json().get("video_url") or "")
        if not video_url:
            raise RuntimeError("video upload response did not contain video_url")
        return video_url

    bucket = os.environ["S3_BUCKET"]
    client = boto3.client(
        "s3",
        endpoint_url=os.getenv("S3_ENDPOINT_URL") or None,
        region_name=os.getenv("S3_REGION", "auto"),
    )
    client.upload_file(str(path), bucket, key, ExtraArgs={"ContentType": "video/mp4"})
    public_base = os.getenv("S3_PUBLIC_BASE_URL", "").rstrip("/")
    if public_base:
        return f"{public_base}/{key}"
    return client.generate_presigned_url(
        "get_object",
        Params={"Bucket": bucket, "Key": key},
        ExpiresIn=int(os.getenv("OUTPUT_URL_TTL_SECONDS", "604800")),
    )


def _validate_locked_checkpoint(params: dict[str, Any]) -> None:
    expected_id = os.getenv("H3_NSFW_MODEL_ID", "SexGod1979/PinkCherry_MiniMax-H3")
    expected_version = os.getenv(
        "H3_NSFW_MODEL_VERSION", "bf2fef11d0e55e957f4af997e3beade3362f44b3"
    )
    if params.get("adult_model_id") not in {None, "", expected_id}:
        raise ValueError("adult model id does not match the locked worker configuration")
    if params.get("adult_model_version") not in {None, "", expected_version}:
        raise ValueError(
            "adult model version does not match the locked worker configuration"
        )


def handler(job: dict[str, Any]) -> dict[str, Any]:
    params = job.get("input") or {}
    prompt = str(params.get("prompt") or "").strip()
    validate_prompt(prompt)
    _validate_locked_checkpoint(params)
    duration = int(params.get("duration", 5))
    ratio = str(params.get("ratio") or "16:9")
    resolution = str(params.get("resolution") or "768p")
    target = build_target(ratio=ratio, resolution=resolution, duration=duration)
    steps = int(params.get("steps") or INFERENCE_STEPS)
    projected_denoise_seconds = validate_runtime_budget(
        ratio=ratio,
        resolution=resolution,
        duration=duration,
        steps=steps,
        seconds_per_megapixel_step=SECONDS_PER_MPIXEL_STEP,
        budget_seconds=DENOISE_BUDGET_SECONDS,
    )
    generate_audio = bool(params.get("generate_audio", True))
    seed = int(params.get("seed", -1))
    if seed < 0:
        seed = int.from_bytes(os.urandom(4), "big")
    prompt = ensure_trigger(prompt, os.getenv("H3_TRIGGER", ""))

    with tempfile.TemporaryDirectory(prefix="h3-job-") as directory:
        rendered = Path(directory) / "render.mp4"
        output = Path(directory) / "output.mp4"
        started = time.monotonic()
        download_seconds = ensure_models(job)
        _progress(job, "model_load_start")
        load_started = time.monotonic()
        generator = _generator()
        model_load_seconds = round(time.monotonic() - load_started, 3)
        _progress(job, "model_load_done", seconds=model_load_seconds)
        width, height = expected_canvas(ratio, resolution)
        _progress(
            job,
            "video_start",
            width=width,
            height=height,
            frames=frames_for_duration(duration),
            steps=steps,
        )
        video_started = time.monotonic()
        sampling_params_kwargs: dict[str, Any] = {
            "prompt": prompt,
            # H3 serves only the CFG-distilled single-positive branch: no
            # negative prompt, no guidance scale, no audio guidance scale.
            "task": "t2va",
            "conditions": [],
            "target": target,
            "num_inference_steps": steps,
            "flow_shift": FLOW_SHIFT,
            "audio_flow_shift": AUDIO_FLOW_SHIFT,
            "seed": seed,
            "save_output": True,
            "return_file_paths_only": True,
            "output_path": directory,
            "output_file_name": rendered.name,
        }
        if QUALITY and QUALITY != "lossless":
            sampling_params_kwargs["quality"] = QUALITY
        generation = generator.generate(sampling_params_kwargs=sampling_params_kwargs)
        if generation is None or not generation.output_file_path:
            raise RuntimeError("SGLang returned no MiniMax H3 output")
        rendered = Path(generation.output_file_path)
        if not rendered.is_file():
            raise RuntimeError(f"SGLang output is missing: {rendered}")
        video_seconds = round(time.monotonic() - video_started, 3)
        _progress(job, "video_done", seconds=video_seconds)
        # H3 muxes 32 kHz stereo AAC in the same pass, so there is no separate
        # audio model, no second inference and nothing to mux back together.
        if generate_audio:
            rendered.replace(output)
        else:
            _strip_audio(rendered, output)
        inference_seconds = round(time.monotonic() - started, 3)
        key = f"videos/{job.get('id') or uuid4()}.mp4"
        _progress(job, "upload_start")
        upload_started = time.monotonic()
        video_url = _upload(output, key)
        upload_seconds = round(time.monotonic() - upload_started, 3)
        _progress(job, "complete", seconds=round(time.monotonic() - started, 3))
        return {
            "video_url": video_url,
            "seed": seed,
            "model_id": os.getenv("H3_MODEL_ID", "MiniMaxAI/MiniMax-H3"),
            "model_version": os.getenv(
                "H3_MODEL_VERSION", "42ed227ee7df40d41602854ae760620d6eb651fe"
            ),
            "model_variant": MODEL_VARIANT,
            "workflow_version": os.getenv(
                "H3_WORKFLOW_VERSION", "h3-fl2va-pinkcherry-turbo8-v1"
            ),
            "adult_model_id": (
                os.getenv("H3_NSFW_MODEL_ID", "SexGod1979/PinkCherry_MiniMax-H3")
                if NSFW_TRANSFORMER_ENABLED
                else None
            ),
            "adult_model_version": (
                os.getenv(
                    "H3_NSFW_MODEL_VERSION",
                    "bf2fef11d0e55e957f4af997e3beade3362f44b3",
                )
                if NSFW_TRANSFORMER_ENABLED
                else None
            ),
            "turbo_lora_id": (
                os.getenv("H3_TURBO_LORA_ID", "lightx2v/Minimax-h3-Turbo")
                if TURBO_LORA_ENABLED
                else None
            ),
            "turbo_lora_strength": TURBO_LORA_STRENGTH if TURBO_LORA_ENABLED else None,
            "gpu_name": torch.cuda.get_device_name(),
            "gpu_total_vram_gb": round(
                torch.cuda.get_device_properties(0).total_memory / (1024**3), 1
            ),
            "performance_mode": residency_profile(
                torch.cuda.get_device_properties(0).total_memory / (1024**3)
            )["performance_mode"],
            "engine": "sglang",
            "engine_version": os.getenv("H3_SGLANG_VERSION", "0.5.18"),
            "quantization": QUANTIZATION or "bf16",
            "attention_backend": ATTENTION_BACKEND,
            "quality": QUALITY,
            "inference_seconds": inference_seconds,
            "video_inference_seconds": video_seconds,
            "model_load_seconds": model_load_seconds,
            "model_download_seconds": download_seconds,
            "upload_seconds": upload_seconds,
            "projected_denoise_seconds": projected_denoise_seconds,
            "inference_steps": steps,
            "flow_shift": FLOW_SHIFT,
            "audio_flow_shift": AUDIO_FLOW_SHIFT,
            "peak_memory_mb": generation.peak_memory_mb,
            "duration": duration,
            "fps": FPS,
            "frame_count": frames_for_duration(duration),
            "short_edge": short_edge_for(resolution),
            "width": width,
            "height": height,
            "verified_configuration": is_verified_configuration(resolution),
            "has_audio": generate_audio,
            "audio_model_id": "MiniMaxAI/MiniMax-H3" if generate_audio else None,
            "audio_sample_rate": 32000 if generate_audio else None,
        }


if __name__ == "__main__":
    if os.getenv("EAGER_LOAD_MODELS", "1") == "1":
        _generator()
    runpod.serverless.start({"handler": handler})
