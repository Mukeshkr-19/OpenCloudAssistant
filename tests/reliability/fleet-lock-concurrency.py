#!/usr/bin/env python3
"""Prove refresh and verifier's shared registry lock prevents lost updates."""

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
MODULE = ROOT / "integrations/fleet"
CHILD = """
import json, os, sys, time
from pathlib import Path
from fleet_runtime import registry_lock
root=Path(sys.argv[1]); state=root/'state.json'
with registry_lock(root):
    data=json.loads(state.read_text())
    value=data['value']
    time.sleep(0.2)
    temp=root/f'state.{os.getpid()}.tmp'
    temp.write_text(json.dumps({'value': value + 1}))
    os.replace(temp, state)
"""


def main():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        state = root / "state.json"
        state.write_text('{"value": 0}')
        env = os.environ | {"PYTHONPATH": str(MODULE)}
        processes = [subprocess.Popen([sys.executable, "-c", CHILD, str(root)], env=env) for _ in range(2)]
        assert all(process.wait(timeout=5) == 0 for process in processes)
        assert json.loads(state.read_text()) == {"value": 2}
    for path in (ROOT / "integrations/fleet/registry/refresh.py", ROOT / "integrations/fleet/registry/verify.py"):
        assert "with registry_lock(" in path.read_text()
    print("PASS concurrent registry writers serialize without lost updates")
    print("PASS refresh and verifier use the shared tested lock")
    print("FLEET_LOCK_CONCURRENCY_RELIABILITY: PASS")


if __name__ == "__main__":
    main()
