#!/usr/bin/env bash
set -euo pipefail

export PATH="$HOME/.local/bin:$HOME/.bun/bin:$HOME/bin:$PATH"

BASE="${OPEN_CLOUD_FLEET_HOME:-$HOME/.local/share/hermes-fleet}"
DISPATCHER="$BASE/dispatcher.py"
COMMAND="${1:-status}"

case "$COMMAND" in
    paths)
        echo "Fleet base:       $BASE"
        echo "Dispatcher:       $DISPATCHER"
        echo "Policy:           $BASE/fleet.json"
        echo "Registry:         $BASE/registry/models.json"
        echo "Health database:  $BASE/health.sqlite"
        ;;
    status)
        if [ ! -f "$DISPATCHER" ]; then
            echo "Fleet runtime is not installed." >&2
            echo "Run the Open Cloud Assistant installer first." >&2
            exit 1
        fi
        exec python3 "$DISPATCHER" status
        ;;
    select)
        ROLE="${2:-worker}"
        if [ ! -f "$DISPATCHER" ]; then
            echo "Fleet runtime is not installed." >&2
            exit 1
        fi
        exec python3 "$DISPATCHER" select "$ROLE"
        ;;
    *)
        echo "Usage:" >&2
        echo "  opencloud fleet status" >&2
        echo "  opencloud fleet paths" >&2
        echo "  opencloud fleet select [main|worker|reviewer]" >&2
        exit 2
        ;;
esac
