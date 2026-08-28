#!/usr/bin/env bash
set -euo pipefail

if [[ -d /runpod-volume ]]; then
  DEFAULT_ROOT=/runpod-volume/models/Wan2.2-T2V-A14B-Adult-v2
else
  DEFAULT_ROOT=/workspace/models/Wan2.2-T2V-A14B-Adult-v2
fi

MODEL_ROOT="${MODEL_ROOT:-$DEFAULT_ROOT}"
DOWNLOAD_MAX_WORKERS="${DOWNLOAD_MAX_WORKERS:-8}"
mkdir -p "$MODEL_ROOT/base" "$MODEL_ROOT/adult-lora"

hf download Wan-AI/Wan2.2-T2V-A14B-Diffusers \
  --revision 5be7df9619b54f4e2667b2755bc6a756675b5cd7 \
  --local-dir "$MODEL_ROOT/base" \
  --max-workers "$DOWNLOAD_MAX_WORKERS"

hf download lopi999/Wan2.2-I2V_General-NSFW-LoRA \
  NSFW-22-H-e8.safetensors NSFW-22-L-e8.safetensors \
  --revision aeef17d7fa51d753ab7d1004ddb4f218a95d756d \
  --local-dir "$MODEL_ROOT/adult-lora" \
  --max-workers "$DOWNLOAD_MAX_WORKERS"

echo "Wan V2 model files are ready under $MODEL_ROOT"
