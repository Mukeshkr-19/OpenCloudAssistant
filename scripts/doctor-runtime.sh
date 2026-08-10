#!/usr/bin/env bash
set -euo pipefail

TARGET_HOME="${OPEN_CLOUD_HOME:-$HOME}"

OPEN_DIR="$TARGET_HOME/.opencloud"
CONFIG="$OPEN_DIR/config.env"
CHANNELS="$OPEN_DIR/channels.json"

FLEET="${OPEN_CLOUD_FLEET_HOME:-$TARGET_HOME/.local/share/hermes-fleet}"
HERMES_CONFIG="$TARGET_HOME/.hermes/config.yaml"
VELLUM_BRIDGE="$TARGET_HOME/.config/hermes-vellum/mcp/server.py"
REPAIR_HELPER="$TARGET_HOME/.local/bin/hermes-code-repair"

FAIL=0

pass() {
    printf "PASS  %-24s %s\n" "$1" "${2:-}"
}

fail() {
    printf "FAIL  %-24s %s\n" "$1" "${2:-}"
    FAIL=$((FAIL + 1))
}

skip() {
    printf "SKIP  %-24s %s\n" "$1" "${2:-not present}"
}

echo "Runtime integrity"

if [ -d "$OPEN_DIR" ]; then

    mode="$(stat -c "%a" "$OPEN_DIR" 2>/dev/null || true)"

    if [ "$mode" = "700" ]; then
        pass "OpenCloud directory" "permissions 700"
    else
        fail "OpenCloud directory" "expected mode 700; run chmod 700 $OPEN_DIR"
    fi

else
    skip "OpenCloud directory"
fi

if [ -f "$CONFIG" ]; then

    mode="$(stat -c "%a" "$CONFIG" 2>/dev/null || true)"

    if [ "$mode" = "600" ]; then
        pass "Provider config" "permissions 600"
    else
        fail "Provider config" "expected mode 600; run chmod 600 $CONFIG"
    fi

else
    skip "Provider config"
fi

if [ -f "$CHANNELS" ]; then

    if python3 -c "import json,sys; json.load(open(sys.argv[1]))" "$CHANNELS" >/dev/null 2>&1; then
        pass "Channel state" "valid JSON"
    else
        fail "Channel state" "invalid JSON: $CHANNELS"
    fi

else
    skip "Channel state"
fi

if [ -d "$FLEET" ]; then

    for file in fleet.json registry/models.json; do

        if [ -f "$FLEET/$file" ] && \
           python3 -c "import json,sys; json.load(open(sys.argv[1]))" "$FLEET/$file" >/dev/null 2>&1
        then
            pass "Fleet $file" "valid JSON"
        else
            fail "Fleet $file" "missing or invalid"
        fi
    done

    if [ -f "$FLEET/health.sqlite" ]; then

        if python3 -c "import sqlite3,sys; c=sqlite3.connect(sys.argv[1]); r=c.execute(\"PRAGMA quick_check\").fetchone(); c.close(); raise SystemExit(0 if r and r[0] == \"ok\" else 1)" "$FLEET/health.sqlite"
        then
            pass "Fleet health DB" "SQLite quick_check ok"
        else
            fail "Fleet health DB" "SQLite integrity check failed"
        fi

    else
        skip "Fleet health DB"
    fi

else
    fail "Fleet runtime" "missing: reinstall with ./setup.sh --install"
fi

if [ -f "$HERMES_CONFIG" ]; then

    if python3 -c "import yaml,sys; d=yaml.safe_load(open(sys.argv[1])); raise SystemExit(0 if isinstance(d,dict) else 1)" "$HERMES_CONFIG" >/dev/null 2>&1
    then
        pass "Hermes config" "valid YAML"
    else
        fail "Hermes config" "invalid YAML: $HERMES_CONFIG"
    fi

else
    fail "Hermes config" "missing"
fi

if [ -f "$VELLUM_BRIDGE" ]; then

    if python3 -c "import sys; compile(open(sys.argv[1]).read(),sys.argv[1],\"exec\")" "$VELLUM_BRIDGE" >/dev/null 2>&1
    then
        pass "Vellum bridge" "Python syntax valid"
    else
        fail "Vellum bridge" "invalid Python: $VELLUM_BRIDGE"
    fi

else
    fail "Vellum bridge" "missing; rerun ./setup.sh --install"
fi

if [ -x "$REPAIR_HELPER" ]; then
    pass "Repair helper" "executable"
else
    fail "Repair helper" "missing; rerun install/60-self-repair.sh --install"
fi

if [ "$FAIL" -eq 0 ]; then
    echo "RUNTIME_DOCTOR: PASS"
    exit 0
fi

echo "RUNTIME_DOCTOR: FAIL ($FAIL checks failed)"
exit 1
