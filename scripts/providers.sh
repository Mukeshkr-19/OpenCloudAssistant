#!/usr/bin/env bash
set -euo pipefail

CONFIG="${OPEN_CLOUD_CONFIG:-$HOME/.opencloud/config.env}"
DIR="$(dirname "$CONFIG")"
COMMAND="${1:-status}"

has_value() {
    local key="$1"
    [ -f "$CONFIG" ] || return 1
    grep -Eq "^${key}=.+" "$CONFIG"
}

set_key() {
    local key="$1"
    local value="$2"
    local tmp

    mkdir -p "$DIR"
    umask 077
    touch "$CONFIG"
    chmod 600 "$CONFIG"

    tmp="$(mktemp "$DIR/config.env.XXXXXX")"

    grep -v "^${key}=" "$CONFIG" > "$tmp" || true
    printf "%s=%s\n" "$key" "$value" >> "$tmp"

    chmod 600 "$tmp"
    mv "$tmp" "$CONFIG"
}

status() {
    echo "Open Cloud Assistant providers"

    if has_value NVIDIA_API_KEY; then
        echo "NVIDIA:      CONFIGURED"
    else
        echo "NVIDIA:      NOT CONFIGURED"
    fi

    if has_value OPENROUTER_API_KEY; then
        echo "OpenRouter:   CONFIGURED"
    else
        echo "OpenRouter:   NOT CONFIGURED"
    fi

    if command -v opencode >/dev/null 2>&1; then
        echo "Zen:          CLIENT AVAILABLE"
    else
        echo "Zen:          OPTIONAL / CLIENT MISSING"
    fi

    echo "Gemini:       BLOCKED UNTIL VERIFIED"

    if [ -f "$CONFIG" ]; then
        echo "Config:       $CONFIG"
        echo "Permissions:  $(stat -c %a "$CONFIG" 2>/dev/null || echo unknown)"
    fi
}

configure() {
    local value=""

    mkdir -p "$DIR"
    umask 077

    echo "Credentials are stored locally with file mode 600."
    echo "Credential values are never echoed."
    echo

    read -r -s -p "NVIDIA API key (Enter to keep current/unset): " value
    echo

    if [ -n "$value" ]; then
        set_key NVIDIA_API_KEY "$value"
        value=""
        echo "NVIDIA credential saved."
    fi

    read -r -s -p "OpenRouter API key (Enter to keep current/unset): " value
    echo

    if [ -n "$value" ]; then
        set_key OPENROUTER_API_KEY "$value"
        value=""
        echo "OpenRouter credential saved."
    fi

    [ ! -f "$CONFIG" ] || chmod 600 "$CONFIG"

    echo
    echo "Gemini was not enabled."
    echo "Next: opencloud fleet refresh"
}

case "$COMMAND" in
    status)
        status
        ;;
    configure)
        configure
        ;;
    path)
        echo "$CONFIG"
        ;;
    *)
        echo "Usage:" >&2
        echo "  opencloud providers status" >&2
        echo "  opencloud providers configure" >&2
        echo "  opencloud providers path" >&2
        exit 2
        ;;
esac
