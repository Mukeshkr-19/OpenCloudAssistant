#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

INSTALLER="$ROOT/install/95-services.sh"

REGISTRY_TIMER="hermes-fleet-registry.timer"
VERIFIER_TIMER="hermes-fleet-verifier.timer"
GATEWAY_SERVICE="hermes-gateway.service"

require_contains() {
    local file="$1"
    local text="$2"

    grep -qF "$text" "$file" || {
        echo "FAIL missing contract: $text in $file" >&2
        exit 1
    }
}

echo "Open Cloud Assistant service persistence reliability test"

echo
echo "STATIC BOOT CONTRACT"

test -f services/systemd/hermes-fleet-registry.timer
test -f services/systemd/hermes-fleet-verifier.timer
test -x "$INSTALLER"

require_contains     services/systemd/hermes-fleet-registry.timer     "WantedBy=timers.target"

require_contains     services/systemd/hermes-fleet-verifier.timer     "WantedBy=timers.target"

require_contains     "$INSTALLER"     "systemctl --user enable --now hermes-fleet-registry.timer"

require_contains     "$INSTALLER"     "systemctl --user enable --now hermes-fleet-verifier.timer"

require_contains     "$INSTALLER"     "systemctl --user enable --now hermes-gateway.service"

require_contains     "$INSTALLER"     "loginctl show-user"

require_contains     "$INSTALLER"     "enable-linger"

echo "PASS Fleet registry boot target contract"
echo "PASS Fleet verifier boot target contract"
echo "PASS installer enable-now contract"
echo "PASS gateway persistence contract"
echo "PASS linger management contract"

if [ "${OPEN_CLOUD_LIVE_SERVICE_TEST:-0}" != "1" ]; then
    echo "LIVE_SERVICE_RECOVERY: SKIP"
    echo "SERVICE_PERSISTENCE_RELIABILITY: PASS"
    exit 0
fi

echo
echo "LIVE ORACLE SERVICE RECOVERY"

command -v systemctl >/dev/null
command -v loginctl >/dev/null

LINGER="$(loginctl show-user "$USER" -p Linger --value 2>/dev/null || true)"

echo "Linger: ${LINGER:-unknown}"

[ "$LINGER" = "yes" ] || {
    echo "FAIL: user lingering is not enabled" >&2
    exit 1
}

echo "PASS user lingering enabled"

for unit in     "$REGISTRY_TIMER"     "$VERIFIER_TIMER"
do
    enabled="$(systemctl --user is-enabled "$unit" 2>/dev/null || true)"
    active="$(systemctl --user is-active "$unit" 2>/dev/null || true)"

    echo "$unit enabled=$enabled active=$active"

    [ "$enabled" = "enabled" ] || {
        echo "FAIL: $unit is not enabled" >&2
        exit 1
    }

    [ "$active" = "active" ] || {
        echo "FAIL: $unit is not active before restart" >&2
        exit 1
    }

    systemctl --user restart "$unit"

    active="$(systemctl --user is-active "$unit" 2>/dev/null || true)"

    [ "$active" = "active" ] || {
        echo "FAIL: $unit did not recover after restart" >&2
        exit 1
    }

    echo "PASS $unit restart recovery"
done

GATEWAY_PRESENT=0

if systemctl --user cat "$GATEWAY_SERVICE" >/dev/null 2>&1; then
    GATEWAY_PRESENT=1
fi

if [ "$GATEWAY_PRESENT" -eq 1 ]; then

    gateway_enabled="$(
        systemctl --user is-enabled "$GATEWAY_SERVICE" 2>/dev/null || true
    )"

    gateway_active="$(
        systemctl --user is-active "$GATEWAY_SERVICE" 2>/dev/null || true
    )"

    echo "$GATEWAY_SERVICE enabled=$gateway_enabled active=$gateway_active"

    [ "$gateway_enabled" = "enabled" ] || {
        echo "FAIL: gateway is installed but not enabled" >&2
        exit 1
    }

    [ "$gateway_active" = "active" ] || {
        echo "FAIL: gateway is installed but not active" >&2
        exit 1
    }

    old_pid="$(
        systemctl --user show             "$GATEWAY_SERVICE"             -p MainPID             --value
    )"

    echo "Gateway old PID: $old_pid"

    [ "${old_pid:-0}" -gt 0 ]

    wall_start="$(date +%s%N)"

    systemctl --user restart "$GATEWAY_SERVICE"

    recovered=0

    for _ in $(seq 1 30); do

        state="$(
            systemctl --user is-active                 "$GATEWAY_SERVICE"                 2>/dev/null || true
        )"

        new_pid="$(
            systemctl --user show                 "$GATEWAY_SERVICE"                 -p MainPID                 --value 2>/dev/null || true
        )"

        if [ "$state" = "active" ] &&            [ "${new_pid:-0}" -gt 0 ] &&            [ "$new_pid" != "$old_pid" ]; then

            recovered=1
            break
        fi

        sleep 1
    done

    wall_end="$(date +%s%N)"

    [ "$recovered" -eq 1 ] || {
        echo "FAIL: Hermes gateway did not recover after controlled restart" >&2
        exit 1
    }

    recovery_ms="$(
        python3 -c "print(round(($wall_end - $wall_start) / 1000000, 3))"
    )"

    echo "Gateway new PID: $new_pid"

    echo "PASS Hermes gateway controlled restart recovery"
    echo "MEASURE gateway_restart_recovery_ms=$recovery_ms"

else

    echo "HERMES_GATEWAY_RECOVERY: SKIP (gateway not installed)"
fi

echo
echo "LIVE_BOOT_CONFIGURATION:"
echo "  linger=yes"
echo "  registry_timer=enabled+active"
echo "  verifier_timer=enabled+active"

if [ "$GATEWAY_PRESENT" -eq 1 ]; then
    echo "  gateway=enabled+active"
fi

echo
echo "INFO this proves boot configuration and controlled service recovery"
echo "INFO an actual OS reboot remains a separate machine acceptance test"

echo "SERVICE_PERSISTENCE_RELIABILITY: PASS"
