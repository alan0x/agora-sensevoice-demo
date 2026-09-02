#!/usr/bin/env bash
set -euo pipefail

PUBLIC_BASE_URL="${PUBLIC_BASE_URL:-https://asr.pitun.cc}"

launchctl print "gui/$UID/com.pitun.ominix-asr" | sed -n '1,24p'
launchctl print "gui/$UID/com.pitun.agora-bridge" | sed -n '1,24p'
curl --fail --silent --show-error http://127.0.0.1:8080/health
echo
curl --fail --silent --show-error "$PUBLIC_BASE_URL/api/v1/status"
echo
