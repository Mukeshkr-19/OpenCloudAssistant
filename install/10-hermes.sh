#!/usr/bin/env bash
set -euo pipefail

# Open Cloud Assistant portable user PATH
export PATH="$HOME/.local/bin:$HOME/.bun/bin:$HOME/bin:$PATH"


MODE="${1:---dry-run}"

if command -v hermes >/dev/null 2>&1; then
    echo "HERMES_INSTALL: ALREADY_PRESENT"
    exit 0
fi

case "$MODE" in
    --dry-run)
        echo "HERMES_INSTALL: WOULD_INSTALL"
        echo "Source: official Nous Research Hermes installer"
        echo "Interactive Hermes setup will be skipped so Open Cloud Assistant can configure the stack."
        ;;
    --install)
        curl -fsSL https://hermes-agent.nousresearch.com/install.sh | bash -s -- --skip-setup
        command -v hermes >/dev/null 2>&1
        echo "HERMES_INSTALL: PASS"
        ;;
    *)
        echo "Usage: $0 [--dry-run|--install]" >&2
        exit 2
        ;;
esac
