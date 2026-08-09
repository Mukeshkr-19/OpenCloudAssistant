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

has_value() {
    local key="$1"
    [ -f "$CONFIG" ] || return 1
    grep -Eq "^${key}=.+" "$CONFIG"
}

POLICY="$ROOT/config/fleet/hermes-fleet-policy.json"

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

if [ -f "$BASE/registry/refresh.py" ] && [ -f "$BASE/registry/verify.py" ]; then
    pass "Fleet discovery" "installed"
else
    skip "Fleet discovery" "not installed yet"
fi

if [ -f "$CONFIG" ]; then
    MODE="$(stat -c %a "$CONFIG" 2>/dev/null || echo unknown)"

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
