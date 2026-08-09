#!/usr/bin/env bash
set -euo pipefail

# Open Cloud Assistant portable user PATH
export PATH="$HOME/.local/bin:$HOME/.bun/bin:$HOME/bin:$PATH"


FAILURES=0

pass() {
    printf "PASS  %s\n" "$1"
}

fail() {
    printf "FAIL  %s\n" "$1"
    FAILURES=$((FAILURES + 1))
}

echo "Open Cloud Assistant preflight"
echo

if [ "$(uname -s)" = "Linux" ]; then
    pass "Linux host"
else
    fail "Linux host required for reference server install"
fi

if [ -r /etc/os-release ]; then
    . /etc/os-release
    if [ "${ID:-}" = "ubuntu" ]; then
        pass "Ubuntu detected"
    else
        fail "Reference installer currently targets Ubuntu"
    fi
else
    fail "Unable to identify Linux distribution"
fi

ARCH="$(uname -m)"
case "$ARCH" in
    aarch64|arm64)
        pass "ARM64 architecture"
        ;;
    x86_64|amd64)
        pass "x86_64 architecture"
        ;;
    *)
        fail "Unsupported architecture: $ARCH"
        ;;
esac

for cmd in git curl xz unzip python3; do
    if command -v "$cmd" >/dev/null 2>&1; then
        pass "dependency: $cmd"
    else
        fail "dependency missing: $cmd"
    fi
done

echo
if [ "$FAILURES" -eq 0 ]; then
    echo "PREFLIGHT_STATUS: PASS"
    exit 0
fi

echo "PREFLIGHT_STATUS: FAIL ($FAILURES)"
exit 1
