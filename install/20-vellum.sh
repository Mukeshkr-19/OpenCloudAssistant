#!/usr/bin/env bash
set -euo pipefail

MODE="${1:---dry-run}"

if command -v vellum >/dev/null 2>&1; then
    echo "VELLUM_INSTALL: ALREADY_PRESENT"
    exit 0
fi

case "$MODE" in
    --dry-run)
        if command -v bun >/dev/null 2>&1; then
            echo "BUN: PRESENT"
        else
            echo "BUN: WOULD_INSTALL"
        fi
        echo "VELLUM_INSTALL: WOULD_INSTALL"
        ;;
    --install)
        if ! command -v bun >/dev/null 2>&1; then
            command -v unzip >/dev/null 2>&1 || {
                echo "ERROR: unzip is required before installing Bun" >&2
                exit 1
            }
            curl -fsSL https://bun.com/install | bash
            export BUN_INSTALL="$HOME/.bun"
            export PATH="$BUN_INSTALL/bin:$PATH"
        fi

        bun install -g vellum
        export PATH="$HOME/.bun/bin:$PATH"
        command -v vellum >/dev/null 2>&1
        echo "VELLUM_INSTALL: PASS"
        ;;
    *)
        echo "Usage: $0 [--dry-run|--install]" >&2
        exit 2
        ;;
esac
