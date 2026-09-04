#!/usr/bin/env bash
# Thin wrapper for provisioning a network volume by hand. The worker itself
# calls download_models.py at start when no volume supplied the weights.
set -euo pipefail
export MODEL_ROOT="${MODEL_ROOT:-${MODEL_VOLUME_DIR:-}}"
exec python "$(dirname "$0")/download_models.py"
