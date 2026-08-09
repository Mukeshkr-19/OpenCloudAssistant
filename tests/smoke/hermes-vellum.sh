#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

echo "Open Cloud Assistant Hermes/Vellum integration smoke test"

install/80-vellum-bridge.sh --check
install/85-hermes-orchestration.sh --check

TMP="$(mktemp -d)"

cleanup() {
    rm -rf "$TMP"
}
trap cleanup EXIT

HOME_TARGET="$TMP/home"
CONFIG="$HOME_TARGET/.hermes/config.yaml"
STATE="$HOME_TARGET/.opencloud/state"
PY="$(command -v python3)"

mkdir -p "$(dirname "$CONFIG")"

printf "%s\n" \
"_config_version: 1" \
"delegation:" \
"  max_iterations: 7" \
"  provider: user-provider" \
"  model: user-defined-model" \
"mcp_servers:" \
"  existing-server:" \
"    enabled: true" \
"    command: existing-command" \
"    args: []" \
> "$CONFIG"

OPEN_CLOUD_HOME="$HOME_TARGET" install/80-vellum-bridge.sh --install

OPEN_CLOUD_HOME="$HOME_TARGET" \
OPEN_CLOUD_HERMES_CONFIG="$CONFIG" \
OPEN_CLOUD_HERMES_SOURCE="$HOME/.hermes/hermes-agent" \
OPEN_CLOUD_HERMES_PYTHON="$PY" \
OPEN_CLOUD_STATE_DIR="$STATE" \
install/85-hermes-orchestration.sh --install

OPEN_CLOUD_HOME="$HOME_TARGET" \
OPEN_CLOUD_HERMES_CONFIG="$CONFIG" \
OPEN_CLOUD_HERMES_PYTHON="$PY" \
OPEN_CLOUD_STATE_DIR="$STATE" \
scripts/doctor-brain.sh

"$PY" -c "
import yaml,sys
d=yaml.safe_load(open(sys.argv[1]))
x=d[\"delegation\"]
assert x[\"orchestrator_enabled\"] is True
assert x[\"max_concurrent_children\"] == 3
assert x[\"max_iterations\"] == 50
assert x[\"max_spawn_depth\"] == 1
assert x[\"inherit_mcp_toolsets\"] is True
assert x[\"provider\"] == \"user-provider\"
assert x[\"model\"] == \"user-defined-model\"
assert \"existing-server\" in d[\"mcp_servers\"]
assert d[\"mcp_servers\"][\"vellum-bridge\"][\"enabled\"] is True
" "$CONFIG"

test -f "$HOME_TARGET/.config/hermes-vellum/mcp/server.py"
test -f "$STATE/hermes-vellum-installed"

grep -qF "def get_user_context" "$HOME_TARGET/.config/hermes-vellum/mcp/server.py"
grep -qF "def start_vellum_task" "$HOME_TARGET/.config/hermes-vellum/mcp/server.py"
grep -B1 -F "def start_vellum_task" "$HOME_TARGET/.config/hermes-vellum/mcp/server.py" | grep -qF "@mcp.tool()"

echo "HERMES_VELLUM_SMOKE: PASS"

test -f "$ROOT/integrations/hermes/silent_gateway_lifecycle.py"
python3 -m py_compile "$ROOT/integrations/hermes/silent_gateway_lifecycle.py"
