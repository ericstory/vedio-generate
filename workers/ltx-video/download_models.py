"""Fetch the PinkCherry LTX 2.3 weights with the huggingface_hub Python API.

The worker runs this at start when the Pod has no network volume (the
volume-free lane, same shape as MiniMax H3), and download_models.sh wraps it to
provision a volume by hand. Revisions are the ones pinned in models.lock.json;
huggingface_hub verifies Hub/Xet object integrity on the way down, so the
79 GB are not re-hashed here. Gemma is a gated repository: HF_TOKEN must belong
to an account that accepted its terms.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from huggingface_hub import snapshot_download


PINKCHERRY_REPO = "SexGod1979/PinkCherry_NSFW_LTX23"
PINKCHERRY_REVISION = "aa5e3192dacd58e8c807e4198e3fcecc53db9f80"
PINKCHERRY_FILE = "v1.8/PinkCherry_FineTune_bf16_v1_8_LTX23.safetensors"
LTX_REPO = "Lightricks/LTX-2.3"
LTX_REVISION = "6b5a83e3045eaf8e46cfa0acce512412aa2b9cce"
LTX_FILES = (
    "ltx-2.3-22b-distilled-lora-384-1.1.safetensors",
    "ltx-2.3-spatial-upscaler-x2-1.1.safetensors",
)
GEMMA_REPO = "google/gemma-3-12b-it-qat-q4_0-unquantized"
GEMMA_REVISION = "68f7ee4fbd59087436ada77ed2d62f373fdd4482"
GEMMA_SUBDIR = "gemma-3-12b"
GEMMA_SHARDS = tuple(f"model-{index:05d}-of-00005.safetensors" for index in range(1, 6))


def default_root() -> Path:
    # With a network volume mounted the weights persist there between Pods.
    # Without one the Pod is free to land in any data centre and the weights
    # come down to container disk on every cold start instead.
    if Path("/runpod-volume").is_dir():
        return Path("/runpod-volume/models/PinkCherry-LTX-2.3-v1.8")
    return Path("/models/PinkCherry-LTX-2.3-v1.8")


def expected_files(root: Path) -> list[Path]:
    return [
        root / PINKCHERRY_FILE,
        *(root / name for name in LTX_FILES),
        *(root / GEMMA_SUBDIR / shard for shard in GEMMA_SHARDS),
    ]


def main() -> int:
    root = Path(os.getenv("MODEL_ROOT") or default_root())
    workers = int(os.getenv("DOWNLOAD_MAX_WORKERS", "8"))
    root.mkdir(parents=True, exist_ok=True)

    snapshot_download(
        repo_id=PINKCHERRY_REPO,
        revision=PINKCHERRY_REVISION,
        allow_patterns=[PINKCHERRY_FILE],
        local_dir=str(root),
        max_workers=workers,
    )
    snapshot_download(
        repo_id=LTX_REPO,
        revision=LTX_REVISION,
        allow_patterns=list(LTX_FILES),
        local_dir=str(root),
        max_workers=workers,
    )
    snapshot_download(
        repo_id=GEMMA_REPO,
        revision=GEMMA_REVISION,
        local_dir=str(root / GEMMA_SUBDIR),
        max_workers=workers,
    )

    missing = [str(path) for path in expected_files(root) if not path.is_file()]
    if missing:
        raise RuntimeError("download finished but files are missing: " + ", ".join(missing))
    print(f"PinkCherry LTX 2.3 v1.8 model files are ready under {root}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
