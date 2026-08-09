#!/usr/bin/env bash
set -euo pipefail

TARGET_HOME="${OPEN_CLOUD_HOME:-$HOME}"
STATE_DIR="${OPEN_CLOUD_STATE_DIR:-$TARGET_HOME/.opencloud/state}"
CHANNELS="${OPEN_CLOUD_CHANNELS_STATE:-$TARGET_HOME/.opencloud/channels.json}"
MARKER="$STATE_DIR/services-installed"
FAIL=0

pass() {
    printf "PASS  %-24s %s\n" "$1" "$2"
}

fail() {
    printf "FAIL  %-24s %s\n" "$1" "$2"
    FAIL=1
}

skip() {
    printf "SKIP  %-24s %s\n" "$1" "$2"
}

selected_channels() {
    [ -f "$CHANNELS" ] || return 0
    python3 -c "import json,sys; d=json.load(open(sys.argv[1])); print(\",\".join(d.get(\"selected\",[]) if not d.get(\"deferred\") else []))" "$CHANNELS"
}

if [ ! -f "$MARKER" ]; then
    skip "Always-on services" "public service stage not installed yet"
    exit 0
fi

for unit in hermes-fleet-registry.timer hermes-fleet-verifier.timer; do
    if systemctl --user is-enabled "$unit" >/dev/null 2>&1 && systemctl --user is-active "$unit" >/dev/null 2>&1; then
        pass "$unit" "enabled and active"
    else
        fail "$unit" "not enabled and active"
    fi
done

SELECTED="$(selected_channels)"

case ",$SELECTED," in
    *,telegram,*|*,discord,*|*,advanced,*|*,imessage,*)
        if systemctl --user is-active hermes-gateway.service >/dev/null 2>&1; then
            pass "Hermes gateway" "active for selected messaging channel"
        else
            fail "Hermes gateway" "required by selected messaging channel but inactive"
        fi
        ;;
    *)
        skip "Hermes gateway" "not required by selected channels"
        ;;
esac

case ",$SELECTED," in
    *,browser,*)
        fail "Browser runtime" "selected; end-to-end browser service still requires release validation"
        ;;
    *)
        skip "Browser runtime" "not selected"
        ;;
esac

LINGER="$(loginctl show-user "$USER" -p Linger --value 2>/dev/null || true)"

if [ "$LINGER" = "yes" ]; then
    pass "User service linger" "enabled for boot persistence"
else
    fail "User service linger" "enable with sudo loginctl enable-linger $USER"
fi

exit "$FAIL"
