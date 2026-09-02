#!/usr/bin/env bash
# Thin wrapper so a standalone CPU Pod can populate a volume with one command.
# The real logic lives in download_models.py, which uses the huggingface_hub
# Python API rather than the `hf` command: installing huggingface_hub[cli] to get
# that command upgrades the copy SGLang ships and breaks the SageAttention build.
set -euo pipefail
exec python3 "$(dirname "$0")/download_models.py"
