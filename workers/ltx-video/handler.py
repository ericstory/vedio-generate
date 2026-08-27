from __future__ import annotations

import os
from pathlib import Path
import tempfile
from typing import Any
from uuid import uuid4

import boto3
import httpx
import runpod
import torch
from ltx_core.loader import LTXV_LORA_COMFY_RENAMING_MAP, LoraPathStrengthAndSDOps
from ltx_core.model.video_vae import get_video_chunks_number
from ltx_pipelines.ti2vid_two_stages import TI2VidTwoStagesPipeline
from ltx_pipelines.utils.constants import DEFAULT_NEGATIVE_PROMPT, LTX_2_3_PARAMS
from ltx_pipelines.utils.media_io import encode_video
from ltx_pipelines.utils.model_paths import ModelPaths
from ltx_pipelines.utils.quantization_factory import QuantizationKind
from ltx_pipelines.utils.types import OffloadMode

from worker_config import (
    FPS,
    dimensions,
    frame_count,
    quantization_for_compute_capability,
    validate_prompt,
)


MODEL_ROOT = Path(os.getenv("MODEL_ROOT", "/runpod-volume/models/PinkCherry-LTX-2.3-v1.8"))
CHECKPOINT = Path(
    os.getenv(
        "LTX_CHECKPOINT_PATH",
        str(MODEL_ROOT / "v1.8" / "PinkCherry_FineTune_bf16_v1_8_LTX23.safetensors"),
    )
)
UPSCALER = Path(
    os.getenv(
        "LTX_UPSCALER_PATH",
        str(MODEL_ROOT / "ltx-2.3-spatial-upscaler-x2-1.1.safetensors"),
    )
)
DISTILLED_LORA = Path(
    os.getenv(
        "LTX_DISTILLED_LORA_PATH",
        str(MODEL_ROOT / "ltx-2.3-22b-distilled-lora-384-1.1.safetensors"),
    )
)
GEMMA_ROOT = Path(os.getenv("LTX_GEMMA_ROOT", str(MODEL_ROOT / "gemma-3-12b")))
_PIPELINE: TI2VidTwoStagesPipeline | None = None


def _require_models() -> None:
    missing = [
        str(path)
        for path in (CHECKPOINT, UPSCALER, DISTILLED_LORA, GEMMA_ROOT)
        if not path.exists()
    ]
    if missing:
        raise RuntimeError("model files are missing: " + ", ".join(missing))


def _pipeline() -> TI2VidTwoStagesPipeline:
    """Load once per warm RunPod worker and reuse GPU-resident weights across jobs."""
    global _PIPELINE
    if _PIPELINE is None:
        _require_models()
        requested_quantization = os.getenv("LTX_QUANTIZATION", "auto")
        compute_major, compute_minor = torch.cuda.get_device_capability()
        quantization_name = quantization_for_compute_capability(
            requested_quantization, compute_major, compute_minor
        )
        quantization = (
            QuantizationKind(quantization_name).to_policy(str(CHECKPOINT))
            if quantization_name
            else None
        )
        offload = OffloadMode(os.getenv("LTX_OFFLOAD", "none"))
        distilled_lora = LoraPathStrengthAndSDOps(
            str(DISTILLED_LORA),
            float(os.getenv("LTX_DISTILLED_LORA_STRENGTH", "0.6")),
            LTXV_LORA_COMFY_RENAMING_MAP,
        )
        _PIPELINE = TI2VidTwoStagesPipeline(
            model_paths=ModelPaths.from_monolith(str(CHECKPOINT), str(GEMMA_ROOT)),
            distilled_lora=[distilled_lora],
            spatial_upsampler_path=str(UPSCALER),
            loras=[],
            quantization=quantization,
            offload_mode=offload,
        )
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
        body = response.json()
        video_url = str(body.get("video_url") or "")
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


def handler(job: dict[str, Any]) -> dict[str, Any]:
    params = job.get("input") or {}
    prompt = str(params.get("prompt") or "").strip()
    validate_prompt(prompt)
    duration = int(params.get("duration", 6))
    if duration < 4 or duration > 15:
        raise ValueError("duration must be between 4 and 15 seconds")
    resolution = str(params.get("resolution") or "720p")
    if resolution not in {"480p", "720p"}:
        raise ValueError("LTX 2.3 supports 480p or 720p in this workflow")
    width, height = dimensions(str(params.get("ratio") or "16:9"), resolution)
    seed = int(params.get("seed", -1))
    if seed < 0:
        seed = int.from_bytes(os.urandom(4), "big")

    with tempfile.TemporaryDirectory(prefix="ltx-job-") as directory:
        output = Path(directory) / "output.mp4"
        # LTX's official CLI runs the complete pipeline under inference mode.
        # The returned video is a lazy iterator, so encode_video must stay inside
        # the same context: consuming it performs the actual VAE decode.
        with torch.inference_mode():
            result = _pipeline()(
                prompt=prompt,
                negative_prompt=os.getenv("LTX_NEGATIVE_PROMPT", DEFAULT_NEGATIVE_PROMPT),
                seed=seed,
                height=height,
                width=width,
                frame_rate=FPS,
                images=[],
                num_frames=frame_count(duration),
                num_inference_steps=int(os.getenv("LTX_INFERENCE_STEPS", "20")),
                video_guider_params=LTX_2_3_PARAMS.video_guider_params,
                audio_guider_params=LTX_2_3_PARAMS.audio_guider_params,
            )
            encode_video(
                video=result.video,
                fps=FPS,
                audio=result.audio,
                output_path=str(output),
                video_chunks_number=get_video_chunks_number(result.num_frames, result.tiling_config),
                color_space=None,
            )
        key = f"videos/{job.get('id') or uuid4()}.mp4"
        return {
            "video_url": _upload(output, key),
            "seed": seed,
            "model_id": os.getenv("SELF_HOSTED_MODEL_ID", "SexGod1979/PinkCherry_NSFW_LTX23"),
            "model_version": os.getenv(
                "SELF_HOSTED_MODEL_VERSION", "PinkCherry_FineTune_bf16_v1_8_LTX23"
            ),
            "workflow_version": os.getenv(
                "SELF_HOSTED_WORKFLOW_VERSION", "pinkcherry-native-two-stage-v1"
            ),
        }


if os.getenv("EAGER_LOAD_MODELS", "1") == "1":
    _pipeline()

runpod.serverless.start({"handler": handler})
