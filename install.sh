#!/usr/bin/env bash
# h3chain environment installer — for a ComfyUI instance (e.g. AutoDL)
# Usage: bash install.sh [COMFYUI_PATH]     (default /root/ComfyUI)
set -euo pipefail

COMFYUI_PATH="${1:-/root/ComfyUI}"
CUSTOM_NODES="$COMFYUI_PATH/custom_nodes"
MODELS="$COMFYUI_PATH/models"

echo "=== h3chain installer ==="
echo "ComfyUI: $COMFYUI_PATH"

[ -d "$COMFYUI_PATH" ] || { echo "ComfyUI not found at $COMFYUI_PATH"; exit 1; }

# GitHub acceleration (AutoDL: source /etc/network_turbo; others: skip silently)
if [ -f /etc/network_turbo ]; then source /etc/network_turbo; echo "network_turbo: on"; fi
# Prefer HF mirror when huggingface.co is unreachable (CN networks)
export HF_ENDPOINT="${HF_ENDPOINT:-https://hf-mirror.com}"

PY=python3
command -v "$PY" >/dev/null || PY=/root/miniconda3/bin/python3

# --- 1. custom nodes -------------------------------------------------------
install_node () {  # install_node <git-url> <dir-name>
  local url="$1" dir="$2"
  if [ -d "$CUSTOM_NODES/$dir" ]; then
    echo "[ok] $dir already present"
  else
    echo "[..] cloning $dir"
    git clone --depth 1 "$url" "$CUSTOM_NODES/$dir"
  fi
  if [ -f "$CUSTOM_NODES/$dir/requirements.txt" ]; then
    "$PY" -m pip install -q -r "$CUSTOM_NODES/$dir/requirements.txt" || \
      echo "[warn] pip warnings for $dir (continuing)"
  fi
}

install_node https://github.com/tritant/ComfyUI_MiniMax_H3_Extender.git ComfyUI_MiniMax_H3_Extender
install_node https://github.com/kijai/ComfyUI-KJNodes.git ComfyUI-KJNodes

# --- 2. sageattention (speed) ---------------------------------------------
if "$PY" -c "import sageattention" 2>/dev/null; then
  echo "[ok] sageattention already installed"
else
  echo "[..] trying pip install sageattention"
  "$PY" -m pip install -q sageattention || \
    echo "[warn] sageattention install failed — h3chain still works without it (slower)."
  echo "       matching wheels: https://github.com/woct0rdho/SageAttention/releases"
fi

# --- 3. model weights (quantized 24G set) ----------------------------------
download_model () {  # download_model <hf-repo> <remote-path> <local-dir>
  local repo="$1" rel="$2" dir="$3" name
  name="$(basename "$rel")"
  mkdir -p "$dir"
  if [ -f "$dir/$name" ]; then echo "[ok] $name present"; return; fi
  echo "[..] downloading $name (~${4:-?})"
  if command -v hf >/dev/null; then
    hf download "$repo" "$rel" --local-dir "$MODELS/_dl_tmp" >/dev/null
    mv "$MODELS/_dl_tmp/$rel" "$dir/$name"
  else
    "$PY" -m pip install -q -U "huggingface_hub[cli]" || true
    hf download "$repo" "$rel" --local-dir "$MODELS/_dl_tmp" >/dev/null
    mv "$MODELS/_dl_tmp/$rel" "$dir/$name"
  fi
}

echo "--- model weights (quantized, ~42 GB total; skip with --no-models) ---"
if [ "${1:-}" != "--no-models" ]; then
  download_model Comfy-Org/MiniMax-H3 "diffusion_models/minimax_h3_ref2va_pruned_int8_convrot.safetensors" "$MODELS/diffusion_models" 20GB
  download_model Comfy-Org/MiniMax-H3 "text_encoders/qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors" "$MODELS/text_encoders" 15GB
  download_model Comfy-Org/MiniMax-H3 "vae/minimax_h3_video_vae_fp16.safetensors" "$MODELS/vae" 5GB
  download_model Comfy-Org/MiniMax-H3 "vae/minimax_h3_audio_vae_fp32.safetensors" "$MODELS/vae" 0.6GB
  download_model Comfy-Org/MiniMax-H3 "loras/minimax_h3_ref2v_turbo_4step_v0.1_comfyui_bf16.safetensors" "$MODELS/loras" 1.8GB
fi

# --- 4. rename LoRA into the minimax/ layout h3chain expects ---------------
mkdir -p "$MODELS/loras/minimax" "$MODELS/diffusion_models/minimax"
[ -f "$MODELS/loras/minimax/lightx2v-minimax_h3_fl2v_turbo_4step_v0.1.safetensors" ] || \
  ln -sf "$MODELS/loras/minimax_h3_ref2v_turbo_4step_v0.1_comfyui_bf16.safetensors" \
         "$MODELS/loras/minimax/lightx2v-minimax_h3_fl2v_turbo_4step_v0.1.safetensors" 2>/dev/null || true

echo ""
echo "=== done ==="
echo "next: bash boot.sh   # starts ComfyUI (6006) + h3chain web UI (6008)"
echo "      open http://<host>:6008/ in your browser"
