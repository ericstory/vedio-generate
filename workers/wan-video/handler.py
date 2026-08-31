from __future__ import annotations

import os
import json
from pathlib import Path
import subprocess
import tempfile
import time
from types import MethodType
from typing import Any
from uuid import uuid4

import boto3
import httpx
import numpy as np
import runpod
import torch
from diffusers import AudioLDM2Pipeline
from scipy.io import wavfile
from sglang.multimodal_gen import DiffGenerator

from worker_config import FPS, dimensions, ensure_trigger, frames_for_duration, validate_prompt


MODEL_ROOT = Path(
    os.getenv("MODEL_ROOT", "/runpod-volume/models/Wan2.2-T2V-A14B-Adult-FP8-v4")
)
BASE_MODEL_ROOT = Path(os.getenv("WAN_BASE_MODEL_ROOT", str(MODEL_ROOT / "base")))
ADAPTER_ROOT = Path(os.getenv("WAN_ADAPTER_ROOT", str(MODEL_ROOT / "adult-lora")))
ADAPTER_HIGH = Path(
    os.getenv("WAN_ADAPTER_HIGH_PATH", str(ADAPTER_ROOT / "NSFW-22-H-e8.safetensors"))
)
ADAPTER_LOW = Path(
    os.getenv("WAN_ADAPTER_LOW_PATH", str(ADAPTER_ROOT / "NSFW-22-L-e8.safetensors"))
)
ADAPTER_STRENGTH = float(os.getenv("WAN_ADULT_ADAPTER_STRENGTH", "0.9"))
# Optional lightx2v distillation LoRA pair. When enabled it stacks on top of
# the mandatory adult LoRAs (one pair per Wan 2.2 expert) and the template
# drops to few-step CFG-free sampling for the fast profile.
LIGHTNING_ENABLED = os.getenv("WAN_LIGHTNING_ENABLED", "0") == "1"
LIGHTNING_ROOT = Path(
    os.getenv("WAN_LIGHTNING_ROOT", str(MODEL_ROOT / "lightning-lora"))
)
LIGHTNING_HIGH = Path(
    os.getenv("WAN_LIGHTNING_HIGH_PATH", str(LIGHTNING_ROOT / "high_noise_model.safetensors"))
)
LIGHTNING_LOW = Path(
    os.getenv("WAN_LIGHTNING_LOW_PATH", str(LIGHTNING_ROOT / "low_noise_model.safetensors"))
)
LIGHTNING_STRENGTH = float(os.getenv("WAN_LIGHTNING_STRENGTH", "1.0"))
AUDIO_MODEL_ROOT = Path(
    os.getenv("WAN_AUDIO_MODEL_ROOT", str(MODEL_ROOT / "audio" / "audioldm2"))
)
_GENERATOR: DiffGenerator | None = None
_AUDIO_PIPELINE: AudioLDM2Pipeline | None = None


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


def _generate_audio_hidden_states(
    pipe: AudioLDM2Pipeline,
    inputs_embeds: torch.Tensor = None,
    max_new_tokens: int | None = None,
    **model_kwargs: Any,
) -> torch.Tensor:
    """Compatibility replacement for AudioLDM2Pipeline.generate_language_model.

    Diffusers 0.40 still drives the GPT2 hidden-state rollout through private
    GenerationMixin helpers (`_get_initial_cache_position`,
    `_update_model_kwargs_for_generation`) that transformers 5 no longer
    exposes on bare GPT2Model. The rollout is only eight steps over a short
    projection sequence, so uncached full-sequence forward passes reproduce
    the stock semantics through the public model API alone.
    """
    if max_new_tokens is None:
        max_new_tokens = int(pipe.language_model.config.max_new_tokens)
    attention_mask = model_kwargs.get("attention_mask")
    for _ in range(int(max_new_tokens)):
        forward_kwargs: dict[str, Any] = {
            "inputs_embeds": inputs_embeds,
            "use_cache": False,
            "return_dict": True,
        }
        if attention_mask is not None:
            forward_kwargs["attention_mask"] = attention_mask
        output = pipe.language_model(**forward_kwargs)
        inputs_embeds = torch.cat(
            [inputs_embeds, output.last_hidden_state[:, -1:, :]], dim=1
        )
        if attention_mask is not None:
            attention_mask = torch.cat(
                [attention_mask, attention_mask.new_ones((attention_mask.shape[0], 1))],
                dim=-1,
            )
    return inputs_embeds[:, -int(max_new_tokens):, :]


def _require_models() -> None:
    required = [BASE_MODEL_ROOT / "model_index.json", ADAPTER_HIGH, ADAPTER_LOW]
    if LIGHTNING_ENABLED:
        required += [LIGHTNING_HIGH, LIGHTNING_LOW]
    missing = [str(path) for path in required if not path.is_file()]
    if missing:
        raise RuntimeError(
            "Wan V2 requires the base model and all mandatory LoRA weights: "
            + ", ".join(missing)
        )


def _generator() -> DiffGenerator:
    """Keep the complete FP8 pipeline resident on the 96 GB production GPU."""
    global _GENERATOR
    if _GENERATOR is None:
        _require_models()
        if os.getenv("WAN_ATTENTION_BACKEND", "torch_sdpa") == "sage_attn":
            # SGLang silently falls back to FlashAttention when sageattention
            # is missing, and that fallback is unreliable on SM12.x. Surface a
            # provisioning error instead of benchmarking the wrong backend.
            import sageattention  # noqa: F401
        aux_cpu_offload = os.getenv("WAN_AUX_CPU_OFFLOAD", "0") == "1"
        generator = DiffGenerator.from_pretrained(
            model_path=str(BASE_MODEL_ROOT),
            num_gpus=1,
            performance_mode="speed",
            attention_backend=os.getenv("WAN_ATTENTION_BACKEND", "torch_sdpa"),
            dit_cpu_offload=False,
            dit_layerwise_offload=False,
            text_encoder_cpu_offload=aux_cpu_offload,
            vae_cpu_offload=aux_cpu_offload,
            pin_cpu_memory=aux_cpu_offload,
            enable_torch_compile=False,
            warmup_mode="off",
            lora_merge_mode="dynamic",
        )
        # Static FP8 weights cannot be destructively merged with LoRA. Dynamic
        # application preserves the quantized base and addresses both Wan 2.2 experts.
        lora_names = ["adult_high", "adult_low"]
        lora_paths = [str(ADAPTER_HIGH), str(ADAPTER_LOW)]
        lora_targets = ["transformer", "transformer_2"]
        lora_strengths = [ADAPTER_STRENGTH, ADAPTER_STRENGTH]
        if LIGHTNING_ENABLED:
            lora_names += ["lightning_high", "lightning_low"]
            lora_paths += [str(LIGHTNING_HIGH), str(LIGHTNING_LOW)]
            lora_targets += ["transformer", "transformer_2"]
            lora_strengths += [LIGHTNING_STRENGTH, LIGHTNING_STRENGTH]
        generator.set_lora(
            lora_names,
            lora_paths,
            target=lora_targets,
            strength=lora_strengths,
            merge_mode="dynamic",
        )
        _GENERATOR = generator
    return _GENERATOR


def _audio_pipeline() -> AudioLDM2Pipeline:
    """Load prompt-conditioned sound generation lazily after video inference."""
    global _AUDIO_PIPELINE
    if _AUDIO_PIPELINE is None:
        if not (AUDIO_MODEL_ROOT / "model_index.json").is_file():
            raise RuntimeError(f"Wan V2 audio model is missing: {AUDIO_MODEL_ROOT}")
        pipe = AudioLDM2Pipeline.from_pretrained(
            AUDIO_MODEL_ROOT,
            torch_dtype=torch.float16,
            local_files_only=True,
        )
        pipe.generate_language_model = MethodType(_generate_audio_hidden_states, pipe)
        pipe.enable_model_cpu_offload()
        _AUDIO_PIPELINE = pipe
    return _AUDIO_PIPELINE


def _generate_audio(prompt: str, path: Path, *, duration: int, seed: int) -> int:
    audio_pipe = _audio_pipeline()
    output = audio_pipe(
        prompt=(
            "high quality synchronized cinematic ambience and realistic sound effects, "
            f"no music unless explicitly requested, scene: {prompt}"
        ),
        negative_prompt="low quality, distorted, clipping, harsh noise",
        audio_length_in_s=float(duration) + 0.1,
        num_inference_steps=int(os.getenv("WAN_AUDIO_INFERENCE_STEPS", "50")),
        guidance_scale=float(os.getenv("WAN_AUDIO_GUIDANCE_SCALE", "3.5")),
        generator=torch.Generator(device="cuda").manual_seed(seed ^ 0xA0D10),
    ).audios[0]
    sample_rate = int(audio_pipe.vocoder.config.sampling_rate)
    waveform = np.asarray(output, dtype=np.float32).squeeze()
    peak = float(np.max(np.abs(waveform))) if waveform.size else 0.0
    if peak > 0:
        waveform = waveform * (0.95 / peak)
    wavfile.write(path, sample_rate, (waveform * 32767.0).astype(np.int16))
    return sample_rate


def _mux_audio(video_path: Path, audio_path: Path, output_path: Path) -> None:
    subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-i",
            str(video_path),
            "-i",
            str(audio_path),
            "-map",
            "0:v:0",
            "-map",
            "1:a:0",
            "-c:v",
            "copy",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            "-shortest",
            "-movflags",
            "+faststart",
            str(output_path),
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


def _validate_locked_adapter(params: dict[str, Any]) -> None:
    expected_id = os.getenv(
        "WAN_ADULT_ADAPTER_ID", "lopi999/Wan2.2-I2V_General-NSFW-LoRA"
    )
    expected_version = os.getenv(
        "WAN_ADULT_ADAPTER_VERSION", "aeef17d7fa51d753ab7d1004ddb4f218a95d756d"
    )
    if params.get("adult_adapter_id") not in {None, "", expected_id}:
        raise ValueError("adult adapter id does not match the locked worker configuration")
    if params.get("adult_adapter_version") not in {None, "", expected_version}:
        raise ValueError("adult adapter version does not match the locked worker configuration")
    if "adult_adapter_strength" in params and float(params["adult_adapter_strength"]) != ADAPTER_STRENGTH:
        raise ValueError("adult adapter strength does not match the locked worker configuration")


def handler(job: dict[str, Any]) -> dict[str, Any]:
    params = job.get("input") or {}
    prompt = str(params.get("prompt") or "").strip()
    validate_prompt(prompt)
    _validate_locked_adapter(params)
    duration = int(params.get("duration", 5))
    num_frames = frames_for_duration(duration)
    resolution = str(params.get("resolution") or "480p")
    width, height = dimensions(str(params.get("ratio") or "16:9"), resolution)
    generate_audio = bool(params.get("generate_audio", True))
    seed = int(params.get("seed", -1))
    if seed < 0:
        seed = int.from_bytes(os.urandom(4), "big")
    prompt = ensure_trigger(prompt, os.getenv("WAN_ADULT_TRIGGER", "nsfwsks"))
    negative_prompt = os.getenv(
        "WAN_NEGATIVE_PROMPT",
        "subtitles, watermark, text, static, blurry, low quality, deformed, disfigured, "
        "extra fingers, fused limbs, duplicated body parts, bad anatomy",
    )

    with tempfile.TemporaryDirectory(prefix="wan-job-") as directory:
        video_only = Path(directory) / "video.mp4"
        audio = Path(directory) / "audio.wav"
        output = Path(directory) / "output.mp4"
        started = time.monotonic()
        _progress(job, "model_load_start")
        load_started = time.monotonic()
        generator = _generator()
        model_load_seconds = round(time.monotonic() - load_started, 3)
        _progress(job, "model_load_done", seconds=model_load_seconds)
        _progress(job, "video_start", width=width, height=height, frames=num_frames)
        video_started = time.monotonic()
        sampling_params_kwargs = {
            "prompt": prompt,
            "negative_prompt": negative_prompt,
            "width": width,
            "height": height,
            "num_frames": num_frames,
            "fps": FPS,
            "num_inference_steps": int(os.getenv("WAN_INFERENCE_STEPS", "40")),
            "guidance_scale": float(os.getenv("WAN_GUIDANCE_SCALE", "4.0")),
            "guidance_scale_2": float(os.getenv("WAN_GUIDANCE_SCALE_2", "3.0")),
            "seed": seed,
            "save_output": True,
            "return_file_paths_only": True,
            "output_path": directory,
            "output_file_name": video_only.name,
        }
        # Lightning distillation is trained against a shifted flow schedule;
        # leave the pipeline default unless the profile sets it explicitly.
        flow_shift = os.getenv("WAN_FLOW_SHIFT", "").strip()
        if flow_shift:
            sampling_params_kwargs["flow_shift"] = float(flow_shift)
        generation = generator.generate(sampling_params_kwargs=sampling_params_kwargs)
        if generation is None or not generation.output_file_path:
            raise RuntimeError("SGLang returned no Wan video output")
        video_only = Path(generation.output_file_path)
        if not video_only.is_file():
            raise RuntimeError(f"SGLang output is missing: {video_only}")
        video_seconds = round(time.monotonic() - video_started, 3)
        _progress(job, "video_done", seconds=video_seconds)
        sample_rate = None
        audio_seconds = 0.0
        audio_model_load_seconds = 0.0
        if generate_audio:
            torch.cuda.empty_cache()
            _progress(job, "audio_model_load_start")
            audio_load_started = time.monotonic()
            _audio_pipeline()
            audio_model_load_seconds = round(time.monotonic() - audio_load_started, 3)
            _progress(job, "audio_model_load_done", seconds=audio_model_load_seconds)
            _progress(job, "audio_start")
            audio_started = time.monotonic()
            sample_rate = _generate_audio(
                prompt,
                audio,
                duration=duration,
                seed=seed,
            )
            _mux_audio(video_only, audio, output)
            audio_seconds = round(time.monotonic() - audio_started, 3)
            _progress(job, "audio_done", seconds=audio_seconds)
        else:
            video_only.replace(output)
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
            "model_id": os.getenv(
                "WAN_MODEL_ID", "nvidia/Wan2.2-T2V-A14B-Diffusers-FP8"
            ),
            "model_version": os.getenv(
                "WAN_MODEL_VERSION", "2c5a06469cd2255816eb2e46b8e11600ed435d52"
            ),
            "workflow_version": os.getenv(
                "WAN_WORKFLOW_VERSION", "wan22-t2v-fp8-resident96-adult-lora-audio-v5"
            ),
            "adult_adapter_id": os.getenv(
                "WAN_ADULT_ADAPTER_ID", "lopi999/Wan2.2-I2V_General-NSFW-LoRA"
            ),
            "adult_adapter_version": os.getenv(
                "WAN_ADULT_ADAPTER_VERSION", "aeef17d7fa51d753ab7d1004ddb4f218a95d756d"
            ),
            "adult_adapter_strength": ADAPTER_STRENGTH,
            "gpu_name": torch.cuda.get_device_name(),
            "engine": "sglang",
            "engine_version": "0.5.16",
            "quantization": "nvidia-modelopt-fp8",
            "inference_seconds": inference_seconds,
            "video_inference_seconds": video_seconds,
            "audio_inference_seconds": audio_seconds,
            "model_load_seconds": model_load_seconds,
            "audio_model_load_seconds": audio_model_load_seconds,
            "upload_seconds": upload_seconds,
            "attention_backend": os.getenv("WAN_ATTENTION_BACKEND", "torch_sdpa"),
            "inference_steps": int(os.getenv("WAN_INFERENCE_STEPS", "40")),
            "guidance_scale": float(os.getenv("WAN_GUIDANCE_SCALE", "4.0")),
            "guidance_scale_2": float(os.getenv("WAN_GUIDANCE_SCALE_2", "3.0")),
            "lightning_enabled": LIGHTNING_ENABLED,
            "lightning_strength": LIGHTNING_STRENGTH if LIGHTNING_ENABLED else None,
            "flow_shift": float(flow_shift) if flow_shift else None,
            "peak_memory_mb": generation.peak_memory_mb,
            "duration": duration,
            "fps": FPS,
            "frame_count": num_frames,
            "width": width,
            "height": height,
            "has_audio": generate_audio,
            "audio_model_id": "cvssp/audioldm2" if generate_audio else None,
            "audio_sample_rate": sample_rate,
        }


if __name__ == "__main__":
    if os.getenv("EAGER_LOAD_MODELS", "1") == "1":
        _generator()
    runpod.serverless.start({"handler": handler})
