#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

echo "Open Cloud Assistant materialization smoke test"

bash -n install/30-brain-materialize.sh
bash -n install/40-context-materialize.sh
bash -n install/50-workers.sh

install/30-brain-materialize.sh --check
install/40-context-materialize.sh --check
install/50-workers.sh --check

python3 -c '
import json
from pathlib import Path
p = Path("config/hermes/orchestration.json")
data = json.loads(p.read_text())
assert data["orchestrator_enabled"] is True
assert data["max_concurrent_children"] >= 2
assert data["max_spawn_depth"] == 1
assert "vellum-bridge" in data["required_mcp_toolsets"]
'

echo "MATERIALIZATION_SMOKE: PASS"
