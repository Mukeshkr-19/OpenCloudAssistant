#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TARGET_HOME="${OPEN_CLOUD_HOME:-$HOME}"
CONFIG="${OPEN_CLOUD_CONFIG:-$TARGET_HOME/.opencloud/config.env}"
MODE="${1:---help}"

case "$MODE" in
    --check)
        python3 -m py_compile "$ROOT/scripts/channels.py"
        "$ROOT/scripts/channels.py" --help >/dev/null
        echo "CHANNEL_INSTALL_CHECK: PASS"
        ;;

    --install)
        mkdir -p "$TARGET_HOME/.opencloud"
        chmod 700 "$TARGET_HOME/.opencloud"

        if [ ! -f "$CONFIG" ]; then
            touch "$CONFIG"
        fi

        chmod 600 "$CONFIG"

        echo "CHANNEL_INSTALL: PASS"
        echo "Run: opencloud channels configure"
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
