#!/usr/bin/env python3
"""Deterministic Fleet discovery retention and verification-TTL checks."""

import importlib.util
import json
import os
import sys
import tempfile
import types
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main():
    hermes_cli = types.ModuleType("hermes_cli")
    model_switch = types.ModuleType("hermes_cli.model_switch")
    model_switch.list_provider_models = lambda _: []
    runtime = types.ModuleType("hermes_cli.runtime_provider")
    runtime.resolve_runtime_provider = lambda *args, **kwargs: {
        "provider": kwargs.get("requested", ""),
        "base_url": "https://example.invalid/v1",
        "api_key": "test",
    }
    agent = types.ModuleType("agent")
    credentials = types.ModuleType("agent.credential_pool")
    credentials.load_pool = lambda: None
    metadata = types.ModuleType("agent.model_metadata")
    metadata.MINIMUM_CONTEXT_LENGTH = 64_000
    metadata.get_model_context_length = lambda model, **kwargs: (
        16_000 if model == "too-small" else 128_000
    )
    sys.modules.update({"hermes_cli": hermes_cli, "hermes_cli.model_switch": model_switch,
                        "hermes_cli.runtime_provider": runtime, "agent": agent,
                        "agent.credential_pool": credentials,
                        "agent.model_metadata": metadata})
    refresh = load("fleet_refresh", ROOT / "integrations/fleet/registry/refresh.py")
    verify = load("fleet_verify", ROOT / "integrations/fleet/registry/verify.py")

    # Zen discovery must use OpenCode's live, structured catalog instead of
    # Hermes' broader models.dev history. Cost and protocol stay data-driven.
    refresh.shutil.which = lambda name: "/fixture/opencode" if name == "opencode" else None
    refresh.subprocess.run = lambda *args, **kwargs: types.SimpleNamespace(
        returncode=0,
        stdout='''opencode/fixture-chat
{
  "id": "fixture-chat",
  "name": "Fixture Chat",
  "api": {"npm": "@ai-sdk/openai-compatible"},
  "cost": {"input": 0, "output": 0}
}
opencode/fixture-responses
{
  "id": "fixture-responses",
  "name": "Fixture Responses",
  "api": {"npm": "@ai-sdk/openai"},
  "cost": {"input": 0, "output": 0}
}
''',
    )
    identity, live_rows = refresh.discover(["opencode-zen", "opencode"])
    assert identity == "opencode-cli"
    assert [row["id"] for row in live_rows] == [
        "fixture-chat",
        "fixture-responses",
    ]
    assert refresh.explicitly_free(
        "fixture-chat",
        live_rows[0]["metadata"],
    )

    captured_request = {}
    original_native_auth = verify.native_auth
    original_context_check = verify.hermes_context_compatible
    original_urlopen = verify.urllib.request.urlopen

    class ProbeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return json.dumps({
                "choices": [{
                    "message": {
                        "tool_calls": [{
                            "function": {
                                "name": "hermes_fleet_probe",
                                "arguments": '{"ok": true}',
                            }
                        }]
                    }
                }]
            }).encode()

    verify.native_auth = lambda provider, model: (
        "https://example.invalid/v1",
        "fixture-key",
        {"provider": provider},
    )
    verify.hermes_context_compatible = lambda *args: (
        True,
        128_000,
    )

    def fake_urlopen(request, timeout):
        captured_request["request"] = request
        return ProbeResponse()

    verify.urllib.request.urlopen = fake_urlopen
    probe_ok, _, _, _ = verify.probe(
        "opencode-zen",
        "fixture-chat",
    )
    assert probe_ok
    assert captured_request["request"].get_header("User-agent") == (
        "OpenCloudAssistant-Fleet/1"
    )
    assert verify.classify(
        429,
        "rate limited",
        "opencode-zen",
    ) == ("candidate_rate_limit", False)
    assert verify.classify(
        429,
        "rate limited",
        "nvidia",
    ) == ("provider_rate_limit", True)
    verify.native_auth = original_native_auth
    verify.hermes_context_compatible = original_context_check
    verify.urllib.request.urlopen = original_urlopen

    with tempfile.TemporaryDirectory() as tmp:
        fleet_home = Path(tmp) / "fleet"
        (fleet_home / "registry").mkdir(parents=True)
        (fleet_home / "fleet.json").write_text(
            json.dumps(
                {
                    "pools": {
                        "zen": {
                            "type": "registry",
                            "provider": "opencode-zen",
                            "providerGroup": "zen",
                            "discoveryAliases": [
                                "opencode-zen",
                                "opencode",
                            ],
                            "freeOnly": True,
                        },
                        "nvidia": {
                            "type": "registry",
                            "provider": "nvidia",
                            "providerGroup": "nvidia",
                            "discoveryAliases": ["nvidia"],
                            "freeOnly": False,
                        },
                    }
                }
            )
        )
        os.environ["OPEN_CLOUD_FLEET_HOME"] = str(fleet_home)
        output = Path(tmp) / "models.json"
        old = {
            "models": [
                {"provider": "opencode-zen", "providerGroup": "zen", "id": "keep",
                 "verification": "verified", "verifiedAtMs": 1, "lastProbeMs": 1,
                 "productionEligible": True, "excludedReason": None},
                {"provider": "nvidia", "providerGroup": "nvidia", "id": "remove",
                 "verification": "verified", "verifiedAtMs": 1, "lastProbeMs": 1,
                 "productionEligible": True, "excludedReason": None},
            ]
        }
        output.write_text(json.dumps(old))
        refresh.ROOT = Path(tmp)
        refresh.OUTPUT = output
        refresh.CONFIG = Path(tmp) / "missing.yaml"
        refresh.configured_seeds = lambda specs: {
            group: set()
            for group in sorted(
                {
                    spec["providerGroup"]
                    for spec in specs.values()
                }
            )
        }

        for error in (TimeoutError(), ValueError("malformed"), OSError("http")):
            output.write_text(json.dumps(old))
            refresh.discover = lambda aliases, exc=error: (_ for _ in ()).throw(exc)
            refresh.main()
            data = json.loads(output.read_text())
            assert {row["id"] for row in data["models"]} == {"keep", "remove"}
            assert all(row["discoveryStale"] for row in data["models"])

        # Empty discovery is failure-equivalent; a successful authoritative
        # response for NVIDIA removes its old model.
        def discovery(aliases):
            if "opencode-zen" in aliases:
                raise RuntimeError("empty")
            return "nvidia", [{"id": "replacement", "metadata": {}}]
        output.write_text(json.dumps(old))
        refresh.discover = discovery
        refresh.main()
        data = json.loads(output.read_text())
        assert {row["id"] for row in data["models"]} == {"keep", "replacement"}

        # The live Zen catalog may mix API protocols. Only the protocol Hermes
        # currently executes is eligible; the decision comes from metadata,
        # never from a concrete model ID.
        output.write_text(json.dumps(old))
        refresh.discover = lambda aliases: (
            "opencode-cli",
            live_rows,
        ) if "opencode-zen" in aliases else (
            "nvidia",
            [{"id": "replacement", "metadata": {}}],
        )
        refresh.main()
        protocol_rows = {
            row["id"]: row
            for row in json.loads(output.read_text())["models"]
            if row["provider"] == "opencode-zen"
        }
        assert protocol_rows["fixture-chat"]["excludedReason"] is None
        assert protocol_rows["fixture-chat"]["explicitFree"] is True
        assert protocol_rows["fixture-responses"]["excludedReason"] == (
            "unsupported_runtime_protocol"
        )

        assert verify.verification_is_fresh({"verification": "verified", "verifiedAtMs": 100}, 101)
        assert not verify.verification_is_fresh(
            {"verification": "verified", "verifiedAtMs": 100}, 100 + verify.VERIFICATION_TTL_MS
        )
        stale = {
            "models": [{"provider": "opencode-zen", "providerGroup": "zen", "id": "stale",
                        "verification": "verified", "verifiedAtMs": 1,
                        "lastProbeMs": 1, "excludedReason": None, "configuredSeed": True}],
            "productionModels": {"zen": ["stale"], "nvidia": []},
        }
        output.write_text(json.dumps(stale))
        verify.REGISTRY = output
        verify.TARGET = {"zen": 1, "nvidia": 0}
        verify.probe = lambda provider, model: (True, "verified", False, "synthetic")
        verify.time.time = lambda: 1_000_000
        verify.main()
        reprobed = json.loads(output.read_text())["models"][0]
        assert reprobed["verification"] == "verified"
        assert reprobed["verifiedAtMs"] == 1_000_000_000
        reprobed["verifiedAtMs"] = 1
        output.write_text(json.dumps({"models": [reprobed], "productionModels": {"zen": ["stale"], "nvidia": []}}))
        verify.probe = lambda provider, model: (False, "no_tool_call", False, "synthetic")
        verify.time.time = lambda: 2_000_000
        verify.main()
        failed = json.loads(output.read_text())
        assert failed["models"][0]["verification"] == "incompatible"
        assert failed["productionModels"]["zen"] == []

        stale["models"][0]["verification"] = "verified"
        stale["models"][0]["verifiedAtMs"] = 1
        output.write_text(json.dumps(stale))
        verify.MAX_ATTEMPTS = {"zen": 0, "nvidia": 0}
        verify.time.time = lambda: 3_000_000
        verify.main()
        assert json.loads(output.read_text())["productionModels"]["zen"] == []

        # Tool-call success cannot override Hermes' hard runtime context floor.
        too_small = {
            "models": [{"provider": "nvidia", "providerGroup": "nvidia",
                        "id": "too-small", "verification": "verified",
                        "verifiedAtMs": 3_000_000_000, "lastProbeMs": 3_000_000_000,
                        "productionEligible": True, "excludedReason": None}],
            "productionModels": {"zen": [], "nvidia": ["too-small"]},
        }
        output.write_text(json.dumps(too_small))
        verify.TARGET = {"zen": 0, "nvidia": 0}
        verify.time.time = lambda: 3_000_001
        verify.main()
        rejected = json.loads(output.read_text())
        assert rejected["models"][0]["verification"] == "incompatible"
        assert rejected["models"][0]["lastProbeReason"] == "context_below_hermes_minimum"
        assert rejected["productionModels"]["nvidia"] == []

    print("PASS failed discovery retains degraded last-known-good rows")
    print("PASS successful discovery authoritatively removes absent models")
    print("PASS OpenCode live catalog drives Zen cost and protocol eligibility")
    print("PASS provider probes send an explicit application User-Agent")
    print("PASS Zen model rate limits do not suppress healthy sibling models")
    print("PASS model verification freshness expires at configured TTL")
    print("PASS stale verified model is re-probed and refreshes verifiedAtMs")
    print("PASS failed stale re-probe demotes model from production")
    print("PASS stale verified model cannot survive an exhausted probe budget")
    print("PASS Hermes-incompatible context cannot remain production eligible")
    print("FLEET_REGISTRY_STATE_RELIABILITY: PASS")


if __name__ == "__main__":
    main()
