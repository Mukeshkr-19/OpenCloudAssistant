#!/usr/bin/env bash
set -euo pipefail

export PATH="$HOME/.local/bin:$HOME/.bun/bin:$HOME/bin:$PATH"

BASE="${OPEN_CLOUD_FLEET_HOME:-$HOME/.local/share/hermes-fleet}"
REGISTRY="$BASE/registry"
CONFIG="${OPEN_CLOUD_CONFIG:-$HOME/.opencloud/config.env}"
COMMAND="${1:-refresh}"

HERMES_PYTHON="${OPEN_CLOUD_HERMES_PYTHON:-$HOME/.hermes/hermes-agent/venv/bin/python}"

if [ ! -x "$HERMES_PYTHON" ]; then
    HERMES_PYTHON="$(command -v python3)"
fi

load_provider_env() {
    [ -f "$CONFIG" ] || return 0

    while IFS="=" read -r key value; do
        case "$key" in
            NVIDIA_API_KEY|OPENROUTER_API_KEY|OPEN_CLOUD_MODEL_VERIFICATION_TTL_SECONDS)
                export "$key=$value"
                ;;
        esac
    done < "$CONFIG"
}

require_runtime() {
    test -f "$REGISTRY/refresh.py" || {
        echo "ERROR: Fleet refresher is not installed." >&2
        exit 1
    }

    test -f "$REGISTRY/verify.py" || {
        echo "ERROR: Fleet verifier is not installed." >&2
        exit 1
    }
}

require_runtime
load_provider_env

case "$COMMAND" in
    refresh)
        "$HERMES_PYTHON" "$REGISTRY/refresh.py"
        "$HERMES_PYTHON" "$REGISTRY/verify.py"
        echo "FLEET_REFRESH: PASS"
        ;;
    verify)
        "$HERMES_PYTHON" "$REGISTRY/verify.py"
        echo "FLEET_VERIFY: PASS"
        ;;
    check)
        "$HERMES_PYTHON" -m py_compile "$REGISTRY/refresh.py" "$REGISTRY/verify.py"
        echo "FLEET_REFRESH_WRAPPER_CHECK: PASS"
        ;;
    *)
        echo "Usage:" >&2
        echo "  opencloud fleet refresh" >&2
        echo "  opencloud fleet verify" >&2
        exit 2
        ;;
esac
