#!/usr/bin/env bash
set -euo pipefail

export PATH="$HOME/.local/bin:$HOME/.bun/bin:$HOME/bin:$PATH"

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TARGET_HOME="${OPEN_CLOUD_HOME:-$HOME}"
STATE_DIR="${OPEN_CLOUD_STATE_DIR:-$TARGET_HOME/.opencloud/state}"
MARKER="$STATE_DIR/hermes-vellum-installed"
CONFIG="${OPEN_CLOUD_HERMES_CONFIG:-$TARGET_HOME/.hermes/config.yaml}"
SERVER="$TARGET_HOME/.config/hermes-vellum/mcp/server.py"
POLICY="$ROOT/config/hermes/orchestration.json"

PY="${OPEN_CLOUD_HERMES_PYTHON:-$HOME/.hermes/hermes-agent/venv/bin/python}"
if [ ! -x "$PY" ]; then
    PY="$(command -v python3)"
fi

if [ ! -f "$MARKER" ]; then
    printf "SKIP  %-24s %s\n" "Hermes/Vellum bridge" "public integration not installed yet"
    printf "SKIP  %-24s %s\n" "Hermes orchestration" "public integration not installed yet"
    exit 0
fi

FAIL=0

if [ -f "$SERVER" ] && grep -qF "def get_user_context" "$SERVER"; then
    printf "PASS  %-24s %s\n" "Hermes/Vellum bridge" "installed"
else
    printf "FAIL  %-24s %s\n" "Hermes/Vellum bridge" "server missing or invalid"
    FAIL=1
fi

if "$PY" "$ROOT/scripts/hermes-config.py" verify \
    --config "$CONFIG" \
    --policy "$POLICY" \
    --server "$SERVER" \
    --python "$PY" >/dev/null 2>&1
then
    printf "PASS  %-24s %s\n" "Hermes orchestration" "4 children / depth 2 / bounded children / final-only iMessage"
else
    printf "FAIL  %-24s %s\n" "Hermes orchestration" "configuration mismatch"
    FAIL=1
fi

exit "$FAIL"
