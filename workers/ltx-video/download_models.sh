#!/usr/bin/env bash
set -euo pipefail

if [[ -z "${MODEL_VOLUME_DIR:-}" ]]; then
  if [[ -d /runpod-volume ]]; then
    MODEL_VOLUME_DIR=/runpod-volume/models/PinkCherry-LTX-2.3-v1.8
  elif [[ -d /workspace ]]; then
    MODEL_VOLUME_DIR=/workspace/models/PinkCherry-LTX-2.3-v1.8
  else
    echo "Set MODEL_VOLUME_DIR: neither /runpod-volume nor /workspace exists." >&2
    exit 1
  fi
fi
PINKCHERRY_REVISION="${PINKCHERRY_REVISION:-aa5e3192dacd58e8c807e4198e3fcecc53db9f80}"
LTX_REVISION="${LTX_REVISION:-6b5a83e3045eaf8e46cfa0acce512412aa2b9cce}"
GEMMA_REVISION="${GEMMA_REVISION:-68f7ee4fbd59087436ada77ed2d62f373fdd4482}"
DOWNLOAD_MAX_WORKERS="${DOWNLOAD_MAX_WORKERS:-8}"
DOWNLOAD_PARALLEL_REPOS="${DOWNLOAD_PARALLEL_REPOS:-1}"
export HF_HUB_DOWNLOAD_TIMEOUT="${HF_HUB_DOWNLOAD_TIMEOUT:-120}"
export HF_XET_HIGH_PERFORMANCE="${HF_XET_HIGH_PERFORMANCE:-1}"

if ! command -v hf >/dev/null 2>&1; then
  echo "Missing Hugging Face CLI (hf). Install huggingface_hub first." >&2
  exit 1
fi

verify_sha256() {
  local expected="$1"
  local path="$2"
  local actual
  if command -v sha256sum >/dev/null 2>&1; then
    actual="$(sha256sum "$path" | awk '{print $1}')"
  else
    actual="$(shasum -a 256 "$path" | awk '{print $1}')"
  fi
  if [[ "$actual" != "$expected" ]]; then
    echo "SHA256 mismatch for $path: expected $expected, got $actual" >&2
    exit 1
  fi
}

mkdir -p "$MODEL_VOLUME_DIR"

download_pinkcherry() {
  hf download SexGod1979/PinkCherry_NSFW_LTX23 \
    v1.8/PinkCherry_FineTune_bf16_v1_8_LTX23.safetensors \
    --revision "$PINKCHERRY_REVISION" \
    --max-workers "$DOWNLOAD_MAX_WORKERS" \
    --local-dir "$MODEL_VOLUME_DIR"
}

download_ltx() {
  hf download Lightricks/LTX-2.3 \
    ltx-2.3-22b-distilled-lora-384-1.1.safetensors \
    ltx-2.3-spatial-upscaler-x2-1.1.safetensors \
    --revision "$LTX_REVISION" \
    --max-workers "$DOWNLOAD_MAX_WORKERS" \
    --local-dir "$MODEL_VOLUME_DIR"
}

download_gemma() {
  # This repository is gated. Accept its terms once and provide a read-only HF_TOKEN.
  hf download google/gemma-3-12b-it-qat-q4_0-unquantized \
    --revision "$GEMMA_REVISION" \
    --max-workers "$DOWNLOAD_MAX_WORKERS" \
    --local-dir "$MODEL_VOLUME_DIR/gemma-3-12b"
}

if [[ "$DOWNLOAD_PARALLEL_REPOS" == "1" ]]; then
  download_pinkcherry & pinkcherry_pid=$!
  download_ltx & ltx_pid=$!
  download_gemma & gemma_pid=$!

  download_failed=0
  wait "$pinkcherry_pid" || download_failed=1
  wait "$ltx_pid" || download_failed=1
  wait "$gemma_pid" || download_failed=1
  if [[ "$download_failed" -ne 0 ]]; then
    echo "One or more model downloads failed. Rerun with DOWNLOAD_PARALLEL_REPOS=0 on a low-memory host." >&2
    exit 1
  fi
else
  download_pinkcherry
  download_ltx
  download_gemma
fi

verify_sha256 \
  c3fa92ab8a81c4a47b36f0fc85720b6d0c1181d0af9f22cda25c9334ceac3ca0 \
  "$MODEL_VOLUME_DIR/v1.8/PinkCherry_FineTune_bf16_v1_8_LTX23.safetensors"
verify_sha256 \
  f5d4953f3386197a4b4f5abdb17616ff256171e8075c111d6e7d2dfa6e823b3a \
  "$MODEL_VOLUME_DIR/ltx-2.3-22b-distilled-lora-384-1.1.safetensors"
verify_sha256 \
  5f416311fa8172b65af67530758964708d29a317b830d689a51143b7f91913ed \
  "$MODEL_VOLUME_DIR/ltx-2.3-spatial-upscaler-x2-1.1.safetensors"

for shard in 1 2 3 4 5; do
  printf -v shard_name 'model-%05d-of-00005.safetensors' "$shard"
  test -s "$MODEL_VOLUME_DIR/gemma-3-12b/$shard_name"
done

echo "PinkCherry LTX 2.3 v1.8 models are ready at $MODEL_VOLUME_DIR"
du -sh "$MODEL_VOLUME_DIR"
