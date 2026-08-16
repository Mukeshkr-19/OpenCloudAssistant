#!/usr/bin/env bash
set -euo pipefail

export PATH="$HOME/.local/bin:$HOME/.bun/bin:$HOME/bin:$PATH"

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TARGET_HOME="${OPEN_CLOUD_HOME:-$HOME}"
SYSTEMD_DIR="${OPEN_CLOUD_SYSTEMD_DIR:-$TARGET_HOME/.config/systemd/user}"
CONFIG="${OPEN_CLOUD_CONFIG:-$TARGET_HOME/.opencloud/config.env}"
CHANNELS="${OPEN_CLOUD_CHANNELS_STATE:-$TARGET_HOME/.opencloud/channels.json}"
FLEET="${OPEN_CLOUD_FLEET_HOME:-$TARGET_HOME/.local/share/hermes-fleet}"
HERMES_PYTHON="${OPEN_CLOUD_HERMES_PYTHON:-$TARGET_HOME/.hermes/hermes-agent/venv/bin/python}"
STATE_DIR="${OPEN_CLOUD_STATE_DIR:-$TARGET_HOME/.opencloud/state}"
MODE="${1:---help}"

if [ ! -x "$HERMES_PYTHON" ]; then
    HERMES_PYTHON="$(command -v python3)"
fi

render_unit() {
    local src="$1"
    local dst="$2"

    python3 -c "from pathlib import Path; import sys; s=Path(sys.argv[1]).read_text(); s=s.replace(\"__OPEN_CLOUD_HOME__\",sys.argv[3]); s=s.replace(\"__OPEN_CLOUD_CONFIG__\",sys.argv[4]); s=s.replace(\"__FLEET_REGISTRY__\",sys.argv[5]); s=s.replace(\"__HERMES_PYTHON__\",sys.argv[6]); s=s.replace(\"__FLEET_HOME__\",sys.argv[7]); Path(sys.argv[2]).write_text(s)" "$src" "$dst" "$TARGET_HOME" "$CONFIG" "$FLEET/registry" "$HERMES_PYTHON" "$FLEET"
}

selected_channels() {
    if [ ! -f "$CHANNELS" ]; then
        return 0
    fi

    python3 -c "import json,sys; d=json.load(open(sys.argv[1])); print(\",\".join(d.get(\"selected\",[]) if not d.get(\"deferred\") else []))" "$CHANNELS"
}

gateway_required() {
    local selected
    selected="$(selected_channels)"

    case ",$selected," in
        *,telegram,*|*,discord,*|*,advanced,*|*,imessage,*)
            return 0
            ;;
        *)
            return 1
            ;;
    esac
}

browser_selected() {
    local selected
    selected="$(selected_channels)"

    case ",$selected," in
        *,browser,*) return 0 ;;
        *) return 1 ;;
    esac
}

render_all() {
    local out="$1"
    mkdir -p "$out"

    for name in \
        hermes-fleet-registry.service \
        hermes-fleet-registry.timer \
        hermes-fleet-verifier.service \
        hermes-fleet-verifier.timer
    do
        render_unit "$ROOT/services/systemd/$name" "$out/$name"
    done
}

validate_rendered() {
    local out="$1"

    for name in \
        hermes-fleet-registry.service \
        hermes-fleet-registry.timer \
        hermes-fleet-verifier.service \
        hermes-fleet-verifier.timer
    do
        test -s "$out/$name"

        if grep -q "__OPEN_CLOUD_\|__FLEET_REGISTRY__\|__HERMES_PYTHON__\|__FLEET_HOME__" "$out/$name"; then
            echo "ERROR: unresolved service placeholder in $name" >&2
            exit 1
        fi
    done

    if command -v systemd-analyze >/dev/null 2>&1; then
        systemd-analyze verify \
            "$out/hermes-fleet-registry.service" \
            "$out/hermes-fleet-registry.timer" \
            "$out/hermes-fleet-verifier.service" \
            "$out/hermes-fleet-verifier.timer" \
            >/dev/null
    fi
}

plan() {
    echo "Open Cloud Assistant service plan"
    echo "Fleet registry timer: REQUIRED"
    echo "Fleet verifier timer: REQUIRED"

    if gateway_required; then
        echo "Hermes gateway: REQUIRED"
    else
        echo "Hermes gateway: SKIP"
    fi

    if browser_selected; then
        echo "Browser runtime: RELEASE VALIDATION STILL REQUIRED"
    else
        echo "Browser runtime: SKIP"
    fi
}

case "$MODE" in
    --check)
        TMP="$(mktemp -d)"
        render_all "$TMP"
        validate_rendered "$TMP"
        rm -rf "$TMP"
        echo "SERVICE_INSTALL_CHECK: PASS"
        ;;

    --plan)
        plan
        ;;

    --install)
        test -f "$FLEET/registry/refresh.py" || {
            echo "ERROR: Fleet registry runtime is not installed." >&2
            exit 1
        }

        test -f "$FLEET/registry/verify.py" || {
            echo "ERROR: Fleet verifier runtime is not installed." >&2
            exit 1
        }

        mkdir -p "$SYSTEMD_DIR" "$STATE_DIR" "$(dirname "$CONFIG")"
        chmod 700 "$SYSTEMD_DIR" "$STATE_DIR" "$(dirname "$CONFIG")"

        if [ ! -f "$CONFIG" ]; then
            touch "$CONFIG"
        fi
        chmod 600 "$CONFIG"

        TMP="$(mktemp -d)"
        render_all "$TMP"
        validate_rendered "$TMP"

        install -m 644 "$TMP/hermes-fleet-registry.service" "$SYSTEMD_DIR/hermes-fleet-registry.service"
        install -m 644 "$TMP/hermes-fleet-registry.timer" "$SYSTEMD_DIR/hermes-fleet-registry.timer"
        install -m 644 "$TMP/hermes-fleet-verifier.service" "$SYSTEMD_DIR/hermes-fleet-verifier.service"
        install -m 644 "$TMP/hermes-fleet-verifier.timer" "$SYSTEMD_DIR/hermes-fleet-verifier.timer"

        rm -rf "$TMP"

        systemctl --user daemon-reload
        systemctl --user enable \
            hermes-fleet-registry.timer \
            hermes-fleet-verifier.timer
        systemctl --user restart \
            hermes-fleet-registry.timer \
            hermes-fleet-verifier.timer

        if gateway_required; then
            command -v hermes >/dev/null 2>&1 || {
                echo "ERROR: Hermes is required for selected messaging channels." >&2
                exit 1
            }

            if [ ! -f "$SYSTEMD_DIR/hermes-gateway.service" ]; then
                hermes gateway install --start-now --start-on-login
            fi

            mkdir -p "$SYSTEMD_DIR/hermes-gateway.service.d"

            printf "%s\n" \
                "[Service]" \
                "EnvironmentFile=-$CONFIG" \
                "Environment=OPEN_CLOUD_FLEET_HOME=$FLEET" \
                "Environment=OPEN_CLOUD_SELF_REPAIR=1" \
                "Environment=OPEN_CLOUD_REPAIR_STATE=$TARGET_HOME/.local/share/opencloud-repair" \
                > "$SYSTEMD_DIR/hermes-gateway.service.d/opencloud.conf"

            chmod 644 "$SYSTEMD_DIR/hermes-gateway.service.d/opencloud.conf"

            systemctl --user daemon-reload
            systemctl --user enable --now hermes-gateway.service
            systemctl --user restart hermes-gateway.service
        fi

        LINGER="$(loginctl show-user "$USER" -p Linger --value 2>/dev/null || true)"

        if [ "$LINGER" != "yes" ]; then
            if command -v sudo >/dev/null 2>&1 && sudo -n true >/dev/null 2>&1; then
                sudo loginctl enable-linger "$USER"
            else
                echo "WARNING: user lingering is not enabled."
                echo "For boot-time user services run: sudo loginctl enable-linger $USER"
            fi
        fi

        printf "%s\n" "version=1" > "$STATE_DIR/services-installed"
        chmod 600 "$STATE_DIR/services-installed"

        echo "SERVICE_INSTALL: PASS"
        plan
        ;;

    -h|--help|help)
        echo "Usage:"
        echo "  $0 --check"
        echo "  $0 --plan"
        echo "  $0 --install"
        ;;

    *)
        echo "ERROR: unknown mode: $MODE" >&2
        exit 2
        ;;
esac
