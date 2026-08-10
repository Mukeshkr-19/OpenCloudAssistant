#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

echo "Open Cloud Assistant uninstall smoke test"

TMP="$(mktemp -d)"
trap "rm -rf \"$TMP\"" EXIT

HOME_DIR="$TMP/home"
BIN="$TMP/bin"

mkdir -p \
    "$HOME_DIR/.config/systemd/user/hermes-gateway.service.d" \
    "$HOME_DIR/.opencloud" \
    "$HOME_DIR/.hermes" \
    "$HOME_DIR/.local/share/vellum" \
    "$HOME_DIR/.local/share/hermes-fleet" \
    "$BIN"

for unit in \
    hermes-fleet-registry.timer \
    hermes-fleet-registry.service \
    hermes-fleet-verifier.timer \
    hermes-fleet-verifier.service
do
    printf "%s\n" "[Unit]" > "$HOME_DIR/.config/systemd/user/$unit"
done

printf "%s\n" "[Service]" \
    > "$HOME_DIR/.config/systemd/user/hermes-gateway.service.d/opencloud.conf"

printf "%s\n" "SECRET_PLACEHOLDER" \
    > "$HOME_DIR/.opencloud/config.env"

printf "%s\n" "hermes-user-data" \
    > "$HOME_DIR/.hermes/config.yaml"

printf "%s\n" "vellum-memory" \
    > "$HOME_DIR/.local/share/vellum/keep.txt"

printf "%s\n" "fleet-state" \
    > "$HOME_DIR/.local/share/hermes-fleet/keep.txt"

printf "%s\n" \
    "#!/usr/bin/env bash" \
    "exit 0" \
    > "$BIN/systemctl"

chmod 755 "$BIN/systemctl"

PLAN="$(
    HOME="$HOME_DIR" \
    PATH="$BIN:/usr/bin:/bin" \
        scripts/uninstall.sh --dry-run
)"

[[ "$PLAN" == *"UNINSTALL_PLAN: PASS"* ]]

test -f "$HOME_DIR/.config/systemd/user/hermes-fleet-registry.timer"

HOME="$HOME_DIR" \
PATH="$BIN:/usr/bin:/bin" \
    scripts/uninstall.sh --yes

test ! -e "$HOME_DIR/.config/systemd/user/hermes-fleet-registry.timer"
test ! -e "$HOME_DIR/.config/systemd/user/hermes-fleet-verifier.timer"
test ! -e "$HOME_DIR/.config/systemd/user/hermes-gateway.service.d/opencloud.conf"

test -f "$HOME_DIR/.opencloud/config.env"
test -f "$HOME_DIR/.hermes/config.yaml"
test -f "$HOME_DIR/.local/share/vellum/keep.txt"
test -f "$HOME_DIR/.local/share/hermes-fleet/keep.txt"

HELP="$(bin/opencloud help)"
[[ "$HELP" == *"opencloud uninstall"* ]]

echo "UNINSTALL_SMOKE: PASS"
