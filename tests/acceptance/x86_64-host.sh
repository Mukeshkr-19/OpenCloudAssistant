#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

if [ "${OPEN_CLOUD_ACCEPT_X86_DESTRUCTIVE:-0}" != "1" ]; then
    echo "REFUSED: this test performs a real Open Cloud Assistant installation." >&2
    echo "Run only on a disposable fresh x86_64 Ubuntu acceptance host with:" >&2
    echo "OPEN_CLOUD_ACCEPT_X86_DESTRUCTIVE=1 ./tests/acceptance/x86_64-host.sh" >&2
    exit 2
fi

REPORT="${OPEN_CLOUD_ACCEPTANCE_REPORT:-/tmp/opencloud-x86-acceptance-$(date -u +%Y%m%dT%H%M%SZ).log}"

exec > >(tee "$REPORT") 2>&1

echo "============================================================"
echo " OPEN CLOUD ASSISTANT — REAL X86_64 ACCEPTANCE"
echo "============================================================"

echo
echo "========== HOST =========="

ARCH="$(uname -m)"
echo "Architecture: $ARCH"

[ "$ARCH" = "x86_64" ] || {
    echo "FAIL: this acceptance test requires x86_64" >&2
    exit 1
}

test -f /etc/os-release
. /etc/os-release

echo "OS: ${PRETTY_NAME:-unknown}"

[ "${ID:-}" = "ubuntu" ] || {
    echo "FAIL: host is not Ubuntu" >&2
    exit 1
}

case "${VERSION_ID:-}" in
    24.*)
        ;;
    *)
        echo "FAIL: Ubuntu 24.x required for this acceptance run" >&2
        exit 1
        ;;
esac

echo
echo "Memory:"
free -h || true

echo
echo "Kernel:"
uname -a

echo
echo "Repository:"
git log -1 --oneline

echo
echo "========== CLEAN-HOST CONTRACT =========="

CLEAN_RC=0

for path in     "$HOME/.hermes"     "$HOME/.opencloud"     "$HOME/.local/share/hermes-fleet"     "$HOME/.config/hermes-vellum"
do
    if [ -e "$path" ]; then
        echo "FAIL: pre-existing OpenCloud/Hermes state: $path"
        CLEAN_RC=1
    fi
done

if [ "$CLEAN_RC" -ne 0 ]; then
    echo "REFUSED: host is not clean enough for fresh-install acceptance"
    exit 1
fi

echo "PASS: no pre-existing OpenCloud runtime state"

echo
echo "========== PREREQUISITES =========="

for cmd in     git     curl     xz     unzip     python3     sudo     systemctl     loginctl     rsync
do
    command -v "$cmd" >/dev/null || {
        echo "FAIL: missing prerequisite: $cmd" >&2
        exit 1
    }

    echo "PASS prerequisite: $cmd"
done

python3 -c "import yaml" || {
    echo "FAIL: Python yaml module unavailable" >&2
    exit 1
}

echo "PASS prerequisite: python3-yaml"

echo
echo "========== FIRST INSTALL =========="

FIRST_START="$(date +%s%N)"

set +e

OPEN_CLOUD_CHANNELS=cli     ./setup.sh --install     > /tmp/opencloud-x86-first-install.log 2>&1

FIRST_RC=$?

set -e

cat /tmp/opencloud-x86-first-install.log

FIRST_END="$(date +%s%N)"

FIRST_MS="$(
    python3 -c "print(round(($FIRST_END - $FIRST_START) / 1000000, 3))"
)"

echo
echo "FIRST_INSTALL_RC: $FIRST_RC"
echo "MEASURE first_install_ms=$FIRST_MS"

[ "$FIRST_RC" -eq 0 ]

grep -qF     "SETUP_INSTALL: PASS"     /tmp/opencloud-x86-first-install.log

echo "FIRST_INSTALL: PASS"

echo
echo "========== FIRST DOCTOR =========="

./bin/opencloud doctor     > /tmp/opencloud-x86-doctor-first.log 2>&1

cat /tmp/opencloud-x86-doctor-first.log

grep -qF     "DOCTOR_STATUS: PASS"     /tmp/opencloud-x86-doctor-first.log

echo "FIRST_DOCTOR: PASS"

echo
echo "========== SERVICE STATE =========="

for unit in     hermes-fleet-registry.timer     hermes-fleet-verifier.timer
do
    ENABLED="$(
        systemctl --user is-enabled "$unit" 2>/dev/null || true
    )"

    ACTIVE="$(
        systemctl --user is-active "$unit" 2>/dev/null || true
    )"

    echo "$unit enabled=$ENABLED active=$ACTIVE"

    [ "$ENABLED" = "enabled" ]
    [ "$ACTIVE" = "active" ]
done

LINGER="$(
    loginctl show-user "$USER" -p Linger --value 2>/dev/null || true
)"

echo "Linger: $LINGER"

[ "$LINGER" = "yes" ]

echo "SERVICE_STATE: PASS"

echo
echo "========== SECOND INSTALL / IDEMPOTENCY =========="

SECOND_START="$(date +%s%N)"

set +e

OPEN_CLOUD_CHANNELS=cli     ./setup.sh --install     > /tmp/opencloud-x86-second-install.log 2>&1

SECOND_RC=$?

set -e

cat /tmp/opencloud-x86-second-install.log

SECOND_END="$(date +%s%N)"

SECOND_MS="$(
    python3 -c "print(round(($SECOND_END - $SECOND_START) / 1000000, 3))"
)"

echo
echo "SECOND_INSTALL_RC: $SECOND_RC"
echo "MEASURE second_install_ms=$SECOND_MS"

[ "$SECOND_RC" -eq 0 ]

grep -qF     "SETUP_INSTALL: PASS"     /tmp/opencloud-x86-second-install.log

grep -qF     "HERMES_LIVE_INSTALL: ALREADY_PRESENT"     /tmp/opencloud-x86-second-install.log

echo "SECOND_INSTALL_IDEMPOTENCY: PASS"

echo
echo "========== SECOND DOCTOR =========="

./bin/opencloud doctor     > /tmp/opencloud-x86-doctor-second.log 2>&1

cat /tmp/opencloud-x86-doctor-second.log

grep -qF     "DOCTOR_STATUS: PASS"     /tmp/opencloud-x86-doctor-second.log

echo "SECOND_DOCTOR: PASS"

echo
echo "========== PUBLIC SMOKE =========="

./tests/smoke/run.sh

echo "X86_PUBLIC_SMOKE: PASS"

echo
echo "========== RELIABILITY SUITE =========="

OPEN_CLOUD_HERMES_ROOT="$HOME/.hermes/hermes-agent"     ./tests/reliability/run.sh

echo "X86_RELIABILITY: PASS"

echo
echo "========== PUBLIC AUDIT =========="

./scripts/public-audit.sh

echo "X86_PUBLIC_AUDIT: PASS"

echo
echo "========== REPOSITORY INTEGRITY =========="

if [ -n "$(git status --porcelain)" ]; then
    echo "FAIL: acceptance modified repository source"
    git status --short
    exit 1
fi

echo "REPOSITORY_INTEGRITY: PASS"

echo
echo "============================================================"
echo " REAL_X86_64_ACCEPTANCE: PASS"
echo "============================================================"

echo
echo "Architecture:                $ARCH"
echo "Operating system:            ${PRETTY_NAME:-unknown}"
echo "Fresh install:               PASS"
echo "First doctor:                PASS"
echo "Second install/idempotency:  PASS"
echo "Second doctor:               PASS"
echo "Fleet timers:                PASS"
echo "User lingering:              PASS"
echo "Smoke suite:                 PASS"
echo "Reliability suite:           PASS"
echo "Public audit:                PASS"
echo "Repository integrity:        PASS"
echo "First install ms:            $FIRST_MS"
echo "Second install ms:           $SECOND_MS"
echo
echo "Acceptance report: $REPORT"
