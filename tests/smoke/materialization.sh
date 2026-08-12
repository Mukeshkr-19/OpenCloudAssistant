#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

echo "Open Cloud Assistant materialization smoke test"

bash -n install/30-brain-materialize.sh
bash -n install/40-context-materialize.sh
bash -n install/50-workers.sh

HERMES_ROOT="${OPEN_CLOUD_HERMES_ROOT:-$HOME/.hermes/hermes-agent}"

if [ -d "$HERMES_ROOT/.git" ]; then
    OPEN_CLOUD_HERMES_ROOT="$HERMES_ROOT" install/30-brain-materialize.sh --check
else
    echo "HERMES_BRAIN_MATERIALIZATION: SKIP (Hermes Git source unavailable)"
fi

install/40-context-materialize.sh --check
install/50-workers.sh --check

python3 -c '
import json
from pathlib import Path
p = Path("config/hermes/orchestration.json")
data = json.loads(p.read_text())
assert data["orchestrator_enabled"] is True
assert data["max_concurrent_children"] == 4
assert data["max_iterations"] == 12
assert data["child_timeout_seconds"] == 120
assert data["max_spawn_depth"] == 2
assert "vellum-bridge" in data["required_mcp_toolsets"]
'

echo "MATERIALIZATION_SMOKE: PASS"
