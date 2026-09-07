#!/usr/bin/env bash
# h3chain boot — AutoDL Art app entrypoint: ComfyUI (6006) + h3chain web (6008)
# Idempotent: safe to re-run (boot script re-invokes on restart).
set -uo pipefail

H3CHAIN_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
COMFYUI_DIR="${COMFYUI_DIR:-/root/ComfyUI}"
LOG_DIR="${LOG_DIR:-/tmp/h3chain-logs}"
mkdir -p "$LOG_DIR"

log() { echo "[boot] $*" | tee -a "$LOG_DIR/boot.log"; }

# Dry-run mode: link weights, report status, do not start/wait services.
# (used by CI/tests: H3CHAIN_BOOT_DRY=1 bash boot.sh)
if [ "${H3CHAIN_BOOT_DRY:-0}" = "1" ]; then
  DRY=1
else
  DRY=0
fi

# ---------------------------------------------------------- 1. weight symlinks
# Public-model symlinks: point at /.autodl public models when present.
# If a target is missing, print download guidance (boundary: no silent failure).
link_model() {
  local link="$1" target="$2"
  if [ -e "$link" ]; then
    return 0
  fi
  if [ -e "$target" ]; then
    mkdir -p "$(dirname "$link")"
    ln -s "$target" "$link"
    log "linked $link -> $target"
  else
    log "WARN: weight not found: $target"
    log "      download it to $target (see README: hf-mirror guidance),"
    log "      or place your own weights and update story.json models{}."
  fi
}

UNET_DIR="$COMFYUI_DIR/models/unet"
LORA_DIR="$COMFYUI_DIR/models/loras"
CLIP_DIR="$COMFYUI_DIR/models/text_encoders"
VAE_DIR="$COMFYUI_DIR/models/vae"
mkdir -p "$UNET_DIR" "$LORA_DIR" "$CLIP_DIR" "$VAE_DIR"

link_model "$UNET_DIR/minimax_h3_ref2va_pruned_int8_convrot.safetensors" \
           "/.autodl/public/models/minimax_h3_ref2va_pruned_int8_convrot.safetensors"
link_model "$LORA_DIR/lightx2v-minimax_h3_fl2v_turbo_4step_v0.1.safetensors" \
           "/.autodl/public/models/lightx2v-minimax_h3_fl2v_turbo_4step_v0.1.safetensors"
link_model "$CLIP_DIR/qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors" \
           "/.autodl/public/models/qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors"
link_model "$VAE_DIR/minimax_h3_video_vae_fp16.safetensors" \
           "/.autodl/public/models/minimax_h3_video_vae_fp16.safetensors"
link_model "$VAE_DIR/minimax_h3_audio_vae_fp32.safetensors" \
           "/.autodl/public/models/minimax_h3_audio_vae_fp32.safetensors"

# ---------------------------------------------------------- 2. ComfyUI on 6006
if [ "$DRY" = "1" ]; then
  log "dry-run: skipping service start"
  exit 0
fi
if curl -sf http://127.0.0.1:6006/system_stats > /dev/null 2>&1; then
  log "ComfyUI already running on 6006"
else
  if [ -d "$COMFYUI_DIR" ]; then
    log "starting ComfyUI on 6006..."
    (cd "$COMFYUI_DIR" && nohup python3 main.py --port 6006 \
       >> "$LOG_DIR/comfyui.log" 2>&1 &)
  else
    log "WARN: ComfyUI not found at $COMFYUI_DIR"
  fi
fi

# ---------------------------------------------------------- 3. h3chain on 6008
if curl -sf http://127.0.0.1:6008/api/projects > /dev/null 2>&1; then
  log "h3chain already running on 6008"
else
  log "starting h3chain on 6008..."
  (cd "$H3CHAIN_DIR" && nohup python3 -m uvicorn server:app \
     --host 0.0.0.0 --port 6008 \
     >> "$LOG_DIR/h3chain.log" 2>&1 &)
fi

# ---------------------------------------------------------- 4. wait for both
for i in $(seq 1 120); do
  C6=$(curl -sf -o /dev/null -w '%{http_code}' http://127.0.0.1:6006/system_stats 2>/dev/null || true)
  C8=$(curl -sf -o /dev/null -w '%{http_code}' http://127.0.0.1:6008/api/projects 2>/dev/null || true)
  if [ "$C6" = "200" ] && [ "$C8" = "200" ]; then
    log "READY: ComfyUI 6006 ✓  h3chain 6008 ✓"
    exit 0
  fi
  sleep 5
done
log "WARN: services not fully ready after 600s — check logs in $LOG_DIR"
exit 1
