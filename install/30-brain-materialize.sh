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
PATCH_WORKFLOW="$ROOT/integrations/hermes/hermes-cron-workflow-identity.patch"
PATCH_REPEAT="$ROOT/integrations/hermes/hermes-cron-repeat-coercion.patch"
PATCH_RUNONCE="$ROOT/integrations/hermes/hermes-run-now-once-provider-quiet.patch"
PATCH_FASTPATH="$ROOT/integrations/hermes/hermes-cron-control-fast-path.patch"
PATCH_GEO="$ROOT/integrations/hermes/hermes-career-geography-search.patch"
PATCH_WAVES="$ROOT/integrations/hermes/hermes-career-search-waves.patch"
PATCH_REJECT="$ROOT/integrations/hermes/hermes-career-candidate-rejection.patch"
PATCH_SEARCH="$ROOT/integrations/hermes/hermes-search-reliability.patch"
PATCH_PROVIDER_SURVIVAL="$ROOT/integrations/hermes/hermes-provider-survival.patch"
PATCH_PROVIDER_SURVIVAL_FOLLOWUP="$ROOT/integrations/hermes/hermes-provider-survival-followup.patch"
PATCH_PROVIDER_TOOL_COMPAT="$ROOT/integrations/hermes/hermes-provider-tool-call-compatibility.patch"
PATCH_RUNTIME_ELIGIBILITY="$ROOT/integrations/hermes/hermes-runtime-eligibility-search-bounds.patch"
PATCH_PRODUCT_UX="$ROOT/integrations/hermes/hermes-product-reliability-ux.patch"

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
    local patch10="$rendered/hermes-cron-workflow-identity.patch"
    local patch11="$rendered/hermes-cron-repeat-coercion.patch"
    local patch12="$rendered/hermes-run-now-once-provider-quiet.patch"
    local patch13="$rendered/hermes-cron-control-fast-path.patch"
    local patch14="$rendered/hermes-career-geography-search.patch"
    local patch15="$rendered/hermes-career-search-waves.patch"
    local patch16="$rendered/hermes-career-candidate-rejection.patch"
    local patch17="$rendered/hermes-search-reliability.patch"
    local patch18="$rendered/hermes-provider-survival.patch"
    local patch19="$rendered/hermes-provider-survival-followup.patch"
    local patch20="$rendered/hermes-provider-tool-call-compatibility.patch"
    local patch21="$rendered/hermes-runtime-eligibility-search-bounds.patch"
    local patch22="$rendered/hermes-product-reliability-ux.patch"

    require_file "$PATCH_FLEET"
    require_file "$PATCH_LIVE"
    require_file "$PATCH_CRON"
    require_file "$PATCH_CONT"
    require_file "$PATCH_OUTPUT"
    require_file "$PATCH_GUARD"
    require_file "$PATCH_SCORING"
    require_file "$PATCH_REPAIR"
    require_file "$PATCH_DUP"
    require_file "$PATCH_WORKFLOW"
    require_file "$PATCH_REPEAT"
    require_file "$PATCH_RUNONCE"
    require_file "$PATCH_FASTPATH"
    require_file "$PATCH_GEO"
    require_file "$PATCH_WAVES"
    require_file "$PATCH_REJECT"
    require_file "$PATCH_SEARCH"
    require_file "$PATCH_PROVIDER_SURVIVAL"
    require_file "$PATCH_PROVIDER_SURVIVAL_FOLLOWUP"
    require_file "$PATCH_PROVIDER_TOOL_COMPAT"
    require_file "$PATCH_RUNTIME_ELIGIBILITY"
    require_file "$PATCH_PRODUCT_UX"

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
    render_patch "$PATCH_WORKFLOW" "$patch10"
    render_patch "$PATCH_REPEAT" "$patch11"
    render_patch "$PATCH_RUNONCE" "$patch12"
    render_patch "$PATCH_FASTPATH" "$patch13"
    render_patch "$PATCH_GEO" "$patch14"
    render_patch "$PATCH_WAVES" "$patch15"
    render_patch "$PATCH_REJECT" "$patch16"
    render_patch "$PATCH_SEARCH" "$patch17"
    render_patch "$PATCH_PROVIDER_SURVIVAL" "$patch18"
    render_patch "$PATCH_PROVIDER_SURVIVAL_FOLLOWUP" "$patch19"
    render_patch "$PATCH_PROVIDER_TOOL_COMPAT" "$patch20"
    render_patch "$PATCH_RUNTIME_ELIGIBILITY" "$patch21"
    render_patch "$PATCH_PRODUCT_UX" "$patch22"

    # The exported archive is intentionally not a Git checkout.  Create a
    # temporary local index so git apply validates and applies patches to the
    # staged tree rather than silently walking up to the OpenCloudAssistant
    # repository and skipping every patch (nested output directories otherwise
    # make `git -C "$out" apply` a false-positive).
    git -C "$out" init --quiet
    git -C "$out" add -A

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

    echo "MATERIALIZE: checking cron workflow-identity patch"
    git -C "$out" apply --check "$patch10"
    git -C "$out" apply "$patch10"

    echo "MATERIALIZE: checking cron repeat-coercion patch"
    git -C "$out" apply --check "$patch11"
    git -C "$out" apply "$patch11"

    echo "MATERIALIZE: checking run-now-once / provider-quiet patch"
    git -C "$out" apply --check "$patch12"
    git -C "$out" apply "$patch12"

    echo "MATERIALIZE: checking cron-control fast-path patch"
    git -C "$out" apply --check "$patch13"
    git -C "$out" apply "$patch13"

    echo "MATERIALIZE: checking career geography + search-coverage patch"
    git -C "$out" apply --check "$patch14"
    git -C "$out" apply "$patch14"

    echo "MATERIALIZE: checking career search-waves orchestration patch"
    git -C "$out" apply --check "$patch15"
    git -C "$out" apply "$patch15"

    echo "MATERIALIZE: checking career candidate-rejection patch"
    git -C "$out" apply --check "$patch16"
    git -C "$out" apply "$patch16"

    echo "MATERIALIZE: checking career search reliability patch"
    git -C "$out" apply --check --unidiff-zero "$patch17"
    git -C "$out" apply --unidiff-zero "$patch17"

    echo "MATERIALIZE: checking provider routing/cron survival patch"
    git -C "$out" apply --check --unidiff-zero "$patch18"
    git -C "$out" apply --unidiff-zero "$patch18"

    echo "MATERIALIZE: checking provider survival acceptance follow-up patch"
    git -C "$out" apply --check --unidiff-zero "$patch19"
    git -C "$out" apply --unidiff-zero "$patch19"

    echo "MATERIALIZE: checking provider tool-call compatibility patch"
    git -C "$out" apply --check --unidiff-zero "$patch20"
    git -C "$out" apply --unidiff-zero "$patch20"

    echo "MATERIALIZE: checking runtime eligibility + atomic search bounds patch"
    git -C "$out" apply --check --unidiff-zero "$patch21"
    git -C "$out" apply --unidiff-zero "$patch21"

    echo "MATERIALIZE: checking product reliability UX patch"
    git -C "$out" apply --check "$patch22"
    git -C "$out" apply "$patch22"


    for ux_marker in \
        HERMES_OPENCLOUD_MODEL_ALIAS_V1 \
        HERMES_OPENCLOUD_FLEET_MODEL_UX_V1 \
        HERMES_OPENCLOUD_CAMOFOX_SCOPE_V1 \
        HERMES_OPENCLOUD_BROWSER_AVAIL_V1 \
        HERMES_OPENCLOUD_SYSTEM_PROMPT_V1 \
        HERMES_OPENCLOUD_TOOL_INTENT_V1
    do
        if ! grep -RqsF "$ux_marker" "$out/agent" "$out/gateway" "$out/tools" "$out/hermes_cli" "$out/hermes_state.py"; then
            echo "ERROR: product UX marker missing after materialization: $ux_marker" >&2
            exit 1
        fi
    done

    echo "HERMES_INSTALL: applying Routing V1 workload compatibility"
    python3 "$ROOT/integrations/hermes/routing_v1_compat.py" "$out"

    test -f "$out/agent/hermes_fleet_bridge.py"

    for marker in \
        HERMES_FLEET_MAIN_ATTACH_BEGIN \
        HERMES_FLEET_WORKER_ATTACH_BEGIN \
        HERMES_FLEET_FAILURE_ATTACH_BEGIN \
        HERMES_FLEET_FALLBACK_SKIP_BEGIN \
        HERMES_FLEET_GEMINI_UNVERIFIED_GUARD_V1 \
        HERMES_PROVIDER_SURVIVAL_V1 \
        HERMES_PROVIDER_FAILOVER_DEADLINE_V1 \
        HERMES_FLEET_CONTEXT_ELIGIBILITY_V1
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
        HERMES_CRON_STRICT_SILENT_DELIVERY_V1 \
        HERMES_CAREER_SEARCH_ATOMIC_BOUNDS_V1
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

    if ! grep -RqsF "HERMES_CRON_WORKFLOW_IDENTITY_V1" "$out/tools"; then
        echo "ERROR: cron workflow-identity marker missing after materialization" >&2
        exit 1
    fi

    if ! grep -RqsF "HERMES_CRON_REPEAT_COERCION_V1" "$out/tools"; then
        echo "ERROR: cron repeat-coercion marker missing after materialization" >&2
        exit 1
    fi

    if ! grep -RqsF "HERMES_CRON_RUN_NOW_ONCE_V1" "$out/tools"; then
        echo "ERROR: cron run-now-once marker missing after materialization" >&2
        exit 1
    fi

    if ! grep -RqsF "HERMES_PROVIDER_FALLBACK_STATUS_FILTER_V1" "$out/gateway"; then
        echo "ERROR: provider fallback status filter marker missing after materialization" >&2
        exit 1
    fi

    if ! grep -RqsF "HERMES_CRON_CONTROL_FAST_PATH_V1" "$out/gateway"; then
        echo "ERROR: cron-control fast-path marker missing after materialization" >&2
        exit 1
    fi

    if ! grep -RqsF "HERMES_COMPRESSION_FAILSAFE_V1" "$out/gateway"; then
        echo "ERROR: compression failsafe marker missing after materialization" >&2
        exit 1
    fi

    if ! grep -RqsF "HERMES_P13_DIAGNOSTIC_SUPPRESSION_V1" "$out/gateway"; then
        echo "ERROR: P13 diagnostic-suppression marker missing after materialization" >&2
        exit 1
    fi

    if ! grep -RqsF "HERMES_CRON_GEOGRAPHY_POLICY_V1" "$out/cron"; then
        echo "ERROR: career geography-policy marker missing after materialization" >&2
        exit 1
    fi

    if ! grep -RqsF "HERMES_CRON_SEARCH_WAVES_V1" "$out/cron" "$out/agent"; then
        echo "ERROR: career search-waves marker missing after materialization" >&2
        exit 1
    fi

    if ! grep -RqsF "HERMES_CRON_CANDIDATE_REJECTION_V1" "$out/cron"; then
        echo "ERROR: career candidate-rejection marker missing after materialization" >&2
        exit 1
    fi

    for search_marker in \
        HERMES_SEARCH_EMPTY_RESULT_SEMANTICS_V1 \
        HERMES_CAREER_SEARCH_CONTROLLER_V1 \
        HERMES_CAREER_SEARCH_CONTEXT_V1
    do
        if ! grep -RqsF "$search_marker" "$out/agent" "$out/tools" "$out/plugins" "$out/cron"; then
            echo "ERROR: search-reliability marker missing after materialization: $search_marker" >&2
            exit 1
        fi
    done

    python3 -m py_compile "$out/agent/hermes_fleet_bridge.py"
    python3 -m py_compile "$out/agent/conversation_loop.py"
    python3 -m py_compile "$out/tools/tool_search.py" "$out/model_tools.py" "$out/cron/scheduler.py" "$out/cron/output_contract.py"
    python3 -m py_compile "$out/agent/provider_metadata_guard.py" "$out/agent/transports/chat_completions.py" "$out/agent/transports/codex.py" "$out/gateway/run.py"
    python3 -m py_compile "$out/agent/opencloud_self_repair.py"
    python3 -m py_compile "$out/tools/cronjob_tools.py"
    python3 -m py_compile "$out/gateway/cron_control_fast_path.py"
    python3 -m py_compile "$out/tools/web_tools.py" "$out/plugins/web/ddgs/provider.py"
    python3 -m py_compile "$out/cron/search_reliability.py"
    python3 -m py_compile "$out/gateway/slash_commands.py" "$out/hermes_cli/commands.py" "$out/hermes_state.py"
    python3 -m py_compile "$out/tools/browser_camofox.py" "$out/tools/registry.py"

    rm -rf "$rendered"
    rm -rf "$out/.git"

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
