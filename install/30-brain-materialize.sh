#!/usr/bin/env bash
set -euo pipefail

export PATH="$HOME/.local/bin:$HOME/.bun/bin:$HOME/bin:$PATH"

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
HERMES_ROOT="${OPEN_CLOUD_HERMES_ROOT:-$HOME/.hermes/hermes-agent}"
MODE="${1:---check}"

PATCH_FLEET="$ROOT/integrations/hermes/hermes-fleet-bridge.patch"
PATCH_LIVE="$ROOT/integrations/hermes/hermes-live.patch"
PATCH_CRON="$ROOT/integrations/hermes/hermes-cron-tool-safety.patch"

usage() {
    echo "Usage:"
    echo "  $0 --check"
    echo "  $0 --stage DIRECTORY"
}

require_file() {
    [ -f "$1" ] || {
        echo "ERROR: missing required file: $1" >&2
        exit 1
    }
}

render_patch() {
    local src="$1"
    local dst="$2"

    python3 -c '
from pathlib import Path
import os, sys
src = Path(sys.argv[1])
dst = Path(sys.argv[2])
text = src.read_text()
text = text.replace("__OPEN_CLOUD_HOME__", os.path.expanduser("~"))
dst.write_text(text)
' "$src" "$dst"
}

materialize() {
    local out="$1"
    local keep="$2"
    local rendered="$out/.opencloud-rendered"
    local patch1="$rendered/hermes-fleet-bridge.patch"
    local patch2="$rendered/hermes-live.patch"
    local patch3="$rendered/hermes-cron-tool-safety.patch"

    require_file "$PATCH_FLEET"
    require_file "$PATCH_LIVE"
    require_file "$PATCH_CRON"

    if [ ! -d "$HERMES_ROOT/.git" ]; then
        echo "ERROR: Hermes Git source not found at: $HERMES_ROOT" >&2
        exit 1
    fi

    rm -rf "$out"
    mkdir -p "$out" "$rendered"

    echo "MATERIALIZE: exporting clean Hermes baseline from current local HEAD"

    git -C "$HERMES_ROOT" archive HEAD | tar -x -C "$out"

    render_patch "$PATCH_FLEET" "$patch1"
    render_patch "$PATCH_LIVE" "$patch2"
    render_patch "$PATCH_CRON" "$patch3"

    if grep -RqsF "__OPEN_CLOUD_HOME__" "$rendered"; then
        echo "ERROR: unresolved Open Cloud home placeholder" >&2
        exit 1
    fi


    echo "MATERIALIZE: checking Fleet bridge patch"
    git -C "$out" apply --check "$patch1"
    git -C "$out" apply "$patch1"

    echo "MATERIALIZE: checking live Hermes orchestration patch"
    git -C "$out" apply --check "$patch2"
    git -C "$out" apply "$patch2"

    echo "MATERIALIZE: checking cron tool-safety patch"
    git -C "$out" apply --check "$patch3"
    git -C "$out" apply "$patch3"

    test -f "$out/agent/hermes_fleet_bridge.py"

    for marker in \
        HERMES_FLEET_MAIN_ATTACH_BEGIN \
        HERMES_FLEET_WORKER_ATTACH_BEGIN \
        HERMES_FLEET_FAILURE_ATTACH_BEGIN \
        HERMES_FLEET_FALLBACK_SKIP_BEGIN \
        HERMES_FLEET_GEMINI_UNVERIFIED_GUARD_V1
    do
        if ! grep -RqsF "$marker" "$out/agent" "$out/tools"; then
            echo "ERROR: expected Hermes marker missing after materialization: $marker" >&2
            exit 1
        fi
    done

    for cron_marker in \
        HERMES_CRON_REQUIRED_TOOLS_PROTECT_V1 \
        HERMES_CRON_REQUIRED_TOOLS_CACHE_KEY_V1 \
        HERMES_CRON_REQUIRED_TOOLS_RESOLVE_V1 \
        HERMES_CRON_REQUIRED_TO_EXECUTE_V1 \
        HERMES_CRON_RAW_TOOL_PROTOCOL_GUARD_V1 \
        HERMES_CRON_FAILURE_CLASSIFICATION_V1
    do
        if ! grep -RqsF "$cron_marker" "$out/tools" "$out/cron" "$out/model_tools.py"; then
            echo "ERROR: expected cron tool-safety marker missing after materialization: $cron_marker" >&2
            exit 1
        fi
    done

    python3 -m py_compile "$out/agent/hermes_fleet_bridge.py"
    python3 -m py_compile "$out/tools/tool_search.py" "$out/model_tools.py" "$out/cron/scheduler.py"

    rm -rf "$rendered"

    echo "HERMES_BRAIN_MATERIALIZATION: PASS"

    if [ "$keep" = "yes" ]; then
        echo "Staged tree: $out"
    fi
}

case "$MODE" in
    --check)
        TMP="$(mktemp -d)"
        trap 'rm -rf "$TMP"' EXIT
        materialize "$TMP/hermes" no
        ;;
    --stage)
        [ "$#" -eq 2 ] || {
            usage >&2
            exit 2
        }
        materialize "$2" yes
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
