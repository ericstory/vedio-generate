#!/usr/bin/env bash
set -euo pipefail

if [[ -d /runpod-volume ]]; then
  DEFAULT_ROOT=/runpod-volume/models/Wan2.2-T2V-A14B-Adult-FP8-v4
else
  DEFAULT_ROOT=/workspace/models/Wan2.2-T2V-A14B-Adult-FP8-v4
fi

MODEL_ROOT="${MODEL_ROOT:-$DEFAULT_ROOT}"
DOWNLOAD_MAX_WORKERS="${DOWNLOAD_MAX_WORKERS:-8}"
mkdir -p "$MODEL_ROOT/base" "$MODEL_ROOT/adult-lora" "$MODEL_ROOT/audio/audioldm2"

hf download nvidia/Wan2.2-T2V-A14B-Diffusers-FP8 \
  --revision 2c5a06469cd2255816eb2e46b8e11600ed435d52 \
  --local-dir "$MODEL_ROOT/base" \
  --max-workers "$DOWNLOAD_MAX_WORKERS"

hf download lopi999/Wan2.2-I2V_General-NSFW-LoRA \
  NSFW-22-H-e8.safetensors NSFW-22-L-e8.safetensors \
  --revision aeef17d7fa51d753ab7d1004ddb4f218a95d756d \
  --local-dir "$MODEL_ROOT/adult-lora" \
  --max-workers "$DOWNLOAD_MAX_WORKERS"

# AudioLDM2 provides prompt-conditioned ambience and sound effects. Prefer
# safetensors and skip duplicate PyTorch .bin weights to save volume space.
hf download cvssp/audioldm2 \
  --revision c8e7e189d324425c05c4c2f81214041ef4107983 \
  --exclude "*.bin" \
  --local-dir "$MODEL_ROOT/audio/audioldm2" \
  --max-workers "$DOWNLOAD_MAX_WORKERS"

echo "Wan V2 model files are ready under $MODEL_ROOT"
