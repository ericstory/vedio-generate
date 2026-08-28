from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class VideoDefaults:
    resolution: str = "480p"
    ratio: str = "16:9"
    duration: int = 4
    generate_audio: bool = False
    watermark: bool = True


SUPPORTED_MODELS = (
    "wan-2.2-a14b-adult-v2",
    "pinkcherry-ltx-2.3-v1.8",
    "seedance-2.5",
    "seedance-2-mini",
    "seedance-2-fast",
    "seedance-2.0",
)

SELF_HOSTED_MODELS = frozenset({
    "pinkcherry-ltx-2.3-v1.8",
    "wan-2.2-a14b-adult-v2",
})
SELF_HOSTED_PROVIDERS = {
    "pinkcherry-ltx-2.3-v1.8": "runpod",
    "wan-2.2-a14b-adult-v2": "runpod_wan",
}
SEEDANCE_MODELS = frozenset(SUPPORTED_MODELS) - SELF_HOSTED_MODELS

TERMINAL_STATUSES = frozenset({"succeeded", "failed", "cancelled", "expired"})
DEFAULTS = VideoDefaults()

# These values are intentionally limited to combinations exercised successfully.
VERIFIED_CAPABILITIES = {
    "region": "ap-southeast-1",
    "text_to_video": True,
    "async_tasks": True,
    "asset_uri": "asset://<asset-id>",
    "verified_resolution": "480p",
    "verified_ratio": "16:9",
    "verified_duration_seconds": 4,
    "output_container": "mp4",
    "output_video_codec": "h264",
    "output_fps": 24,
}
