#!/usr/bin/env bash
set -euo pipefail

MODE="${1:---check}"
TEST_MISSING="${OPEN_CLOUD_PREFLIGHT_TEST_MISSING:-}"
TEST_MODE="${OPEN_CLOUD_PREFLIGHT_TEST_MODE:-0}"

HOST_FAILURES=0
MISSING=()

pass() {
    printf "PASS  %-24s %s\n" "$1" "${2:-}"
}

fail() {
    printf "FAIL  %-24s %s\n" "$1" "${2:-}"
    HOST_FAILURES=$((HOST_FAILURES + 1))
}

test_missing() {
    local pkg="$1"

    case " $TEST_MISSING " in
        *" $pkg "*)
            return 0
            ;;
    esac

    return 1
}

package_installed() {
    local pkg="$1"

    if test_missing "$pkg"; then
        return 1
    fi

    dpkg-query \
        -W \
        -f="\${Status}" \
        "$pkg" \
        2>/dev/null \
        | grep -qF "install ok installed"
}

echo "Open Cloud Assistant preflight"
echo

if [ "$(uname -s)" = "Linux" ]; then
    pass "Linux host"
else
    fail "Linux host" "Linux required"
fi

if [ -r /etc/os-release ]; then
    . /etc/os-release

    if [ "${ID:-}" = "ubuntu" ]; then
        pass "Ubuntu detected" "${VERSION_ID:-unknown}"
    else
        fail "Ubuntu detected" "reference installer currently targets Ubuntu"
    fi
else
    fail "Ubuntu detected" "/etc/os-release unavailable"
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
        fail "Architecture" "$ARCH is unsupported"
        ;;
esac

PACKAGES=(
    ca-certificates
    curl
    git
    xz-utils
    unzip
    python3
    python3-venv
    python3-pip
    python3-yaml
    dbus-user-session
    procps
    rsync
)

for pkg in "${PACKAGES[@]}"; do

    if package_installed "$pkg"; then

        pass "package: $pkg"

    else

        MISSING+=("$pkg")

        case "$MODE" in
            --dry-run)
                echo "WOULD_INSTALL prerequisite: $pkg"
                ;;

            --install)
                echo "MISSING prerequisite: $pkg"
                ;;

            *)
                echo "FAIL  package: $pkg          missing"
                ;;
        esac
    fi
done

if [ "$HOST_FAILURES" -ne 0 ]; then
    echo
    echo "PREFLIGHT_STATUS: FAIL"
    exit 1
fi

case "$MODE" in

    --dry-run)

        echo
        echo "PREFLIGHT_STATUS: PASS"
        ;;

    --check)

        if [ "${#MISSING[@]}" -ne 0 ]; then
            echo
            echo "PREFLIGHT_STATUS: FAIL (${#MISSING[@]} prerequisites missing)"
            exit 1
        fi

        echo
        echo "PREFLIGHT_STATUS: PASS"
        ;;

    --install)

        if [ "${#MISSING[@]}" -eq 0 ]; then
            echo
            echo "PREFLIGHT_BOOTSTRAP: ALREADY_SATISFIED"
            echo "PREFLIGHT_STATUS: PASS"
            exit 0
        fi

        if [ "$TEST_MODE" = "1" ]; then
            echo "PREFLIGHT_TEST_INSTALL: ${MISSING[*]}"
            echo "PREFLIGHT_STATUS: PASS"
            exit 0
        fi

        if [ "$EUID" -eq 0 ]; then
            APT=(apt-get)
        elif command -v sudo >/dev/null 2>&1; then
            APT=(sudo apt-get)
        else
            echo "ERROR: missing prerequisites require root privileges." >&2
            echo "Run as root or install sudo first." >&2
            exit 1
        fi

        echo
        echo "Installing missing Ubuntu prerequisites:"
        printf "  %s\n" "${MISSING[@]}"

        "${APT[@]}" update
        DEBIAN_FRONTEND=noninteractive \
            "${APT[@]}" install -y "${MISSING[@]}"

        for pkg in "${MISSING[@]}"; do
            package_installed "$pkg" || {
                echo "ERROR: prerequisite installation failed: $pkg" >&2
                exit 1
            }
        done

        echo
        echo "PREFLIGHT_BOOTSTRAP: PASS"
        echo "PREFLIGHT_STATUS: PASS"
        ;;

    -h|--help|help)

        echo "Usage:"
        echo "  install/00-preflight.sh --check"
        echo "  install/00-preflight.sh --dry-run"
        echo "  install/00-preflight.sh --install"
        ;;

    *)

        echo "ERROR: unknown preflight mode: $MODE" >&2
        exit 2
        ;;
esac
