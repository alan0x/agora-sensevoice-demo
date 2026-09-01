#!/usr/bin/env bash
set -euo pipefail

OMINIX_DIR="${OMINIX_DIR:-/Users/alan0x/Documents/projects/OminiX-API}"
OMINIX_MODEL_DIR="${OMINIX_MODEL_DIR:-/Users/alan0x/.OminiX/models/qwen3-asr-1.7b}"
OMINIX_PORT="${OMINIX_PORT:-8080}"
OMINIX_BIN="$OMINIX_DIR/target/release/ominix-api"

if [[ ! -x "$OMINIX_BIN" ]]; then
  echo "Missing $OMINIX_BIN. Run 'cargo build --release' in $OMINIX_DIR first." >&2
  exit 1
fi

if [[ ! -f "$OMINIX_MODEL_DIR/config.json" ]]; then
  echo "Qwen3-ASR model not found at $OMINIX_MODEL_DIR." >&2
  exit 1
fi

exec "$OMINIX_BIN" \
  --asr-model "$OMINIX_MODEL_DIR" \
  --port "$OMINIX_PORT"
