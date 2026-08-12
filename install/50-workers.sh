#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
POLICY="$ROOT/config/hermes/orchestration.json"
MODE="${1:---check}"

check_policy() {
    python3 -c '
import json, sys
from pathlib import Path
p = Path(sys.argv[1])
data = json.loads(p.read_text())
required = {
    "orchestrator_enabled": True,
    "max_concurrent_children": 4,
    "max_iterations": 12,
    "child_timeout_seconds": 120,
    "max_spawn_depth": 2,
    "inherit_mcp_toolsets": True,
}
for key, value in required.items():
    if data.get(key) != value:
        raise SystemExit(f"invalid {key}: {data.get(key)!r}")
mcp = data.get("required_mcp_toolsets", [])
if "vellum-bridge" not in mcp:
    raise SystemExit("vellum-bridge missing from required_mcp_toolsets")
print("WORKER_POLICY: PASS")
' "$POLICY"
}

case "$MODE" in
    --check)
        check_policy
        ;;
    --show)
        check_policy
        cat "$POLICY"
        ;;
    --help|-h|help)
        echo "Usage: $0 [--check|--show]"
        ;;
    *)
        echo "ERROR: unknown mode: $MODE" >&2
        exit 2
        ;;
esac
