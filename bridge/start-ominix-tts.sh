#!/usr/bin/env bash
# Start a dedicated OminiX-API instance for Qwen3-TTS (preset voices).
# Keep TTS on its own port/instance: a long TTS generation would otherwise
# block ASR inference on a shared instance (single inference thread upstream).
set -euo pipefail

TTS_MODEL_DIR="${TTS_MODEL_DIR:-$HOME/.OminiX/models/Qwen3-TTS-12Hz-1.7B-CustomVoice-8bit}"
TTS_PORT="${TTS_PORT:-8090}"
OMINIX_BIN="${OMINIX_BIN:-}"

if [[ -z "$OMINIX_BIN" ]]; then
  if [[ -x "$HOME/Documents/projects/OminiX-API/target/release/ominix-api" ]]; then
    OMINIX_BIN="$HOME/Documents/projects/OminiX-API/target/release/ominix-api"
  else
    OMINIX_BIN="$(command -v ominix-api || true)"
  fi
fi

if [[ -z "$OMINIX_BIN" || ! -x "$OMINIX_BIN" ]]; then
  echo "ominix-api binary not found. Run the official install.sh, build from source, or set OMINIX_BIN." >&2
  exit 1
fi

if [[ ! -f "$TTS_MODEL_DIR/config.json" ]]; then
  echo "Qwen3-TTS model not found at $TTS_MODEL_DIR." >&2
  echo "Download it with:" >&2
  echo "  hf download mlx-community/Qwen3-TTS-12Hz-1.7B-CustomVoice-8bit --local-dir $TTS_MODEL_DIR" >&2
  exit 1
fi

exec env PORT="$TTS_PORT" QWEN3_TTS_MODEL_DIR="$TTS_MODEL_DIR" "$OMINIX_BIN"
