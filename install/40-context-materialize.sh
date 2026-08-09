#!/usr/bin/env bash
set -euo pipefail

export PATH="$HOME/.local/bin:$HOME/.bun/bin:$HOME/bin:$PATH"

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SOURCE="$ROOT/integrations/vellum/mcp-managed-blocks.py"
MODE="${1:---check}"

usage() {
    echo "Usage:"
    echo "  $0 --check"
    echo "  $0 --stage DIRECTORY"
}

render_context() {
    local dst="$1"

    mkdir -p "$(dirname "$dst")"

    python3 -c '
from pathlib import Path
import os, sys
src = Path(sys.argv[1])
dst = Path(sys.argv[2])
text = src.read_text()
text = text.replace("__OPEN_CLOUD_HOME__", os.path.expanduser("~"))
dst.write_text(text)
' "$SOURCE" "$dst"

    if grep -qF "__OPEN_CLOUD_HOME__" "$dst"; then
        echo "ERROR: unresolved Open Cloud home placeholder" >&2
        exit 1
    fi

    grep -qF "get_user_context" "$dst"
    grep -qF "memory" "$dst"
    grep -qF "items" "$dst"
    grep -qF "list" "$dst"
    grep -qF -- "--json" "$dst"
    python3 -m py_compile "$dst"

    echo "VELLUM_CONTEXT_MATERIALIZATION: PASS"
}

case "$MODE" in
    --check)
        [ -f "$SOURCE" ] || {
            echo "ERROR: missing context integration source: $SOURCE" >&2
            exit 1
        }
        TMP="$(mktemp -d)"
        trap 'rm -rf "$TMP"' EXIT
        render_context "$TMP/mcp-managed-blocks.py"
        ;;
    --stage)
        [ "$#" -eq 2 ] || {
            usage >&2
            exit 2
        }
        [ -f "$SOURCE" ] || {
            echo "ERROR: missing context integration source: $SOURCE" >&2
            exit 1
        }
        render_context "$2/mcp-managed-blocks.py"
        echo "Staged context: $2/mcp-managed-blocks.py"
        ;;
    --help|-h|help)
        usage
        ;;
    *)
        echo "ERROR: unknown mode: $MODE" >&2
        usage >&2
        exit 2
        ;;
esac
