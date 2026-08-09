#!/usr/bin/env bash
set -euo pipefail

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
        echo
        echo "Planned later setup stages:"
        echo "  provider configuration"
        echo "  dynamic Fleet"
        echo "  Hermes/Vellum context bridge"
        echo "  parallel workers"
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
