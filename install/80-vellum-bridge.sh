#!/usr/bin/env bash
set -euo pipefail

export PATH="$HOME/.local/bin:$HOME/.bun/bin:$HOME/bin:$PATH"

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TARGET_HOME="${OPEN_CLOUD_HOME:-$HOME}"
SOURCE="$ROOT/integrations/vellum/server.py"
TARGET="$TARGET_HOME/.config/hermes-vellum/mcp/server.py"
STATE="$TARGET_HOME/.config/hermes-vellum/mcp/state"
MODE="${1:---help}"

render() {
    local dst="$1"

    python3 -c "from pathlib import Path; import sys; s=Path(sys.argv[1]).read_text(); s=s.replace(\"__OPEN_CLOUD_HOME__\",sys.argv[3]).replace(\"**OPEN_CLOUD_HOME**\",sys.argv[3]); Path(sys.argv[2]).write_text(s)" "$SOURCE" "$dst" "$TARGET_HOME"
}

validate() {
    local file="$1"

    python3 -m py_compile "$file"

    grep -qF "def get_user_context" "$file"
    grep -qF "def start_vellum_task" "$file"
    grep -qF "def get_vellum_task" "$file"
    grep -qF "def stop_vellum_task" "$file"
    grep -qF "def repair_code" "$file"

    if grep -qF "__OPEN_CLOUD_HOME__" "$file"; then
        echo "ERROR: unresolved OpenCloud home placeholder" >&2
        exit 1
    fi
}

case "$MODE" in
    --check)
        TMP="$(mktemp -d)"
        render "$TMP/server.py"
        validate "$TMP/server.py"
        rm -rf "$TMP"
        echo "VELLUM_BRIDGE_INSTALL_CHECK: PASS"
        ;;

    --install)
        TMP="$(mktemp -d)"
        render "$TMP/server.py"
        validate "$TMP/server.py"

        mkdir -p "$(dirname "$TARGET")" "$STATE"
        chmod 700 "$(dirname "$TARGET")" "$STATE"

        install -m 700 "$TMP/server.py" "$TARGET"
        rm -rf "$TMP"

        echo "VELLUM_BRIDGE_INSTALL: PASS"
        echo "Server: $TARGET"
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
