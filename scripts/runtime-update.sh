#!/usr/bin/env bash
set -euo pipefail

export PATH="$HOME/.local/bin:$HOME/.opencode/bin:$HOME/.bun/bin:$HOME/bin:$PATH"

ROOT="${OPEN_CLOUD_ROOT:-$HOME/OpenCloudAssistant}"
MODE="${1:---run}"

require_runtime() {
    command -v opencode >/dev/null
    command -v bun >/dev/null
    command -v vellum >/dev/null
    command -v hermes >/dev/null
    test -x "$ROOT/install/30-brain-materialize.sh"
    test -x "$ROOT/install/35-hermes-live.sh"
    test -x "$ROOT/install/80-vellum-bridge.sh"
    test -x "$ROOT/install/85-hermes-orchestration.sh"
}

if [ "$MODE" = "--check" ]; then
    test -x "$ROOT/install/30-brain-materialize.sh"
    test -x "$ROOT/install/35-hermes-live.sh"
    test -x "$ROOT/install/80-vellum-bridge.sh"
    test -x "$ROOT/install/85-hermes-orchestration.sh"
    echo "RUNTIME_UPDATE_CHECK: PASS"
    exit 0
fi

[ "$MODE" = "--run" ] || {
    echo "Usage: $0 [--check|--run]" >&2
    exit 2
}

require_runtime

echo "RUNTIME_UPDATE: OpenCode"
opencode upgrade --method curl
if [ -x "$HOME/.opencode/bin/opencode" ]; then
    ln -sfn ../../.opencode/bin/opencode "$HOME/.local/bin/opencode"
fi
opencode --version

echo "RUNTIME_UPDATE: Vellum"
bun add -g vellum@latest
if systemctl --user is-active --quiet vellum-core.service; then
    systemctl --user restart vellum-core.service
fi
vellum --version

echo "RUNTIME_UPDATE: supported Hermes baseline"
hermes update --check
"$ROOT/install/30-brain-materialize.sh" --check
hermes_result="$("$ROOT/install/35-hermes-live.sh" --install)"
printf '%s\n' "$hermes_result"
"$ROOT/install/80-vellum-bridge.sh" --install
OPEN_CLOUD_HERMES_SOURCE="$HOME/.hermes/hermes-agent" \
    "$ROOT/install/85-hermes-orchestration.sh" --install

if [[ "$hermes_result" == *"HERMES_LIVE_INSTALL: PASS"* ]] && \
   systemctl --user is-active --quiet hermes-gateway.service; then
    systemctl --user restart hermes-gateway.service
fi

echo "RUNTIME_UPDATE: PASS"
