#!/usr/bin/env bash
set -euo pipefail

export PATH="$HOME/.local/bin:$HOME/.bun/bin:$HOME/bin:$PATH"

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MODE="${1:---help}"
TEST_MODE="${OPEN_CLOUD_SETUP_TEST_MODE:-0}"

configure_channels() {
    local state="${OPEN_CLOUD_CHANNELS_STATE:-$HOME/.opencloud/channels.json}"

    if [ -f "$state" ]; then
        echo "CHANNEL_SETUP: EXISTING_SELECTION"
        return 0
    fi

    if [ -n "${OPEN_CLOUD_CHANNELS:-}" ]; then
        OPEN_CLOUD_CHANNELS_STATE="$state" python3 "$ROOT/scripts/channels.py" set "$OPEN_CLOUD_CHANNELS"
        return 0
    fi

    if [ -t 0 ] && [ -t 1 ]; then
        OPEN_CLOUD_CHANNELS_STATE="$state" python3 "$ROOT/scripts/channels.py" configure
        return 0
    fi

    echo "CHANNEL_SETUP: noninteractive install defaults to CLI"
    OPEN_CLOUD_CHANNELS_STATE="$state" python3 "$ROOT/scripts/channels.py" set cli
}

run_checks() {
    local hermes_root="${OPEN_CLOUD_HERMES_ROOT:-$HOME/.hermes/hermes-agent}"
    local hermes_source="${OPEN_CLOUD_HERMES_SOURCE:-$hermes_root}"

    "$ROOT/install/00-preflight.sh" --dry-run
    "$ROOT/install/10-hermes.sh" --dry-run
    "$ROOT/install/20-vellum.sh" --dry-run

    if [ -d "$hermes_root/.git" ]; then
        OPEN_CLOUD_HERMES_ROOT="$hermes_root" "$ROOT/install/30-brain-materialize.sh" --check
        OPEN_CLOUD_HERMES_ROOT="$hermes_root" "$ROOT/install/35-hermes-live.sh" --check
    else
        echo "HERMES_COMPATIBILITY_CHECK: DEFERRED_UNTIL_HERMES_INSTALL"
        echo "HERMES_LIVE_INTEGRATION_CHECK: DEFERRED_UNTIL_HERMES_INSTALL"
    fi

    "$ROOT/install/40-context-materialize.sh" --check
    "$ROOT/install/50-workers.sh" --check
    "$ROOT/install/60-self-repair.sh" --dry-run
    "$ROOT/install/70-fleet-runtime.sh" --check
    "$ROOT/install/75-fleet-registry.sh" --check
    "$ROOT/install/80-vellum-bridge.sh" --check

    if [ -f "$hermes_source/tools/delegate_tool.py" ]; then
        OPEN_CLOUD_HERMES_SOURCE="$hermes_source" "$ROOT/install/85-hermes-orchestration.sh" --check
    else
        echo "HERMES_ORCHESTRATION_SOURCE_CHECK: DEFERRED_UNTIL_HERMES_INSTALL"
    fi

    "$ROOT/install/90-channels.sh" --check
    "$ROOT/install/95-services.sh" --check
}

run_dry() {
    echo "Open Cloud Assistant setup dry run"
    echo
    run_checks
    echo
    echo "SETUP_DRY_RUN: PASS"
}

run_install_test() {
    echo "Open Cloud Assistant install branch safe validation"
    echo
    run_checks
    echo
    echo "SETUP_INSTALL_TEST: PASS"
}

run_install() {
    echo "============================================================"
    echo " Open Cloud Assistant installation"
    echo "============================================================"

    echo "[1/14] Preflight"
    "$ROOT/install/00-preflight.sh" --install

    echo "[2/14] Hermes"
    "$ROOT/install/10-hermes.sh" --install

    echo "[3/14] Vellum"
    "$ROOT/install/20-vellum.sh" --install

    echo "[4/14] Hermes compatibility"
    "$ROOT/install/30-brain-materialize.sh" --check

    echo "[5/14] Hermes live integration"
    "$ROOT/install/35-hermes-live.sh" --install

    echo "[6/14] Context and worker contracts"
    "$ROOT/install/40-context-materialize.sh" --check
    "$ROOT/install/50-workers.sh" --check

    echo "[7/14] Restricted self-repair"
    "$ROOT/install/60-self-repair.sh" --install

    echo "[8/14] Dynamic Fleet runtime"
    "$ROOT/install/70-fleet-runtime.sh" --install

    echo "[9/14] Dynamic Fleet registry"
    "$ROOT/install/75-fleet-registry.sh" --install

    echo "[10/14] Hermes and Vellum bridge"
    "$ROOT/install/80-vellum-bridge.sh" --install

    echo "[11/14] Hermes orchestration"
    "$ROOT/install/85-hermes-orchestration.sh" --install

    echo "[12/14] Channels"
    "$ROOT/install/90-channels.sh" --install
    configure_channels

    echo "[13/14] Always-on services"
    "$ROOT/install/95-services.sh" --install

    echo "[14/14] Final doctor"
    "$ROOT/bin/opencloud" doctor

    echo
    echo "============================================================"
    echo " SETUP_INSTALL: PASS"
    echo "============================================================"
}

case "$MODE" in
    --dry-run)
        run_dry
        ;;

    --install)
        if [ "$TEST_MODE" = "1" ]; then
            run_install_test
        else
            run_install
        fi
        ;;

    -h|--help|help)
        echo "Usage:"
        echo "  ./setup.sh --dry-run"
        echo "  ./setup.sh --install"
        echo
        echo "Noninteractive examples:"
        echo "  OPEN_CLOUD_CHANNELS=cli ./setup.sh --install"
        echo "  OPEN_CLOUD_CHANNELS=telegram,cli ./setup.sh --install"
        ;;

    *)
        echo "Unknown option: $MODE" >&2
        exit 2
        ;;
esac
