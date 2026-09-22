#!/usr/bin/env bash
# Start a single OminiX-API ASR instance. For concurrent capacity, prefer
# start-ominix-pool.sh.
set -euo pipefail

OMINIX_MODEL_DIR="${OMINIX_MODEL_DIR:-$HOME/.OminiX/models/Qwen3-ASR-1.7B-4bit}"
OMINIX_PORT="${OMINIX_PORT:-8080}"
ASR_MODE="${ASR_MODE:-conversational}"
OMINIX_BIN="${OMINIX_BIN:-$(command -v ominix-api || true)}"

if [[ -z "$OMINIX_BIN" || ! -x "$OMINIX_BIN" ]]; then
  echo "ominix-api binary not found. Run the official install.sh or set OMINIX_BIN." >&2
  exit 1
fi

if [[ ! -f "$OMINIX_MODEL_DIR/config.json" ]]; then
  echo "Qwen3-ASR model not found at $OMINIX_MODEL_DIR." >&2
  echo "Download it with:" >&2
  echo "  huggingface-cli download mlx-community/Qwen3-ASR-1.7B-4bit --local-dir $OMINIX_MODEL_DIR" >&2
  exit 1
fi

exec env PORT="$OMINIX_PORT" ASR_MODEL_DIR="$OMINIX_MODEL_DIR" ASR_MODE="$ASR_MODE" "$OMINIX_BIN"
