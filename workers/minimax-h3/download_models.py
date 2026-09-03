"""Fetch the H3 weights with the huggingface_hub Python API.

Deliberately not the `hf` command line. Installing `huggingface_hub[cli]` to get
that command upgraded the copy already in the SGLang base image, and the newer
release's strict dataclass validator raises

    TypeError: Unsupported type for field 'import_name': str | None

while SageAttention generates its package metadata, which failed the image build
outright. SGLang depends on huggingface_hub anyway, so using whichever version
the base image pins costs nothing and keeps the build reproducible.
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

from huggingface_hub import snapshot_download


BASE_REPO = "MiniMaxAI/MiniMax-H3"
BASE_REVISION = "42ed227ee7df40d41602854ae760620d6eb651fe"
NSFW_REPO = "SexGod1979/PinkCherry_MiniMax-H3"
NSFW_REVISION = "bf2fef11d0e55e957f4af997e3beade3362f44b3"
NSFW_FILE = "beta-0.6-fl2va/PinkCherry_fl2va_MiniMax_H3_bf16_beta-0.6.safetensors"
TURBO_REPO = "lightx2v/Minimax-h3-Turbo"
TURBO_REVISION = "05ef678438e84933c406131b59abbf86919b3aac"
TURBO_FILE = "minimax_h3_fl2v_turbo_8step_v1.0_768p_bf16.safetensors"
FAST_TURBO_FILE = "minimax_h3_fl2v_turbo_4step_v1.1_768p_bf16.safetensors"


def _default_root() -> Path:
    # With a network volume mounted the weights persist there between Pods.
    # Without one the Pod is free to land in any data centre and the weights come
    # down to container disk on every cold start instead.
    # The basename must stay "MiniMax-H3": SGLang picks the native H3 pipeline
    # and config by matching it against the HF repo id (see handler.py).
    if Path("/runpod-volume").is_dir():
        return Path("/runpod-volume/models/MiniMaxAI/MiniMax-H3")
    return Path("/models/MiniMaxAI/MiniMax-H3")


def main() -> int:
    root = Path(os.getenv("MODEL_ROOT") or _default_root())
    workers = int(os.getenv("DOWNLOAD_MAX_WORKERS", "8"))
    # PinkCherry replaces the DiT wholesale, so the stock transformer shards are
    # 66 GB of dead weight. Set this to 1 to keep them for a stock-vs-PinkCherry
    # A/B from one copy.
    include_stock = os.getenv("INCLUDE_STOCK_TRANSFORMER", "0") == "1"
    # Wan's 4-step output was judged below the quality bar, so 8-step is the
    # default here and the aggressive profile is opt-in.
    include_fast = os.getenv("INCLUDE_FAST_TURBO_LORA", "0") == "1"

    root.mkdir(parents=True, exist_ok=True)

    # FL2VA is the self-contained partition serving t2va as well as first/last
    # frame conditioning: transformer, Qwen3-VL text encoder, both VAEs,
    # tokenizer, processor and every config. Ref2VA is a separate 144 GB
    # partition this lane does not use.
    ignore = ["Ref2VA/*", "assets/*", "docs/*", "scripts/*"]
    if not include_stock:
        ignore.append("FL2VA/transformer/*.safetensors")
    snapshot_download(
        repo_id=BASE_REPO,
        revision=BASE_REVISION,
        allow_patterns=["FL2VA/*"],
        ignore_patterns=ignore,
        local_dir=str(root),
        max_workers=workers,
    )

    # Only the bf16 export is usable: the int8_convrot variants in the same
    # repository carry ComfyUI `comfy_quant` tensors and a pruned AdaLN path
    # SGLang's loader does not implement.
    staging = root / "pinkcherry-src"
    snapshot_download(
        repo_id=NSFW_REPO,
        revision=NSFW_REVISION,
        allow_patterns=[NSFW_FILE],
        local_dir=str(staging),
        max_workers=workers,
    )
    destination = root / "pinkcherry"
    destination.mkdir(parents=True, exist_ok=True)
    shutil.move(str(staging / NSFW_FILE), str(destination / Path(NSFW_FILE).name))
    shutil.rmtree(staging, ignore_errors=True)

    # Plain exports only: those carry key_format=minimax-h3-diffusers and the
    # training alpha in safetensors metadata, which is the form the SGLang
    # cookbook loads. The `_comfyui_` twins use ComfyUI's fused-QKV convention.
    turbo_files = [TURBO_FILE] + ([FAST_TURBO_FILE] if include_fast else [])
    snapshot_download(
        repo_id=TURBO_REPO,
        revision=TURBO_REVISION,
        allow_patterns=turbo_files,
        local_dir=str(root / "turbo-lora"),
        max_workers=workers,
    )

    print(f"MiniMax H3 model files are ready under {root}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
