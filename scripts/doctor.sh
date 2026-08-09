#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CONFIG="${OPEN_CLOUD_CONFIG:-$HOME/.opencloud/config.env}"
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

echo "Open Cloud Assistant Doctor"
echo "==========================="
echo

if [ "$(uname -s)" = "Linux" ]; then
    pass "Operating system" "Linux"
else
    fail "Operating system" "Linux required for server reference install"
fi

if [ -r /etc/os-release ]; then
    . /etc/os-release
    if [ "${ID:-}" = "ubuntu" ]; then
        pass "Distribution" "Ubuntu ${VERSION_ID:-}"
    else
        fail "Distribution" "reference installer currently targets Ubuntu"
    fi
else
    fail "Distribution" "unknown"
fi

ARCH="$(uname -m)"
case "$ARCH" in
    aarch64|arm64)
        pass "Architecture" "ARM64"
        ;;
    x86_64|amd64)
        pass "Architecture" "x86_64"
        ;;
    *)
        fail "Architecture" "$ARCH"
        ;;
esac

for cmd in git curl python3; do
    if command -v "$cmd" >/dev/null 2>&1; then
        pass "$cmd" "available"
    else
        fail "$cmd" "missing"
    fi
done

if command -v bun >/dev/null 2>&1; then
    pass "Bun" "available"
else
    fail "Bun" "required for Vellum installation"
fi

if command -v hermes >/dev/null 2>&1; then
    if hermes version >/dev/null 2>&1; then
        pass "Hermes" "installed"
    else
        fail "Hermes" "command exists but version check failed"
    fi
else
    fail "Hermes" "not installed"
fi

if command -v vellum >/dev/null 2>&1; then
    if vellum --help >/dev/null 2>&1; then
        pass "Vellum" "installed"
    else
        fail "Vellum" "command exists but CLI check failed"
    fi
else
    fail "Vellum" "not installed"
fi

if [ -d "$ROOT/.git" ]; then
    pass "OpenCloud source" "present"
else
    fail "OpenCloud source" "missing"
fi

if has_value TELEGRAM_BOT_TOKEN; then
    pass "Telegram" "configured"
else
    skip "Telegram"
fi

if has_value DISCORD_BOT_TOKEN; then
    pass "Discord" "configured"
else
    skip "Discord"
fi

if has_value API_SERVER_KEY; then
    pass "Browser API" "configured"
else
    skip "Browser API"
fi

skip "iMessage" "optional Apple integration"

echo
if [ "$FAILURES" -eq 0 ]; then
    echo "DOCTOR_STATUS: PASS"
    exit 0
fi

echo "DOCTOR_STATUS: FAIL ($FAILURES required checks failed)"
exit 1
