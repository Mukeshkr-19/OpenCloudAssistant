#!/usr/bin/env bash
set -euo pipefail

export PATH="$HOME/.local/bin:$HOME/.bun/bin:$HOME/bin:$PATH"

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MODE="${1:---dry-run}"
FLEET="${OPEN_CLOUD_FLEET_HOME:-$HOME/.local/share/hermes-fleet}"

SYSTEMD_DIR="$HOME/.config/systemd/user"

UNITS=(
    hermes-fleet-registry.timer
    hermes-fleet-registry.service
    hermes-fleet-verifier.timer
    hermes-fleet-verifier.service
)

show_retained() {
    echo
    echo "Retained by default:"
    echo "  $HOME/.opencloud"
    echo "    credentials, channel selections, backups and user configuration"
    echo "  $HOME/.hermes"
    echo "    Hermes source, configuration, sessions and history"
    echo "  $HOME/.local/share/vellum"
    echo "    Vellum memory and runtime state"
    echo "  $FLEET"
    echo "    Fleet registry and health history"
    echo "  $HOME/.config/hermes-vellum"
    echo "    Vellum bridge state and integration files"
    echo "  $HOME/.opencloud/task-profiles"
    echo "    private local task profiles"
    echo "  hermes-gateway.service"
    echo "    upstream Hermes service is not deleted or disabled"
}

remove_path() {
    local path="$1"

    [ -e "$path" ] || return 0

    if [ "$MODE" = "--dry-run" ]; then
        echo "WOULD_REMOVE: $path"
    else
        rm -rf -- "$path"
        echo "REMOVED: $path"
    fi
}

remove_if_managed() {
    local source="$1"
    local target="$2"

    [ -f "$target" ] || return 0

    if [ -f "$source" ] && cmp -s "$source" "$target"; then
        remove_path "$target"
    else
        echo "PRESERVE_MODIFIED: $target"
    fi
}

case "$MODE" in

    --dry-run|--yes)
        ;;

    -h|--help|help)
        echo "Usage:"
        echo "  opencloud uninstall"
        echo "  opencloud uninstall --dry-run"
        echo "  opencloud uninstall --yes"
        echo
        echo "Default behavior is a non-mutating uninstall plan."
        echo "--yes removes only clearly OpenCloud-owned operational files."
        exit 0
        ;;

    *)
        echo "ERROR: unsupported uninstall option: $MODE" >&2
        exit 2
        ;;
esac

echo "Open Cloud Assistant uninstall"
echo

if [ "$MODE" = "--dry-run" ]; then
    echo "Mode: PLAN ONLY"
else
    echo "Mode: APPLY SAFE UNINSTALL"
fi

echo
echo "OpenCloud-managed service automation:"

for unit in "${UNITS[@]}"; do

    if [ "$MODE" = "--dry-run" ]; then

        echo "WOULD_DISABLE: $unit"

    elif command -v systemctl >/dev/null 2>&1; then

        systemctl --user disable --now "$unit" >/dev/null 2>&1 || true
        echo "DISABLED: $unit"
    fi

    remove_path "$SYSTEMD_DIR/$unit"
done

remove_path \
    "$SYSTEMD_DIR/hermes-gateway.service.d/opencloud.conf"

if [ "$MODE" = "--yes" ]; then

    rmdir \
        "$SYSTEMD_DIR/hermes-gateway.service.d" \
        2>/dev/null || true

    if command -v systemctl >/dev/null 2>&1; then
        systemctl --user daemon-reload >/dev/null 2>&1 || true
        systemctl --user reset-failed >/dev/null 2>&1 || true
    fi
fi

remove_if_managed \
    "$ROOT/integrations/self-repair/hermes-code-repair" \
    "$HOME/.local/bin/hermes-code-repair"

show_retained

echo
if [ "$MODE" = "--dry-run" ]; then
    echo "UNINSTALL_PLAN: PASS"
    echo "Run opencloud uninstall --yes to apply this safe plan."
else
    echo "UNINSTALL_STATUS: PASS"
fi
