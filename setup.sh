#!/usr/bin/env bash
set -euo pipefail

# Open Cloud Assistant portable user PATH
export PATH="$HOME/.local/bin:$HOME/.bun/bin:$HOME/bin:$PATH"


ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MODE="${1:---help}"

case "$MODE" in
    --dry-run)
        echo "Open Cloud Assistant setup dry run"
        echo
        "$ROOT/install/00-preflight.sh"
        echo
        "$ROOT/install/10-hermes.sh" --dry-run
        "$ROOT/install/20-vellum.sh" --dry-run
        "$ROOT/install/30-brain-materialize.sh" --check
        "$ROOT/install/40-context-materialize.sh" --check
        "$ROOT/install/50-workers.sh" --check
        "$ROOT/install/60-self-repair.sh" --dry-run
        "$ROOT/install/70-fleet-runtime.sh" --check
        "$ROOT/install/75-fleet-registry.sh" --check
        "$ROOT/install/80-vellum-bridge.sh" --check
        "$ROOT/install/85-hermes-orchestration.sh" --check
        echo
        echo "Planned later setup stages:"
        echo "  messaging selection"
        echo "  restricted OpenCode repair"
        echo "  systemd services"
        echo "  final opencloud doctor"
        echo
        echo "SETUP_DRY_RUN: PASS"
        ;;
    --install)
        echo "Full installation is not enabled yet."
        echo "Run ./setup.sh --dry-run for the currently validated path."
        exit 2
        ;;
    -h|--help|help)
        echo "Usage:"
        echo "  ./setup.sh --dry-run"
        echo "  ./setup.sh --install   # enabled after integration stages are complete"
        ;;
    *)
        echo "Unknown option: $MODE" >&2
        exit 2
        ;;
esac
