from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
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


def _default_model_root() -> Path:
    # A mounted network volume keeps the weights between Pods; without one the
    # worker pulls them to container disk at start (see ensure_models).
    if Path("/runpod-volume").is_dir():
        return Path("/runpod-volume/models/PinkCherry-LTX-2.3-v1.8")
    return Path("/models/PinkCherry-LTX-2.3-v1.8")


MODEL_ROOT = Path(os.getenv("MODEL_ROOT") or _default_model_root())
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
DOWNLOAD_MARKER = MODEL_ROOT / ".download-complete"
DOWNLOAD_SCRIPT = Path(__file__).with_name("download_models.py")
_PIPELINE: TI2VidTwoStagesPipeline | None = None


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


def _required_model_files() -> tuple[Path, ...]:
    return (CHECKPOINT, UPSCALER, DISTILLED_LORA, GEMMA_ROOT)


def _require_models() -> None:
    missing = [str(path) for path in _required_model_files() if not path.exists()]
    if missing:
        raise RuntimeError("model files are missing: " + ", ".join(missing))


def ensure_models(job: dict[str, Any] | None = None) -> float:
    """Fetch the weights to container disk when no network volume supplied them.

    Same reasoning as the H3 lane: a regional volume pins the Pod to the one
    data centre that holds it, and that pin is what starves a lane of GPUs.
    Pulling ~79 GB at start costs about a minute on a fast host and buys
    placement anywhere.
    """
    if all(path.exists() for path in _required_model_files()):
        return 0.0
    if os.getenv("LTX_DOWNLOAD_ON_START", "1") != "1":
        missing = [str(p) for p in _required_model_files() if not p.exists()]
        raise RuntimeError(
            "LTX weights are missing and start-up download is disabled: " + ", ".join(missing)
        )
    if job is not None:
        _progress(job, "model_download_start")
    started = time.monotonic()
    env = {**os.environ, "MODEL_ROOT": str(MODEL_ROOT)}
    # Gemma is gated and anonymous pulls are rate limited, so the token matters.
    subprocess.run([sys.executable, str(DOWNLOAD_SCRIPT)], check=True, env=env)
    DOWNLOAD_MARKER.touch()
    seconds = round(time.monotonic() - started, 3)
    if job is not None:
        _progress(job, "model_download_done", seconds=seconds)
    return seconds


def assert_gpu_healthy(attempts: int = 3, delay_seconds: float = 10.0) -> str:
    """Fail within seconds on a broken host instead of after the download.

    The H3 lane met a RunPod host whose first CUDA call died with
    `CUDA unknown error` after 16 minutes of downloading. Probing first hands
    such a Pod back while it has billed seconds; a couple of retries cover a
    GPU that is merely slow to appear after container start.
    """
    last: Exception | None = None
    for attempt in range(attempts):
        try:
            torch.cuda.init()
            return torch.cuda.get_device_name(0)
        except Exception as exc:  # torch raises plain RuntimeError here
            last = exc
            if attempt + 1 < attempts:
                time.sleep(delay_seconds)
    raise RuntimeError(
        f"GPU host unhealthy: {type(last).__name__}: {str(last)[:300]}"
    ) from last


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

    frames = frame_count(duration)
    steps = int(os.getenv("LTX_INFERENCE_STEPS", "20"))

    with tempfile.TemporaryDirectory(prefix="ltx-job-") as directory:
        output = Path(directory) / "output.mp4"
        started = time.monotonic()
        # Before anything expensive: a bad host must fail in seconds, not minutes.
        _progress(job, "gpu_probe", gpu=assert_gpu_healthy())
        download_seconds = ensure_models(job)
        _progress(job, "model_load_start")
        load_started = time.monotonic()
        pipeline = _pipeline()
        model_load_seconds = round(time.monotonic() - load_started, 3)
        _progress(job, "model_load_done", seconds=model_load_seconds)
        _progress(job, "video_start", width=width, height=height, frames=frames, steps=steps)
        torch.cuda.reset_peak_memory_stats()
        video_started = time.monotonic()
        # LTX's official CLI runs the complete pipeline under inference mode.
        # The returned video is a lazy iterator, so encode_video must stay inside
        # the same context: consuming it performs the actual VAE decode.
        with torch.inference_mode():
            result = pipeline(
                prompt=prompt,
                negative_prompt=os.getenv("LTX_NEGATIVE_PROMPT", DEFAULT_NEGATIVE_PROMPT),
                seed=seed,
                height=height,
                width=width,
                frame_rate=FPS,
                images=[],
                num_frames=frames,
                num_inference_steps=steps,
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
        video_seconds = round(time.monotonic() - video_started, 3)
        _progress(job, "video_done", seconds=video_seconds)
        peak_memory_mb = round(torch.cuda.max_memory_allocated() / 2**20)
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
            "model_id": os.getenv("SELF_HOSTED_MODEL_ID", "SexGod1979/PinkCherry_NSFW_LTX23"),
            "model_version": os.getenv(
                "SELF_HOSTED_MODEL_VERSION", "PinkCherry_FineTune_bf16_v1_8_LTX23"
            ),
            "workflow_version": os.getenv(
                "SELF_HOSTED_WORKFLOW_VERSION", "pinkcherry-native-two-stage-v1"
            ),
            "gpu_name": torch.cuda.get_device_name(),
            "engine": "ltx-pipelines",
            "inference_seconds": inference_seconds,
            "video_inference_seconds": video_seconds,
            "model_load_seconds": model_load_seconds,
            "model_download_seconds": download_seconds,
            "upload_seconds": upload_seconds,
            "peak_memory_mb": peak_memory_mb,
            "inference_steps": steps,
            "duration": duration,
            "fps": FPS,
            "frame_count": frames,
            "width": width,
            "height": height,
            "has_audio": True,
        }


if __name__ == "__main__":
    if os.getenv("EAGER_LOAD_MODELS", "1") == "1":
        _pipeline()
    runpod.serverless.start({"handler": handler})
