#!/usr/bin/env python3
"""Fleet root and verification TTL operator-configuration tests."""

import os
import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
MODULE = ROOT / "integrations/fleet/fleet_runtime.py"


def main():
    sys.path.insert(0, str(MODULE.parent))
    import fleet_runtime
    with tempfile.TemporaryDirectory() as tmp:
        os.environ["OPEN_CLOUD_FLEET_HOME"] = tmp
        assert fleet_runtime.fleet_root() == Path(tmp).resolve()
    for raw, expected in (("86400", 86400000), ("1", 1000), ("0", 0)):
        os.environ["OPEN_CLOUD_MODEL_VERIFICATION_TTL_SECONDS"] = raw
        assert fleet_runtime.verification_ttl_ms() == expected
    for raw in ("bad", "-1", "31536001", "999999999999999999999"):
        proc = subprocess.run(
            [sys.executable, "-c", "from fleet_runtime import verification_ttl_ms; verification_ttl_ms()"],
            env=os.environ | {"PYTHONPATH": str(MODULE.parent), "OPEN_CLOUD_MODEL_VERIFICATION_TTL_SECONDS": raw},
            text=True, capture_output=True,
        )
        assert proc.returncode != 0 and "Traceback" not in proc.stderr and "ERROR:" in proc.stderr
    print("PASS Fleet root honors OPEN_CLOUD_FLEET_HOME")
    print("PASS TTL accepts positive values and defines zero as no freshness cache")
    print("PASS malformed, negative, and excessive TTL values fail without traceback")
    print("FLEET_RUNTIME_CONFIG_RELIABILITY: PASS")


if __name__ == "__main__":
    main()
