#!/usr/bin/env bash
# Start a pool of OminiX-API instances on consecutive local ports.
#
# Sizing notes (from upstream benchmarks on M5 Max 128GB, Qwen3-ASR 1.7B 4-bit,
# ASR_MODE=conversational): a single batched process holds ~70 concurrent
# sessions at p50 ~490ms. For a 50-session target, 2 instances give comfortable
# headroom; use OMINIX_POOL_SIZE=3 to leave margin for bursts.
set -euo pipefail

OMINIX_MODEL_DIR="${OMINIX_MODEL_DIR:-$HOME/.OminiX/models/Qwen3-ASR-1.7B-4bit}"
OMINIX_BIN="${OMINIX_BIN:-}"
OMINIX_POOL_SIZE="${OMINIX_POOL_SIZE:-2}"
OMINIX_BASE_PORT="${OMINIX_BASE_PORT:-8080}"
OMINIX_LOG_DIR="${OMINIX_LOG_DIR:-$(pwd)/logs/ominix}"
# Batching mode: off | interactive | conversational | offline.
ASR_MODE="${ASR_MODE:-conversational}"

if [[ -z "$OMINIX_BIN" ]]; then
  if [[ -x "$HOME/Documents/projects/OminiX-API/target/release/ominix-api" ]]; then
    OMINIX_BIN="$HOME/Documents/projects/OminiX-API/target/release/ominix-api"
  else
    OMINIX_BIN="$(command -v ominix-api || true)"
  fi
fi

if [[ -z "$OMINIX_BIN" || ! -x "$OMINIX_BIN" ]]; then
  echo "ominix-api binary not found. Either run the official install.sh, or set OMINIX_BIN." >&2
  exit 1
fi

if [[ ! -f "$OMINIX_MODEL_DIR/config.json" ]]; then
  echo "Qwen3-ASR model not found at $OMINIX_MODEL_DIR." >&2
  echo "Download it with:" >&2
  echo "  huggingface-cli download mlx-community/Qwen3-ASR-1.7B-4bit --local-dir $OMINIX_MODEL_DIR" >&2
  exit 1
fi

if (( OMINIX_POOL_SIZE < 1 || OMINIX_POOL_SIZE > 32 )); then
  echo "OMINIX_POOL_SIZE must be between 1 and 32." >&2
  exit 1
fi

mkdir -p "$OMINIX_LOG_DIR"

pids=()
for (( i = 0; i < OMINIX_POOL_SIZE; i++ )); do
  port=$((OMINIX_BASE_PORT + i))
  echo "Starting OminiX-API on 127.0.0.1:${port} (ASR_MODE=$ASR_MODE, log: $OMINIX_LOG_DIR/ominix-${port}.log)"
  PORT="$port" \
  ASR_MODEL_DIR="$OMINIX_MODEL_DIR" \
  ASR_MODE="$ASR_MODE" \
    "$OMINIX_BIN" \
    >"$OMINIX_LOG_DIR/ominix-${port}.log" 2>&1 &
  pids+=("$!")
done

cleanup() {
  echo "Stopping OminiX pool..."
  kill "${pids[@]}" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

echo "OminiX pool of $OMINIX_POOL_SIZE instances starting; press Ctrl+C to stop all."
wait
