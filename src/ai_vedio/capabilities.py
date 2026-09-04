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
    "minimax-h3-pinkcherry",
    "minimax-h3-10eros",
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
    "minimax-h3-pinkcherry",
    "minimax-h3-10eros",
})
LTX_MODEL = "pinkcherry-ltx-2.3-v1.8"
# The second NSFW layer on the same MiniMax H3 worker image: 10Eros Max's
# restored full-AdaLN transformer with its TURBO distillation baked in, so no
# turbo LoRA and fewer steps. It runs as its own Pod lane so a warm Pod never
# has to swap 66 GB of transformer weights between the two checkpoints.
EROS_MODEL = "minimax-h3-10eros"
EROS_POD_PROVIDER = "runpod_eros_pod"
# Both are MiniMax H3 underneath: 768p is H3's short edge and the only measured
# recipe, so the 768p rules and the no-reference-image rule apply to both.
H3_FAMILY = frozenset({"minimax-h3-pinkcherry", EROS_MODEL})
# LTX's static default is still the serverless endpoint so historical rows and
# an un-flagged deployment keep working; with LTX_POD_ENABLED=1 the web app
# routes new LTX tasks to LTX_POD_PROVIDER instead (see web._provider_for).
LTX_POD_PROVIDER = "runpod_ltx_pod"
SELF_HOSTED_PROVIDERS = {
    LTX_MODEL: "runpod",
    "wan-2.2-a14b-adult-v2": "runpod_wan_pod",
    "minimax-h3-pinkcherry": "runpod_h3_pod",
    EROS_MODEL: EROS_POD_PROVIDER,
}
# Pod lanes: the Worker reports its own terminal state and live stages back over
# the authenticated internal callbacks. The control plane runs one Pod per lane;
# a worker that pulls jobs keeps it warm through an idle window, a one-shot
# worker (or any failure) has the billed Pod deleted as soon as the terminal
# callback commits.
POD_PROVIDERS = frozenset({"runpod_wan_pod", "runpod_h3_pod", LTX_POD_PROVIDER, EROS_POD_PROVIDER})
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
