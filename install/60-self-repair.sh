#!/usr/bin/env bash
set -euo pipefail

export PATH="$HOME/.local/bin:$HOME/.opencode/bin:$HOME/.bun/bin:$HOME/bin:$PATH"

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MODE="${1:---dry-run}"
HARNESS="$ROOT/integrations/self-repair/hermes-code-repair"
AGENT="$ROOT/integrations/self-repair/hermes-repair-agent.md"

case "$MODE" in
    --dry-run)
        if command -v opencode >/dev/null 2>&1; then
            echo "OPENCODE: ALREADY_PRESENT"
        else
            echo "OPENCODE: WOULD_INSTALL"
        fi
        echo "SELF_REPAIR_HARNESS: WOULD_INSTALL"
        echo "SELF_REPAIR_AGENT: WOULD_INSTALL"
        echo "SELF_REPAIR_MODE: STAGING_VALIDATE_BACKUP_ROLLBACK"
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
