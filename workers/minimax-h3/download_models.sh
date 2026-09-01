#!/usr/bin/env bash
set -euo pipefail

if [[ -d /runpod-volume ]]; then
  DEFAULT_ROOT=/runpod-volume/models/MiniMax-H3-PinkCherry
else
  DEFAULT_ROOT=/workspace/models/MiniMax-H3-PinkCherry
fi

MODEL_ROOT="${MODEL_ROOT:-$DEFAULT_ROOT}"
DOWNLOAD_MAX_WORKERS="${DOWNLOAD_MAX_WORKERS:-8}"
# PinkCherry replaces the DiT wholesale, so the 66 GB of stock transformer
# shards are dead weight on the volume. Set this to 1 to keep them and run the
# stock-vs-PinkCherry quality A/B from one volume (needs ~67 GB more space).
INCLUDE_STOCK_TRANSFORMER="${INCLUDE_STOCK_TRANSFORMER:-0}"
# The 4-step turbo LoRA is the aggressive speed profile. Wan's 4-step output was
# judged below the quality bar, so 8-step is the default and 4-step is opt-in.
INCLUDE_FAST_TURBO_LORA="${INCLUDE_FAST_TURBO_LORA:-0}"

mkdir -p "$MODEL_ROOT/pinkcherry" "$MODEL_ROOT/turbo-lora"

# FL2VA is the self-contained partition that serves t2va as well as first/last
# frame conditioning: transformer, Qwen3-VL text encoder, video VAE, audio VAE,
# tokenizer, processor and every config. Ref2VA is a separate 144 GB partition
# this lane does not use.
stock_excludes=(--exclude "Ref2VA/*" --exclude "assets/*" --exclude "docs/*" --exclude "scripts/*")
if [[ "$INCLUDE_STOCK_TRANSFORMER" != "1" ]]; then
  # Keep transformer/config.json and the index; drop only the weight shards.
  stock_excludes+=(--exclude "FL2VA/transformer/*.safetensors")
fi

hf download MiniMaxAI/MiniMax-H3 \
  --revision 42ed227ee7df40d41602854ae760620d6eb651fe \
  --include "FL2VA/*" \
  "${stock_excludes[@]}" \
  --local-dir "$MODEL_ROOT" \
  --max-workers "$DOWNLOAD_MAX_WORKERS"

# PinkCherry ships a full fine-tuned DiT whose tensor names and shapes are
# identical to the stock FL2VA transformer, so SGLang loads it through the
# single-file transformer override. Only the bf16 export is usable here: the
# int8_convrot variants carry ComfyUI `comfy_quant` tensors and a pruned AdaLN
# path that SGLang's loader does not implement.
hf download SexGod1979/PinkCherry_MiniMax-H3 \
  beta-0.6-fl2va/PinkCherry_fl2va_MiniMax_H3_bf16_beta-0.6.safetensors \
  --revision bf2fef11d0e55e957f4af997e3beade3362f44b3 \
  --local-dir "$MODEL_ROOT/pinkcherry-src" \
  --max-workers "$DOWNLOAD_MAX_WORKERS"
mv -f "$MODEL_ROOT/pinkcherry-src/beta-0.6-fl2va/PinkCherry_fl2va_MiniMax_H3_bf16_beta-0.6.safetensors" \
      "$MODEL_ROOT/pinkcherry/"
rm -rf "$MODEL_ROOT/pinkcherry-src"

# Plain (non-ComfyUI) exports only: those carry `key_format: minimax-h3-diffusers`
# and the training alpha in safetensors metadata, which is the form the SGLang
# cookbook loads. The `_comfyui_` twins use ComfyUI's fused-QKV key convention.
turbo_files=(minimax_h3_fl2v_turbo_8step_v1.0_768p_bf16.safetensors)
if [[ "$INCLUDE_FAST_TURBO_LORA" == "1" ]]; then
  turbo_files+=(minimax_h3_fl2v_turbo_4step_v1.1_768p_bf16.safetensors)
fi

hf download lightx2v/Minimax-h3-Turbo \
  "${turbo_files[@]}" \
  --revision 05ef678438e84933c406131b59abbf86919b3aac \
  --local-dir "$MODEL_ROOT/turbo-lora" \
  --max-workers "$DOWNLOAD_MAX_WORKERS"

echo "MiniMax H3 model files are ready under $MODEL_ROOT"
