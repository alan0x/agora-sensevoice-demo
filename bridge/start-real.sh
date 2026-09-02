#!/usr/bin/env bash
set -euo pipefail

BRIDGE_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd "$BRIDGE_DIR"

if [[ ! -f .env ]]; then
  echo "Missing bridge/.env. Copy .env.example to .env and fill the VPS URL and shared secret." >&2
  exit 1
fi

if [[ ! -x .venv/bin/python ]]; then
  echo "Missing bridge/.venv. Recreate it with the Python 3.13 command in README.md." >&2
  exit 1
fi

set -a
# shellcheck disable=SC1091
source .env
set +a

if [[ "${CONTROL_WS_URL:-}" == *"example.com"* ]]; then
  echo "CONTROL_WS_URL still contains the example domain." >&2
  exit 1
fi

if [[ -z "${BRIDGE_SHARED_SECRET:-}" || "${BRIDGE_SHARED_SECRET}" == replace-* ]]; then
  echo "BRIDGE_SHARED_SECRET is not configured." >&2
  exit 1
fi

ASR_HEALTH_URL="${ASR_HEALTH_URL:-http://127.0.0.1:8080/health}"
ASR_STARTUP_WAIT_SECONDS="${ASR_STARTUP_WAIT_SECONDS:-120}"
deadline=$((SECONDS + ASR_STARTUP_WAIT_SECONDS))
until curl --fail --silent "$ASR_HEALTH_URL" >/dev/null; do
  if (( SECONDS >= deadline )); then
    echo "ASR health check failed at $ASR_HEALTH_URL after ${ASR_STARTUP_WAIT_SECONDS}s." >&2
    exit 1
  fi
  echo "Waiting for OminiX ASR at $ASR_HEALTH_URL..." >&2
  sleep 2
done

exec .venv/bin/python -m bridge.main --mode real
