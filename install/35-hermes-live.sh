#!/usr/bin/env bash
set -euo pipefail

export PATH="$HOME/.local/bin:$HOME/.bun/bin:$HOME/bin:$PATH"

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TARGET_HOME="${OPEN_CLOUD_HOME:-$HOME}"
HERMES_ROOT="${OPEN_CLOUD_HERMES_ROOT:-$TARGET_HOME/.hermes/hermes-agent}"
PATCH1="$ROOT/integrations/hermes/hermes-fleet-bridge.patch"
PATCH2="$ROOT/integrations/hermes/hermes-live.patch"
PATCH3="$ROOT/integrations/hermes/hermes-cron-tool-safety.patch"
BACKUP_ROOT="$TARGET_HOME/.opencloud/backups"
MODE="${1:---check}"

FILES="agent/agent_init.py agent/agent_runtime_helpers.py agent/auxiliary_client.py agent/chat_completion_helpers.py tools/delegate_tool.py tools/daemon_pool.py tools/tool_search.py cron/scheduler.py gateway/run.py model_tools.py agent/hermes_fleet_bridge.py"
MARKERS="HERMES_FLEET_MAIN_ATTACH_BEGIN HERMES_FLEET_WORKER_ATTACH_BEGIN HERMES_FLEET_FAILURE_ATTACH_BEGIN HERMES_FLEET_FALLBACK_SKIP_BEGIN HERMES_FLEET_GEMINI_UNVERIFIED_GUARD_V1 HERMES_CRON_REQUIRED_TOOLS_PROTECT_V1"

require_source() {
    test -d "$HERMES_ROOT/.git" || {
        echo "ERROR: Hermes Git source not found at $HERMES_ROOT" >&2
        exit 1
    }

    test -f "$PATCH1" || {
        echo "ERROR: Hermes Fleet bridge patch missing" >&2
        exit 1
    }

    test -f "$PATCH2" || {
        echo "ERROR: Hermes live patch missing" >&2
        exit 1
    }

    test -f "$PATCH3" || {
        echo "ERROR: Hermes cron tool-safety patch missing" >&2
        exit 1
    }
}

compile_file() {
    local file="$1"
    python3 -c "import pathlib,sys; p=pathlib.Path(sys.argv[1]); compile(p.read_text(),str(p),\"exec\")" "$file"
}

validate_tree() {
    local tree="$1"
    local marker rel

    test -f "$tree/agent/hermes_fleet_bridge.py" || {
        echo "ERROR: Hermes Fleet bridge file missing" >&2
        return 1
    }

    for marker in $MARKERS; do
        grep -RqsF "$marker" "$tree/agent" "$tree/tools" || {
            echo "ERROR: Hermes integration marker missing: $marker" >&2
            return 1
        }
    done

    grep -qF "OPEN_CLOUD_RESTRICTIVE_CRON_FAIL_CLOSED_V1" "$tree/cron/scheduler.py" || {
        echo "ERROR: restrictive cron fail-closed marker missing" >&2
        return 1
    }
    grep -qF "HERMES_SILENT_GATEWAY_LIFECYCLE_NOTICE_V1" "$tree/gateway/run.py" || {
        echo "ERROR: gateway lifecycle compatibility marker missing" >&2
        return 1
    }
    grep -qF "HERMES_CRON_REQUIRED_TOOLS_CACHE_KEY_V1" "$tree/model_tools.py" || {
        echo "ERROR: cron tool-safety cache-key marker missing" >&2
        return 1
    }
    for cron_marker in \
        HERMES_CRON_REQUIRED_TOOLS_RESOLVE_V1 \
        HERMES_CRON_REQUIRED_TO_EXECUTE_V1 \
        HERMES_CRON_RAW_TOOL_PROTOCOL_GUARD_V1 \
        HERMES_CRON_FAILURE_CLASSIFICATION_V1
    do
        grep -qF "$cron_marker" "$tree/cron/scheduler.py" || {
            echo "ERROR: cron tool-safety marker missing: $cron_marker" >&2
            return 1
        }
    done

    for rel in $FILES; do
        compile_file "$tree/$rel"
    done
}

materialize() {
    local out="$1"

    mkdir -p "$out"

    git -C "$HERMES_ROOT" archive HEAD | tar -x -C "$out"

    echo "HERMES_INSTALL: checking Fleet bridge patch"
    git -C "$out" apply --check "$PATCH1"
    git -C "$out" apply "$PATCH1"

    echo "HERMES_INSTALL: checking live attachment patch"
    git -C "$out" apply --check "$PATCH2"
    git -C "$out" apply "$PATCH2"

    echo "HERMES_INSTALL: checking cron tool-safety patch"
    git -C "$out" apply --check "$PATCH3"
    git -C "$out" apply "$PATCH3"

    if [ -f "$out/tools/daemon_pool.py" ]; then
        python3 "$ROOT/integrations/hermes/daemon_pool_compat.py" "$out/tools/daemon_pool.py"
    fi
    python3 "$ROOT/integrations/hermes/restrictive_cron.py" "$out/cron/scheduler.py"
    python3 "$ROOT/integrations/hermes/silent_gateway_lifecycle.py" "$out/gateway/run.py"

    validate_tree "$out"
}

managed_tree_matches() {
    local desired="$1"
    local rel

    for rel in $FILES; do
        test -e "$desired/$rel" || return 1
        test -e "$HERMES_ROOT/$rel" || return 1
        cmp -s "$desired/$rel" "$HERMES_ROOT/$rel" || return 1
    done

    return 0
}

backup_live() {
    local dir="$1"
    local rel

    mkdir -p "$dir"
    chmod 700 "$dir"

    for rel in $FILES; do
        if [ -e "$HERMES_ROOT/$rel" ]; then
            mkdir -p "$dir/$(dirname "$rel")"
            cp -a "$HERMES_ROOT/$rel" "$dir/$rel"
        fi
    done
}

rollback() {
    local rel

    if [ "${CHANGED:-0}" != "1" ]; then
        return 0
    fi

    if [ -z "${BACKUP:-}" ]; then
        return 0
    fi

    echo "ROLLBACK: restoring Hermes files"

    set +e

    for rel in $FILES; do
        if [ -e "$BACKUP/$rel" ]; then
            mkdir -p "$HERMES_ROOT/$(dirname "$rel")"
            cp -a "$BACKUP/$rel" "$HERMES_ROOT/$rel"
        else
            rm -f "$HERMES_ROOT/$rel"
        fi
    done

    set -e
}

install_live() {
    local rel stamp

    require_source

    TMP="$(mktemp -d)"
    trap cleanup EXIT

    materialize "$TMP/tree"

    if managed_tree_matches "$TMP/tree"; then
        validate_tree "$HERMES_ROOT"
        echo "HERMES_LIVE_INSTALL: ALREADY_PRESENT"
        return 0
    fi

    mkdir -p "$BACKUP_ROOT"
    chmod 700 "$BACKUP_ROOT"

    stamp="$(date -u +%Y%m%d-%H%M%S)"
    BACKUP="$BACKUP_ROOT/hermes-live-$stamp"

    backup_live "$BACKUP"

    CHANGED=1
    trap rollback ERR

    for rel in $FILES; do
        mkdir -p "$HERMES_ROOT/$(dirname "$rel")"
        cp -a "$TMP/tree/$rel" "$HERMES_ROOT/$rel"
    done

    validate_tree "$HERMES_ROOT"

    CHANGED=0
    trap - ERR

    echo "HERMES_LIVE_BACKUP: $BACKUP"
    echo "HERMES_LIVE_INSTALL: PASS"
}

cleanup() {
    if [ -n "${TMP:-}" ]; then
        rm -rf "$TMP"
    fi
}

case "$MODE" in
    --check)
        require_source
        TMP="$(mktemp -d)"
        trap cleanup EXIT
        materialize "$TMP/tree"
        echo "HERMES_LIVE_INSTALL_CHECK: PASS"
        ;;

    --install)
        install_live
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
