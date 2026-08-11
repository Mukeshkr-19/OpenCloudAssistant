#!/usr/bin/env bash
set -euo pipefail

export PATH="$HOME/.local/bin:$HOME/.opencode/bin:$HOME/.bun/bin:$HOME/bin:$PATH"

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MODE="${1:---dry-run}"
HARNESS="$ROOT/integrations/self-repair/hermes-code-repair"
AGENT="$ROOT/integrations/self-repair/hermes-repair-agent.md"

ensure_bwrap_apparmor_profile() {
    local restriction=""
    local source="/usr/share/apparmor/extra-profiles/bwrap-userns-restrict"
    local target="/etc/apparmor.d/bwrap-userns-restrict"
    local -a elevate=()

    if [ -r /proc/sys/kernel/apparmor_restrict_unprivileged_userns ]; then
        restriction="$(
            cat /proc/sys/kernel/apparmor_restrict_unprivileged_userns
        )"
    fi

    if [ "$restriction" != "1" ]; then
        echo "BWRAP_APPARMOR_PROFILE: NOT_REQUIRED"
        return 0
    fi

    command -v apparmor_parser >/dev/null 2>&1 || {
        echo "ERROR: apparmor_parser missing" >&2
        return 1
    }

    if [ ! -f "$target" ] && [ ! -f "$source" ]; then
        echo "ERROR: Ubuntu bwrap AppArmor profile missing" >&2
        echo "Install the apparmor-profiles package." >&2
        return 1
    fi

    if [ "$EUID" -eq 0 ]; then
        elevate=()
    elif command -v sudo >/dev/null 2>&1; then
        elevate=(sudo)
    else
        echo "ERROR: enabling Ubuntu bwrap AppArmor profile requires root or sudo" >&2
        return 1
    fi

    if [ ! -f "$target" ]; then
        "${elevate[@]}" install -m 0644 "$source" "$target"
    fi

    "${elevate[@]}" apparmor_parser -r "$target"

    echo "BWRAP_APPARMOR_PROFILE: ENABLED"
}

case "$MODE" in
    --dry-run)
        if command -v opencode >/dev/null 2>&1; then
            echo "OPENCODE: ALREADY_PRESENT"
        else
            echo "OPENCODE: WOULD_INSTALL"
        fi
        if command -v bwrap >/dev/null 2>&1; then
            echo "BUBBLEWRAP: ALREADY_PRESENT"
        else
            echo "BUBBLEWRAP: PROVIDED_BY_UBUNTU_PREFLIGHT"
        fi
        echo "SELF_REPAIR_OS_SANDBOX: BUBBLEWRAP"
        echo "APPARMOR_BWRAP_PROFILE: REQUIRED_ON_RESTRICTED_UBUNTU"
        echo "SELF_REPAIR_HARNESS: WOULD_INSTALL"
        echo "SELF_REPAIR_AGENT: WOULD_INSTALL"
        echo "SELF_REPAIR_MODE: STAGING_VALIDATE_BACKUP_ROLLBACK"
        echo "SELF_REPAIR_SANDBOX_BOUNDARY: FILESYSTEM_PROCESS_NAMESPACE"
        ;;
    --install)
        if ! command -v opencode >/dev/null 2>&1; then
            curl -fsSL https://opencode.ai/install | bash
            export PATH="$HOME/.local/bin:$HOME/.opencode/bin:$HOME/.bun/bin:$PATH"
        fi

        command -v opencode >/dev/null 2>&1 || {
            echo "ERROR: OpenCode installation failed" >&2
            exit 1
        }

        command -v bwrap >/dev/null 2>&1 || {
            echo "ERROR: bubblewrap prerequisite missing; run the supported setup installer" >&2
            exit 1
        }

        ensure_bwrap_apparmor_profile

        install -d -m 700 "$HOME/.config/opencode/agents"
        install -d -m 755 "$HOME/.local/bin"
        install -m 600 "$AGENT" "$HOME/.config/opencode/agents/hermes-repair.md"
        install -m 755 "$HARNESS" "$HOME/.local/bin/hermes-code-repair"
        "$HOME/.local/bin/hermes-code-repair" --self-test
        echo "SELF_REPAIR_INSTALL: PASS"
        ;;
    *)
        echo "Usage: $0 [--dry-run|--install]" >&2
        exit 2
        ;;
esac
