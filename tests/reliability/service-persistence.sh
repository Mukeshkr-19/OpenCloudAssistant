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

require_timer_contract() {
    local file="$1"
    local initial="$2"
    local initial_sources
    local schedule_sources

    require_contains "$file" "OnActiveSec=$initial"
    require_contains "$file" "OnUnitActiveSec=6h"
    require_contains "$file" "RandomizedDelaySec=10min"

    initial_sources="$(grep -Ec '^(OnActiveSec|OnBootSec|OnStartupSec|OnCalendar)=' "$file")"
    [ "$initial_sources" = "1" ] || {
        echo "FAIL expected exactly one initial schedule source in $file" >&2
        exit 1
    }

    schedule_sources="$(grep -Ec '^(OnActiveSec|OnBootSec|OnStartupSec|OnUnitActiveSec|OnUnitInactiveSec|OnCalendar)=' "$file")"
    [ "$schedule_sources" = "2" ] || {
        echo "FAIL expected only initial and recurring schedule sources in $file" >&2
        exit 1
    }

    if grep -q '^Persistent=' "$file"; then
        echo "FAIL Persistent only applies to OnCalendar timers: $file" >&2
        exit 1
    fi
}

echo "Open Cloud Assistant service persistence reliability test"

echo
echo "STATIC BOOT CONTRACT"

test -f services/systemd/hermes-fleet-registry.timer
test -f services/systemd/hermes-fleet-verifier.timer
test -x "$INSTALLER"

require_contains     services/systemd/hermes-fleet-registry.timer     "WantedBy=timers.target"

require_contains     services/systemd/hermes-fleet-verifier.timer     "WantedBy=timers.target"

require_timer_contract services/systemd/hermes-fleet-registry.timer 8min
require_timer_contract services/systemd/hermes-fleet-verifier.timer 15min

require_contains     "$INSTALLER"     "systemctl --user enable \\"

require_contains     "$INSTALLER"     "systemctl --user restart \\"

require_contains     "$INSTALLER"     "hermes-fleet-registry.timer"

require_contains     "$INSTALLER"     "hermes-fleet-verifier.timer"

require_contains     "$INSTALLER"     "systemctl --user enable --now hermes-gateway.service"

require_contains     "$INSTALLER"     "Environment=OPEN_CLOUD_SELF_REPAIR=1"

require_contains     "$INSTALLER"     "Environment=OPEN_CLOUD_REPAIR_STATE="

require_contains     "$INSTALLER"     "is-active --quiet hermes-gateway.service"

require_contains     "$INSTALLER"     "loginctl show-user"

require_contains     "$INSTALLER"     "enable-linger"

echo "PASS Fleet registry boot target contract"
echo "PASS Fleet verifier boot target contract"
echo "PASS Fleet timer cadence contract"
echo "PASS installer enable and rearm contract"
echo "PASS gateway persistence contract"
echo "PASS gateway self-repair environment contract"
echo "PASS linger management contract"

echo
echo "DETERMINISTIC TIMER REARM"

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

BIN="$TMP/bin"
HOME_TARGET="$TMP/home"
FLEET="$HOME_TARGET/.local/share/hermes-fleet"
SYSTEMD_STATE="$TMP/systemd-state"
mkdir -p "$BIN" "$FLEET/registry" "$SYSTEMD_STATE"
touch "$FLEET/registry/refresh.py" "$FLEET/registry/verify.py"

cat > "$BIN/systemctl" <<'SH'
#!/usr/bin/env bash
set -euo pipefail

STATE="${FAKE_SYSTEMD_STATE:?}"
[ "${1:-}" = "--user" ] && shift
command="${1:?}"
shift

case "$command" in
    daemon-reload)
        exit 0
        ;;
    enable)
        for unit in "$@"; do
            [ "$unit" = "--now" ] && continue
            touch "$STATE/$unit.enabled"
        done
        ;;
    restart)
        for unit in "$@"; do
            touch "$STATE/$unit.active"
            if grep -q '^OnActiveSec=' "${FAKE_SYSTEMD_DIR:?}/$unit"; then
                printf '%s\n' 123456789 > "$STATE/$unit.next"
            else
                printf '%s\n' infinity > "$STATE/$unit.next"
            fi
            count="$(cat "$STATE/$unit.restarts" 2>/dev/null || printf '%s' 0)"
            printf '%s\n' "$((count + 1))" > "$STATE/$unit.restarts"
        done
        ;;
    is-active)
        [ "${1:-}" = "--quiet" ] && shift
        [ -f "${FAKE_SYSTEMD_DIR:?}/$1" ]
        ;;
    *)
        echo "unexpected fake systemctl command: $command" >&2
        exit 1
        ;;
esac
SH
chmod 755 "$BIN/systemctl"

cat > "$BIN/loginctl" <<'SH'
#!/usr/bin/env bash
printf '%s\n' yes
SH
chmod 755 "$BIN/loginctl"

run_fake_install() {
    PATH="$BIN:$PATH" \
    HOME="$HOME_TARGET" \
    FAKE_SYSTEMD_STATE="$SYSTEMD_STATE" \
    FAKE_SYSTEMD_DIR="$HOME_TARGET/.config/systemd/user" \
    OPEN_CLOUD_HOME="$HOME_TARGET" \
    OPEN_CLOUD_FLEET_HOME="$FLEET" \
    OPEN_CLOUD_SYSTEMD_DIR="$HOME_TARGET/.config/systemd/user" \
    "$INSTALLER" --install >/dev/null
}

assert_runtime_updater() {
    local updater="$HOME_TARGET/.local/bin/opencloud-runtime-update"
    test -x "$updater"
    cmp -s "$ROOT/scripts/runtime-update.sh" "$updater"
}

assert_timer_state() {
    local unit="$1"
    local next
    test -f "$SYSTEMD_STATE/$unit.enabled"
    test -f "$SYSTEMD_STATE/$unit.active"
    next="$(cat "$SYSTEMD_STATE/$unit.next")"
    [ -n "$next" ] && [ "$next" != "infinity" ]
}

run_fake_install
assert_runtime_updater
assert_timer_state "$REGISTRY_TIMER"
assert_timer_state "$VERIFIER_TIMER"
echo "PASS fresh install enables, activates, and schedules both timers"

run_fake_install
assert_runtime_updater
[ "$(cat "$SYSTEMD_STATE/$REGISTRY_TIMER.restarts")" = "2" ]
[ "$(cat "$SYSTEMD_STATE/$VERIFIER_TIMER.restarts")" = "2" ]
echo "PASS repeated install re-arms both active timers idempotently"

printf '%s\n' infinity > "$SYSTEMD_STATE/$REGISTRY_TIMER.next"
printf '%s\n' infinity > "$SYSTEMD_STATE/$VERIFIER_TIMER.next"
run_fake_install
assert_timer_state "$REGISTRY_TIMER"
assert_timer_state "$VERIFIER_TIMER"
[ "$(cat "$SYSTEMD_STATE/$REGISTRY_TIMER.restarts")" = "3" ]
[ "$(cat "$SYSTEMD_STATE/$VERIFIER_TIMER.restarts")" = "3" ]
echo "PASS upgrade re-arms elapsed timers with finite next triggers"

if find "$SYSTEMD_STATE" -name '*.service.active' | grep -q .; then
    echo "FAIL installer synchronously launched a Fleet service" >&2
    exit 1
fi
echo "PASS timer rearm does not directly launch Fleet services"

# A gateway installed for a Hermes-native platform (e.g. Photon/iMessage)
# that is NOT reflected in channels.json must still receive the OpenCloud env
# drop-in so a clean reinstall preserves the self-repair environment.
mkdir -p "$HOME_TARGET/.config/systemd/user"
touch "$HOME_TARGET/.config/systemd/user/hermes-gateway.service"
run_fake_install
DROPIN="$HOME_TARGET/.config/systemd/user/hermes-gateway.service.d/opencloud.conf"
test -f "$DROPIN"
grep -qF "Environment=OPEN_CLOUD_SELF_REPAIR=1" "$DROPIN"
grep -qF "Environment=OPEN_CLOUD_REPAIR_STATE=" "$DROPIN"
echo "PASS gateway env drop-in written when gateway exists without messaging channel"

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

    next="$(
        systemctl --user show "$unit" \
            -p NextElapseUSecMonotonic --value 2>/dev/null || true
    )"

    [ "$active" = "active" ] || {
        echo "FAIL: $unit did not recover after restart" >&2
        exit 1
    }

    [ -n "$next" ] && [ "$next" != "infinity" ] && [ "$next" != "0" ] || {
        echo "FAIL: $unit has no finite next trigger after restart" >&2
        exit 1
    }

    echo "PASS $unit restart recovery with finite next trigger"
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
