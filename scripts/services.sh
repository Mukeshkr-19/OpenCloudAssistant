#!/usr/bin/env bash
set -euo pipefail

export PATH="$HOME/.local/bin:$HOME/.bun/bin:$HOME/bin:$PATH"

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
COMMAND="${1:-status}"

unit_status() {
    local unit="$1"
    local enabled active

    enabled="$(systemctl --user is-enabled "$unit" 2>/dev/null || true)"
    active="$(systemctl --user is-active "$unit" 2>/dev/null || true)"

    printf "%-34s enabled=%-10s active=%s\n" "$unit" "${enabled:-unknown}" "${active:-unknown}"
}

case "$COMMAND" in
    status)
        echo "Open Cloud Assistant services"
        echo
        unit_status hermes-fleet-registry.timer
        unit_status hermes-fleet-verifier.timer
        unit_status opencloud-runtime-update.timer

        if systemctl --user cat hermes-gateway.service >/dev/null 2>&1; then
            unit_status hermes-gateway.service
        else
            echo "hermes-gateway.service             not installed"
        fi

        LINGER="$(loginctl show-user "$USER" -p Linger --value 2>/dev/null || true)"
        echo "user linger: ${LINGER:-unknown}"
        ;;

    plan)
        exec "$ROOT/install/95-services.sh" --plan
        ;;

    install)
        exec "$ROOT/install/95-services.sh" --install
        ;;

    restart-gateway)
        exec hermes gateway restart
        ;;

    logs)
        journalctl --user -u hermes-gateway.service -n 100 --no-pager
        ;;

    *)
        echo "Usage:" >&2
        echo "  opencloud services status" >&2
        echo "  opencloud services plan" >&2
        echo "  opencloud services install" >&2
        echo "  opencloud services restart-gateway" >&2
        echo "  opencloud services logs" >&2
        exit 2
        ;;
esac
