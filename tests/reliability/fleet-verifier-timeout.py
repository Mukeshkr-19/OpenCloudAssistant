#!/usr/bin/env python3
"""Verify individual and aggregate Fleet verifier deadlines."""

import importlib.util
import sys
import types
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
hermes_cli = types.ModuleType("hermes_cli")
runtime = types.ModuleType("hermes_cli.runtime_provider")
runtime.resolve_runtime_provider = lambda *args, **kwargs: None
agent = types.ModuleType("agent")
credentials = types.ModuleType("agent.credential_pool")
credentials.load_pool = lambda: None
sys.modules.update({"hermes_cli": hermes_cli, "hermes_cli.runtime_provider": runtime,
                    "agent": agent, "agent.credential_pool": credentials})
spec = importlib.util.spec_from_file_location("fleet_verify", ROOT / "integrations/fleet/registry/verify.py")
verify = importlib.util.module_from_spec(spec)
spec.loader.exec_module(verify)


def main():
    unit = (ROOT / "services/systemd/hermes-fleet-verifier.service").read_text()
    assert "TimeoutStartSec=15min" in unit
    seen = []
    verify.native_auth = lambda provider, model: ("https://example.invalid", "key", {})
    verify.chat_endpoint = lambda provider, base: base
    def slow(request, timeout):
        seen.append(timeout)
        raise TimeoutError("synthetic slow provider")
    verify.urllib.request.urlopen = slow
    ok, reason, stop, endpoint = verify.probe("nvidia", "example")
    assert not ok and seen == [45]
    print("PASS provider probe retains 45-second timeout")
    print("PASS verifier oneshot has 15-minute aggregate deadline")
    print("FLEET_VERIFIER_TIMEOUT_RELIABILITY: PASS")


if __name__ == "__main__":
    main()
