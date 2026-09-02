#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd -- "$SCRIPT_DIR/../.." && pwd)"
LAUNCH_AGENT_DIR="$HOME/Library/LaunchAgents"
LOG_DIR="$HOME/Library/Logs/agora-ominix-asr"

mkdir -p "$LAUNCH_AGENT_DIR" "$LOG_DIR"

install_agent() {
  local label="$1"
  local template="$SCRIPT_DIR/${label}.plist.example"
  local target="$LAUNCH_AGENT_DIR/${label}.plist"
  sed \
    -e "s|__REPO_ROOT__|$REPO_ROOT|g" \
    -e "s|__LOG_DIR__|$LOG_DIR|g" \
    "$template" >"$target"
  plutil -lint "$target"
  launchctl bootout "gui/$UID" "$target" 2>/dev/null || true
  launchctl bootstrap "gui/$UID" "$target"
  launchctl kickstart -k "gui/$UID/$label"
}

if [[ ! -f "$REPO_ROOT/bridge/.env" ]]; then
  echo "Missing $REPO_ROOT/bridge/.env; configure it before installing agents." >&2
  exit 1
fi
chmod 600 "$REPO_ROOT/bridge/.env"

install_agent com.pitun.ominix-asr
install_agent com.pitun.agora-bridge

echo "Installed OminiX and Agora Bridge LaunchAgents."
echo "Logs: $LOG_DIR"
