#!/usr/bin/env bash
set -euo pipefail

export PATH="$HOME/.local/bin:$HOME/.bun/bin:$HOME/bin:$PATH"

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CONFIG="${OPEN_CLOUD_CONFIG:-$HOME/.opencloud/config.env}"
BASE="${OPEN_CLOUD_FLEET_HOME:-$HOME/.local/share/hermes-fleet}"
REGISTRY="$BASE/registry/models.json"
FAILURES=0

pass() {
    printf "PASS  %-24s %s\n" "$1" "${2:-}"
}

fail() {
    printf "FAIL  %-24s %s\n" "$1" "${2:-}"
    FAILURES=$((FAILURES + 1))
}

skip() {
    printf "SKIP  %-24s %s\n" "$1" "${2:-not configured}"
}

file_mode() {
    stat -c "%a" "$1" 2>/dev/null || stat -f "%Lp" "$1" 2>/dev/null || echo unknown
}

# Provider keys may live in OpenCloud config (preferred) or Hermes .env
# (gateway/fleet refresh load Hermes credentials via hermes_cli). Doctor must
# not SKIP "not configured" when the live runtime already has keys.
HERMES_ENV="${HERMES_HOME:-$HOME/.hermes}/.env"

has_value() {
    local key="$1"
    if [ -f "$CONFIG" ] && grep -Eq "^${key}=.+" "$CONFIG"; then
        return 0
    fi
    if [ -f "$HERMES_ENV" ] && grep -Eq "^${key}=.+" "$HERMES_ENV"; then
        return 0
    fi
    return 1
}

POLICY="$ROOT/config/fleet/hermes-fleet-policy.json"

TTL_VALUE="${OPEN_CLOUD_MODEL_VERIFICATION_TTL_SECONDS:-}"
if [ -z "$TTL_VALUE" ] && [ -f "$CONFIG" ]; then
    TTL_VALUE="$(sed -n 's/^OPEN_CLOUD_MODEL_VERIFICATION_TTL_SECONDS=//p' "$CONFIG" | tail -1)"
fi
TTL_VALUE="${TTL_VALUE:-86400}"
if OPEN_CLOUD_MODEL_VERIFICATION_TTL_SECONDS="$TTL_VALUE" PYTHONPATH="$ROOT/integrations/fleet" \
    python3 -c 'from fleet_runtime import verification_ttl_ms; verification_ttl_ms()' >/dev/null 2>&1; then
    if [ "$TTL_VALUE" = "0" ]; then
        pass "Verification TTL" "0; re-probe on every verifier run"
    else
        pass "Verification TTL" "valid"
    fi
else
    fail "Verification TTL" "must be an integer from 0 through 31536000 seconds"
fi

if [ -f "$POLICY" ] && python3 -m json.tool "$POLICY" >/dev/null 2>&1; then
    pass "Fleet policy" "valid"
else
    fail "Fleet policy" "missing or invalid"
fi

if [ -f "$BASE/dispatcher.py" ] || [ -f "$BASE/fleet.json" ]; then
    if [ -f "$BASE/dispatcher.py" ] && [ -f "$BASE/fleet.json" ]; then
        pass "Fleet runtime" "installed"
    else
        fail "Fleet runtime" "partial installation"
    fi
else
    skip "Fleet runtime" "not installed yet"
fi

if [ -f "$BASE/session-pin.key" ] && [ "$(wc -c < "$BASE/session-pin.key")" -ge 32 ]; then
    MODE="$(file_mode "$BASE/session-pin.key")"
    if [ "$MODE" = "600" ]; then
        pass "Fleet session pin" "valid key; permissions 600"
    else
        fail "Fleet session pin" "expected permissions 600; found $MODE"
    fi
elif [ -d "$BASE" ]; then
    fail "Fleet session pin" "missing or shorter than 32 bytes"
else
    skip "Fleet session pin" "runtime not installed"
fi

if [ -f "$BASE/registry/refresh.py" ] && [ -f "$BASE/registry/verify.py" ]; then
    pass "Fleet discovery" "installed"
else
    skip "Fleet discovery" "not installed yet"
fi

if [ -f "$CONFIG" ]; then
    MODE="$(file_mode "$CONFIG")"

    if [ "$MODE" = "600" ]; then
        pass "Provider config perms" "600"
    else
        fail "Provider config perms" "expected 600; found $MODE"
    fi
fi

NVIDIA_COUNT=0
ZEN_COUNT=0
OPENROUTER_OK=false
VERIFY_RECORDED=false

if [ -f "$REGISTRY" ]; then
    read -r NVIDIA_COUNT ZEN_COUNT OPENROUTER_OK VERIFY_RECORDED < <(
        python3 -c "
import json,sys
d=json.load(open(sys.argv[1]))
prod=d.get(\"productionModels\",{}) or {}
status=d.get(\"providerStatus\",{}) or {}
orr=status.get(\"openrouter\",{}) or {}
print(len(prod.get(\"nvidia\",[]) or []), len(prod.get(\"zen\",[]) or []), \"true\" if orr.get(\"ok\",False) else \"false\", \"true\" if d.get(\"lastVerificationRunMs\",0) else \"false\")
" "$REGISTRY"
    )

    pass "Fleet registry" "runtime state present"
else
    skip "Fleet registry" "awaiting first refresh"
fi

if has_value NVIDIA_API_KEY; then
    if [ "$NVIDIA_COUNT" -gt 0 ] && [ "$VERIFY_RECORDED" = "true" ]; then
        pass "NVIDIA provider" "verified dynamic capacity available"
    else
        fail "NVIDIA provider" "configured but no verified capacity; run opencloud fleet refresh"
    fi
else
    skip "NVIDIA provider" "not configured"
fi

if has_value OPENROUTER_API_KEY; then
    if [ "$OPENROUTER_OK" = "true" ]; then
        pass "OpenRouter provider" "runtime discovery healthy; route openrouter/free"
    else
        fail "OpenRouter provider" "configured but runtime discovery is not healthy"
    fi
else
    skip "OpenRouter provider" "not configured"
fi

if command -v opencode >/dev/null 2>&1; then
    if [ "$ZEN_COUNT" -gt 0 ]; then
        pass "Zen provider" "verified free capacity available"
    else
        skip "Zen provider" "optional; no verified free capacity currently"
    fi
else
    skip "Zen provider" "optional client not installed"
fi

skip "Gemini lane" "blocked until independently verified"

if [ "$FAILURES" -ne 0 ]; then
    exit 1
fi

exit 0
