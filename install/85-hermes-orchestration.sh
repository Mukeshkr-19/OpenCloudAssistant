#!/usr/bin/env bash
set -euo pipefail

export PATH="$HOME/.local/bin:$HOME/.bun/bin:$HOME/bin:$PATH"

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TARGET_HOME="${OPEN_CLOUD_HOME:-$HOME}"
HERMES_SOURCE="${OPEN_CLOUD_HERMES_SOURCE:-$HOME/.hermes/hermes-agent}"
CONFIG="${OPEN_CLOUD_HERMES_CONFIG:-$TARGET_HOME/.hermes/config.yaml}"
SERVER="$TARGET_HOME/.config/hermes-vellum/mcp/server.py"
POLICY="$ROOT/config/hermes/orchestration.json"
STATE_DIR="${OPEN_CLOUD_STATE_DIR:-$TARGET_HOME/.opencloud/state}"
MODE="${1:---help}"

HERMES_PYTHON="${OPEN_CLOUD_HERMES_PYTHON:-$HOME/.hermes/hermes-agent/venv/bin/python}"

if [ ! -x "$HERMES_PYTHON" ]; then
    HERMES_PYTHON="$(command -v python3)"
fi

check_python() {
    "$HERMES_PYTHON" -c "import yaml"
}

check_source() {
    test -f "$HERMES_SOURCE/tools/delegate_tool.py"
    grep -qF "max_concurrent_children" "$HERMES_SOURCE/tools/delegate_tool.py"
    grep -qF "max_spawn_depth" "$HERMES_SOURCE/tools/delegate_tool.py"
    grep -qF "orchestrator_enabled" "$HERMES_SOURCE/tools/delegate_tool.py"
    grep -qF "inherit_mcp_toolsets" "$HERMES_SOURCE/tools/delegate_tool.py"
}

check_policy() {
    python3 -m json.tool "$POLICY" >/dev/null
    python3 -c "
import json,sys
d=json.load(open(sys.argv[1]))
assert d[\"orchestrator_enabled\"] is True
assert d[\"max_concurrent_children\"] == 3
assert d[\"max_iterations\"] == 50
assert d[\"max_spawn_depth\"] == 1
assert d[\"inherit_mcp_toolsets\"] is True
assert \"vellum-bridge\" in d[\"required_mcp_toolsets\"]
" "$POLICY"
}

case "$MODE" in
    --check)
        check_python
        check_source
        check_policy
        test -x "$ROOT/scripts/hermes-config.py"
        echo "HERMES_ORCHESTRATION_INSTALL_CHECK: PASS"
        ;;

    --install)
        check_python
        check_source
        check_policy

        test -f "$SERVER" || {
            echo "ERROR: Vellum bridge is not installed: $SERVER" >&2
            exit 1
        }

        "$HERMES_PYTHON" "$ROOT/scripts/hermes-config.py" apply \
            --config "$CONFIG" \
            --policy "$POLICY" \
            --server "$SERVER" \
            --python "$HERMES_PYTHON"

        mkdir -p "$STATE_DIR"
        chmod 700 "$STATE_DIR"
        printf "%s\n" "version=1" > "$STATE_DIR/hermes-vellum-installed"
        chmod 600 "$STATE_DIR/hermes-vellum-installed"

        echo "HERMES_ORCHESTRATION_INSTALL: PASS"
        ;;

    -h|--help|help)
        echo "Usage:"
        echo "  $0 --check"
        echo "  $0 --install"
        ;;

    *)
        echo "ERROR: unknown mode: $MODE" >&2
        exit 2
        ;;
esac
