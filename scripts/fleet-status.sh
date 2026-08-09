#!/usr/bin/env bash
set -euo pipefail

export PATH="$HOME/.local/bin:$HOME/.bun/bin:$HOME/bin:$PATH"

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BASE="${OPEN_CLOUD_FLEET_HOME:-$HOME/.local/share/hermes-fleet}"
DISPATCHER="$BASE/dispatcher.py"
REGISTRY="$BASE/registry/models.json"
COMMAND="${1:-status}"

case "$COMMAND" in
    paths)
        echo "Fleet base:       $BASE"
        echo "Dispatcher:       $DISPATCHER"
        echo "Policy:           $BASE/fleet.json"
        echo "Registry:         $REGISTRY"
        echo "Health database:  $BASE/health.sqlite"
        ;;
    status)
        test -f "$DISPATCHER" || {
            echo "Fleet runtime is not installed." >&2
            exit 1
        }
        exec python3 "$DISPATCHER" status
        ;;
    select)
        ROLE="${2:-worker}"
        test -f "$DISPATCHER" || {
            echo "Fleet runtime is not installed." >&2
            exit 1
        }
        exec python3 "$DISPATCHER" select "$ROLE"
        ;;
    refresh)
        exec "$ROOT/scripts/fleet-refresh.sh" refresh
        ;;
    verify)
        exec "$ROOT/scripts/fleet-refresh.sh" verify
        ;;
    proof)
        test -f "$REGISTRY" || {
            echo "No runtime registry exists yet."
            exit 1
        }

        python3 -c "
import json,sys
d=json.load(open(sys.argv[1]))
prod=d.get(\"productionModels\",{}) if isinstance(d,dict) else {}
status=d.get(\"providerStatus\",{}) if isinstance(d,dict) else {}
nvidia=len(prod.get(\"nvidia\",[]) or [])
zen=len(prod.get(\"zen\",[]) or [])
orr=status.get(\"openrouter\",{}) if isinstance(status,dict) else {}
print(\"NVIDIA verified models:\", nvidia)
print(\"Zen verified free models:\", zen)
print(\"OpenRouter discovery healthy:\", bool(orr.get(\"ok\",False)))
print(\"Verification run recorded:\", bool(d.get(\"lastVerificationRunMs\",0)))
" "$REGISTRY"
        ;;
    *)
        echo "Usage:" >&2
        echo "  opencloud fleet status" >&2
        echo "  opencloud fleet paths" >&2
        echo "  opencloud fleet refresh" >&2
        echo "  opencloud fleet verify" >&2
        echo "  opencloud fleet proof" >&2
        echo "  opencloud fleet select [main|worker|reviewer]" >&2
        exit 2
        ;;
esac
