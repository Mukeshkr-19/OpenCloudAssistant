#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

echo "Open Cloud Assistant Hermes/Vellum integration smoke test"

install/80-vellum-bridge.sh --check

TMP="$(mktemp -d)"

cleanup() {
    rm -rf "$TMP"
}
trap cleanup EXIT

HOME_TARGET="$TMP/home"
CONFIG="$HOME_TARGET/.hermes/config.yaml"
STATE="$HOME_TARGET/.opencloud/state"
PY="${OPEN_CLOUD_HERMES_PYTHON:-$HOME/.hermes/hermes-agent/venv/bin/python}"
[ -x "$PY" ] || PY="$(command -v python3)"
HERMES_SOURCE="$TMP/hermes-source"

mkdir -p "$HERMES_SOURCE/tools" "$HERMES_SOURCE/cron" "$HERMES_SOURCE/gateway"
printf "%s\\n" "orchestrator_enabled max_concurrent_children max_iterations child_timeout_seconds max_spawn_depth inherit_mcp_toolsets" > "$HERMES_SOURCE/tools/delegate_tool.py"
printf "%s\\n" "def _start_multiplex(profile_homes):" "    return profile_homes" > "$HERMES_SOURCE/cron/scheduler_provider.py"
printf "%s\n" \
    "cron_start_kwargs = {}" \
    "profile_homes = []" \
    'cron_start_kwargs["profile_homes"] = profile_homes' \
    > "$HERMES_SOURCE/gateway/run.py"

OPEN_CLOUD_HERMES_SOURCE="$HERMES_SOURCE" \
OPEN_CLOUD_HERMES_PYTHON="$PY" \
install/85-hermes-orchestration.sh --check

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
OPEN_CLOUD_HERMES_SOURCE="$HERMES_SOURCE" \
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
assert x[\"max_concurrent_children\"] == 4
assert x[\"max_iterations\"] == 12
assert x[\"child_timeout_seconds\"] == 120
assert x[\"max_spawn_depth\"] == 2
assert x[\"inherit_mcp_toolsets\"] is True
assert x[\"provider\"] == \"user-provider\"
assert x[\"model\"] == \"user-defined-model\"
assert \"existing-server\" in d[\"mcp_servers\"]
assert d[\"mcp_servers\"][\"vellum-bridge\"][\"enabled\"] is True
assert d[\"display\"][\"platforms\"][\"bluebubbles\"][\"tool_progress\"] == \"off\"
assert d[\"display\"][\"platforms\"][\"bluebubbles\"][\"interim_assistant_messages\"] is False
" "$CONFIG"

test -f "$HOME_TARGET/.config/hermes-vellum/mcp/server.py"
test -x "$HOME_TARGET/.config/hermes-vellum/mcp/worker.py"
test -f "$STATE/hermes-vellum-installed"

grep -qF "def get_user_context" "$HOME_TARGET/.config/hermes-vellum/mcp/server.py"
grep -qF "def start_vellum_task" "$HOME_TARGET/.config/hermes-vellum/mcp/server.py"
grep -B1 -F "def start_vellum_task" "$HOME_TARGET/.config/hermes-vellum/mcp/server.py" | grep -qF "@mcp.tool()"

TASK_ID="vellum_00000000000000000000000000000001"
TASK_STATE="$HOME_TARGET/.config/hermes-vellum/mcp/state/$TASK_ID.json"
FAKE_TASK="$TMP/fake-vellum-task"
printf '%s\n' '#!/usr/bin/env bash' 'test "$1" = "synthetic task"' 'echo synthetic-result' > "$FAKE_TASK"
chmod 755 "$FAKE_TASK"
python3 - "$TASK_STATE" "$TASK_ID" <<'PY'
import json, sys
json.dump({"task_id": sys.argv[2], "status": "queued", "prompt": "synthetic task", "timeout_seconds": 10}, open(sys.argv[1], "w"))
PY
HOME="$HOME_TARGET" OPEN_CLOUD_VELLUM_TASK_COMMAND="$FAKE_TASK" \
    "$HOME_TARGET/.config/hermes-vellum/mcp/worker.py" "$TASK_ID"
python3 - "$TASK_STATE" <<'PY'
import json, sys
d=json.load(open(sys.argv[1])); assert d["status"] == "completed"; assert d["result"] == "synthetic-result"
PY
echo "PASS fresh Vellum bridge install launches synthetic mutation worker"

echo "HERMES_VELLUM_SMOKE: PASS"

test -f "$ROOT/integrations/hermes/silent_gateway_lifecycle.py"
python3 -m py_compile "$ROOT/integrations/hermes/silent_gateway_lifecycle.py"
