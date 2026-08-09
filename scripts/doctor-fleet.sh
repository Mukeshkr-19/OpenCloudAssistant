#!/usr/bin/env bash
set -euo pipefail

export PATH="$HOME/.local/bin:$HOME/.bun/bin:$HOME/bin:$PATH"

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CONFIG="${OPEN_CLOUD_CONFIG:-$HOME/.opencloud/config.env}"
BASE="${OPEN_CLOUD_FLEET_HOME:-$HOME/.local/share/hermes-fleet}"
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
        if python3 -c "import ast,sys; ast.parse(open(sys.argv[1],encoding=\"utf-8\").read())" "$BASE/dispatcher.py" >/dev/null 2>&1 && python3 -m json.tool "$BASE/fleet.json" >/dev/null 2>&1; then
            pass "Fleet runtime" "installed"
        else
            fail "Fleet runtime" "installed but invalid"
        fi
    else
        fail "Fleet runtime" "partial installation"
    fi
else
    skip "Fleet runtime" "not installed yet"
fi

if [ -f "$BASE/registry/models.json" ]; then
    pass "Fleet registry" "runtime registry present"
else
    skip "Fleet registry" "awaiting discovery"
fi

if has_value NVIDIA_API_KEY; then
    pass "NVIDIA provider" "credential configured"
else
    skip "NVIDIA provider" "credential wizard not completed"
fi

if has_value OPENROUTER_API_KEY; then
    pass "OpenRouter provider" "credential configured"
else
    skip "OpenRouter provider" "credential wizard not completed"
fi

if command -v opencode >/dev/null 2>&1; then
    pass "Zen client" "OpenCode available"
else
    skip "Zen client" "optional"
fi

skip "Gemini lane" "disabled until independently verified"

if [ "$FAILURES" -ne 0 ]; then
    exit 1
fi

exit 0
