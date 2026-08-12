#!/usr/bin/env python3
"""Deterministic source-transform coverage for Hermes daemon pools."""

import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
PATCHER = ROOT / "integrations/hermes/daemon_pool_compat.py"
OLD = '''def build(self, thread_name, weakref_cb):
            t = threading.Thread(
                name=thread_name,
                target=_worker,
                args=(
                    weakref.ref(self, weakref_cb),
                    self._work_queue,
                    self._initializer,
                    self._initargs,
                ),
                daemon=True,
            )
'''


def main():
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "daemon_pool.py"
        path.write_text(OLD)
        subprocess.run([sys.executable, str(PATCHER), str(path)], check=True)
        transformed = path.read_text()
        assert "OPEN_CLOUD_DAEMON_POOL_PY314_V1" in transformed
        assert "_create_worker_context" in transformed
        assert 'getattr(self, "_initializer", None)' in transformed
        compile(transformed, str(path), "exec")
        before = transformed
        subprocess.run([sys.executable, str(PATCHER), str(path)], check=True)
        assert path.read_text() == before
    print("PASS synthetic legacy daemon source gains dual worker signatures")
    print("PASS daemon compatibility transform is idempotent and compiles")
    print("DAEMON_POOL_COMPAT_RELIABILITY: PASS")


if __name__ == "__main__":
    main()
