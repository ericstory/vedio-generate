from __future__ import annotations

import os
from pathlib import Path
import tempfile
import time
from typing import Any
from uuid import uuid4

import boto3
import httpx
import runpod
import torch
from diffusers import AutoencoderKLWan, WanPipeline
from diffusers.utils import export_to_video

from worker_config import FPS, NUM_FRAMES, dimensions, ensure_trigger, validate_prompt


MODEL_ROOT = Path(
    os.getenv("MODEL_ROOT", "/runpod-volume/models/Wan2.2-T2V-A14B-Adult-v2")
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
_PIPELINE: WanPipeline | None = None


def _require_models() -> None:
    required = (BASE_MODEL_ROOT / "model_index.json", ADAPTER_HIGH, ADAPTER_LOW)
    missing = [str(path) for path in required if not path.is_file()]
    if missing:
        raise RuntimeError(
            "Wan V2 requires the base model and both mandatory adult LoRA weights: "
            + ", ".join(missing)
        )


def _pipeline() -> WanPipeline:
    """Load the full two-expert model and mandatory adapters once per warm worker."""
    global _PIPELINE
    if _PIPELINE is None:
        _require_models()
        vae = AutoencoderKLWan.from_pretrained(
            BASE_MODEL_ROOT,
            subfolder="vae",
            torch_dtype=torch.float32,
            local_files_only=True,
        )
        pipe = WanPipeline.from_pretrained(
            BASE_MODEL_ROOT,
            vae=vae,
            torch_dtype=torch.bfloat16,
            local_files_only=True,
        )
        pipe.load_lora_weights(
            ADAPTER_ROOT,
            weight_name=ADAPTER_HIGH.name,
            adapter_name="adult_high",
        )
        pipe.load_lora_weights(
            ADAPTER_ROOT,
            weight_name=ADAPTER_LOW.name,
            adapter_name="adult_low",
            load_into_transformer_2=True,
        )
        pipe.transformer.set_adapters(["adult_high"], weights=[ADAPTER_STRENGTH])
        if pipe.transformer_2 is None:
            raise RuntimeError("Wan 2.2 low-noise transformer is missing")
        pipe.transformer_2.set_adapters(["adult_low"], weights=[ADAPTER_STRENGTH])
        # The BF16 repository is ~126GB. Component offload keeps both experts available
        # while each denoising stage still executes on the selected 96GB GPU.
        pipe.enable_model_cpu_offload()
        _PIPELINE = pipe
    return _PIPELINE


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
    if int(params.get("duration", 5)) != 5:
        raise ValueError("Wan V2 produces exactly 5 seconds per shot")
    resolution = str(params.get("resolution") or "480p")
    width, height = dimensions(str(params.get("ratio") or "16:9"), resolution)
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
        output = Path(directory) / "output.mp4"
        started = time.monotonic()
        with torch.inference_mode():
            frames = _pipeline()(
                prompt=prompt,
                negative_prompt=negative_prompt,
                width=width,
                height=height,
                num_frames=NUM_FRAMES,
                num_inference_steps=int(os.getenv("WAN_INFERENCE_STEPS", "40")),
                guidance_scale=float(os.getenv("WAN_GUIDANCE_SCALE", "5.0")),
                generator=torch.Generator(device="cuda").manual_seed(seed),
            ).frames[0]
            export_to_video(frames, str(output), fps=FPS)
        inference_seconds = round(time.monotonic() - started, 3)
        key = f"videos/{job.get('id') or uuid4()}.mp4"
        return {
            "video_url": _upload(output, key),
            "seed": seed,
            "model_id": os.getenv(
                "WAN_MODEL_ID", "Wan-AI/Wan2.2-T2V-A14B-Diffusers"
            ),
            "model_version": os.getenv(
                "WAN_MODEL_VERSION", "5be7df9619b54f4e2667b2755bc6a756675b5cd7"
            ),
            "workflow_version": os.getenv(
                "WAN_WORKFLOW_VERSION", "wan22-t2v-adult-lora-v2"
            ),
            "adult_adapter_id": os.getenv(
                "WAN_ADULT_ADAPTER_ID", "lopi999/Wan2.2-I2V_General-NSFW-LoRA"
            ),
            "adult_adapter_version": os.getenv(
                "WAN_ADULT_ADAPTER_VERSION", "aeef17d7fa51d753ab7d1004ddb4f218a95d756d"
            ),
            "adult_adapter_strength": ADAPTER_STRENGTH,
            "gpu_name": torch.cuda.get_device_name(),
            "inference_seconds": inference_seconds,
        }


if os.getenv("EAGER_LOAD_MODELS", "1") == "1":
    _pipeline()

runpod.serverless.start({"handler": handler})
