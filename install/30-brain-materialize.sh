#!/usr/bin/env bash
set -euo pipefail

export PATH="$HOME/.local/bin:$HOME/.bun/bin:$HOME/bin:$PATH"

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
HERMES_ROOT="${OPEN_CLOUD_HERMES_ROOT:-$HOME/.hermes/hermes-agent}"
HERMES_BASELINE_REV="3fa318a50c02df8dbd2c55499f5f73d51ad77188"
MODE="${1:---check}"

PATCH_FLEET="$ROOT/integrations/hermes/hermes-fleet-bridge.patch"
PATCH_LIVE="$ROOT/integrations/hermes/hermes-live.patch"
PATCH_CRON="$ROOT/integrations/hermes/hermes-cron-tool-safety.patch"
PATCH_CONT="$ROOT/integrations/hermes/hermes-cron-required-continuation.patch"
PATCH_OUTPUT="$ROOT/integrations/hermes/hermes-cron-output-contract.patch"
PATCH_GUARD="$ROOT/integrations/hermes/hermes-provider-metadata-guard.patch"
PATCH_SCORING="$ROOT/integrations/hermes/hermes-career-deterministic-scoring.patch"
PATCH_REPAIR="$ROOT/integrations/hermes/hermes-opencloud-self-repair.patch"
PATCH_DUP="$ROOT/integrations/hermes/hermes-cron-duplicate-guard.patch"

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
    local patch4="$rendered/hermes-cron-required-continuation.patch"
    local patch5="$rendered/hermes-cron-output-contract.patch"
    local patch6="$rendered/hermes-provider-metadata-guard.patch"
    local patch7="$rendered/hermes-career-deterministic-scoring.patch"
    local patch8="$rendered/hermes-opencloud-self-repair.patch"
    local patch9="$rendered/hermes-cron-duplicate-guard.patch"

    require_file "$PATCH_FLEET"
    require_file "$PATCH_LIVE"
    require_file "$PATCH_CRON"
    require_file "$PATCH_CONT"
    require_file "$PATCH_OUTPUT"
    require_file "$PATCH_GUARD"
    require_file "$PATCH_SCORING"
    require_file "$PATCH_REPAIR"
    require_file "$PATCH_DUP"

    if [ ! -d "$HERMES_ROOT/.git" ]; then
        echo "ERROR: Hermes Git source not found at: $HERMES_ROOT" >&2
        exit 1
    fi

    if ! git -C "$HERMES_ROOT" cat-file -e "${HERMES_BASELINE_REV}^{commit}" 2>/dev/null; then
        echo "ERROR: pinned Hermes baseline is unavailable: $HERMES_BASELINE_REV" >&2
        exit 1
    fi

    rm -rf "$out"
    mkdir -p "$out" "$rendered"

    echo "MATERIALIZE: exporting pinned Hermes baseline $HERMES_BASELINE_REV"

    git -C "$HERMES_ROOT" archive "$HERMES_BASELINE_REV" | tar -x -C "$out"

    render_patch "$PATCH_FLEET" "$patch1"
    render_patch "$PATCH_LIVE" "$patch2"
    render_patch "$PATCH_CRON" "$patch3"
    render_patch "$PATCH_CONT" "$patch4"
    render_patch "$PATCH_OUTPUT" "$patch5"
    render_patch "$PATCH_GUARD" "$patch6"
    render_patch "$PATCH_SCORING" "$patch7"
    render_patch "$PATCH_REPAIR" "$patch8"
    render_patch "$PATCH_DUP" "$patch9"

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

    echo "MATERIALIZE: checking required-operation continuation patch"
    git -C "$out" apply --check "$patch4"
    git -C "$out" apply "$patch4"

    echo "MATERIALIZE: checking output-contract patch"
    git -C "$out" apply --check "$patch5"
    git -C "$out" apply "$patch5"

    echo "MATERIALIZE: checking provider-metadata guard patch"
    git -C "$out" apply --check "$patch6"
    git -C "$out" apply "$patch6"

    echo "MATERIALIZE: checking career deterministic-scoring patch"
    git -C "$out" apply --check "$patch7"
    git -C "$out" apply "$patch7"

    echo "MATERIALIZE: checking self-repair auto-trigger patch"
    git -C "$out" apply --check "$patch8"
    git -C "$out" apply "$patch8"

    echo "MATERIALIZE: checking cron duplicate-guard patch"
    git -C "$out" apply --check "$patch9"
    git -C "$out" apply "$patch9"

    echo "HERMES_INSTALL: applying Routing V1 workload compatibility"
    python3 "$ROOT/integrations/hermes/routing_v1_compat.py" "$out"

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
        HERMES_CRON_REQUIRED_EXECUTION_CONTINUATION_V1 \
        HERMES_CRON_OUTPUT_CONTRACT_V1 \
        HERMES_CRON_RAW_TOOL_PROTOCOL_GUARD_V1 \
        HERMES_CRON_FAILURE_CLASSIFICATION_V1 \
        HERMES_ROUTING_V1_CRON_PROFILE \
        HERMES_CRON_STRICT_SILENT_DELIVERY_V1
    do
        if ! grep -RqsF "$cron_marker" "$out/tools" "$out/cron" "$out/model_tools.py"; then
            echo "ERROR: expected cron tool-safety marker missing after materialization: $cron_marker" >&2
            exit 1
        fi
    done

    if ! grep -RqsF "HERMES_OPENCLOUD_METADATA_GUARD_V1" "$out/agent" "$out/gateway"; then
        echo "ERROR: provider-metadata guard marker missing after materialization" >&2
        exit 1
    fi

    if ! grep -RqsF "HERMES_CRON_DETERMINISTIC_SCORING_V1" "$out/cron"; then
        echo "ERROR: career deterministic-scoring marker missing after materialization" >&2
        exit 1
    fi

    if ! grep -RqsF "HERMES_OPENCLOUD_SELF_REPAIR_V1" "$out/agent" "$out/gateway"; then
        echo "ERROR: self-repair auto-trigger marker missing after materialization" >&2
        exit 1
    fi

    if ! grep -RqsF "HERMES_CRON_DUPLICATE_GUARD_V1" "$out/tools"; then
        echo "ERROR: cron duplicate-guard marker missing after materialization" >&2
        exit 1
    fi

    python3 -m py_compile "$out/agent/hermes_fleet_bridge.py"
    python3 -m py_compile "$out/agent/conversation_loop.py"
    python3 -m py_compile "$out/tools/tool_search.py" "$out/model_tools.py" "$out/cron/scheduler.py" "$out/cron/output_contract.py"
    python3 -m py_compile "$out/agent/provider_metadata_guard.py" "$out/agent/transports/chat_completions.py" "$out/agent/transports/codex.py" "$out/gateway/run.py"
    python3 -m py_compile "$out/agent/opencloud_self_repair.py"
    python3 -m py_compile "$out/tools/cronjob_tools.py"

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
