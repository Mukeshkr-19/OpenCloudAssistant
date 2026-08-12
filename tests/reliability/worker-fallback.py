#!/usr/bin/env python3
"""Deterministic worker route spreading, fallback, and pin invalidation checks."""

import ast
import importlib.util
import json
import os
import shutil
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
HERMES_ROOT = Path(os.environ.get("OPEN_CLOUD_HERMES_ROOT", Path.home() / ".hermes/hermes-agent"))


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main():
    with tempfile.TemporaryDirectory(prefix="opencloud-worker-fallback-") as tmp:
        home = Path(tmp) / "home"
        fleet_root = Path(tmp) / "fleet"
        (fleet_root / "registry").mkdir(parents=True)
        home.mkdir()
        (fleet_root / "fleet.json").write_text(
            (ROOT / "config/fleet/hermes-fleet-policy.json").read_text()
        )
        shutil.copy2(ROOT / "integrations/fleet/dispatcher.py", fleet_root / "dispatcher.py")
        shutil.copy2(ROOT / "integrations/fleet/fleet_runtime.py", fleet_root / "fleet_runtime.py")
        (fleet_root / "registry/models.json").write_text(json.dumps({
            "productionModels": {"zen": ["zen-verified"], "nvidia": ["nvidia-verified"]},
            "models": [
                {"providerGroup": "zen", "id": "stale-unverified", "verification": "unverified"},
            ],
        }))
        (fleet_root / "session-pin.key").write_bytes(b"x" * 32)

        os.environ["HOME"] = str(home)
        os.environ["OPEN_CLOUD_FLEET_HOME"] = str(fleet_root)
        os.environ["HERMES_FLEET_HEALTH_DB"] = str(fleet_root / "health.sqlite")

        bridge = load("opencloud_worker_bridge", HERMES_ROOT / "agent/hermes_fleet_bridge.py")
        bridge._runtime = lambda candidate: {
            "provider": candidate["provider"], "requested_provider": candidate["provider"]
        }

        fleet = bridge._fleet()
        fleet.close()

        with ThreadPoolExecutor(max_workers=3) as executor:
            routes = list(executor.map(lambda _: bridge.resolve_role("worker"), range(3)))
        assert sorted(route["candidate"]["providerGroup"] for route in routes) == [
            "nvidia", "openrouter", "zen",
        ]
        assert all(len(route["fallback_chain"]) == 1 for route in routes)
        assert all(
            route["fallback_chain"][0]["_hermes_fleet_provider_group"]
            != route["candidate"]["providerGroup"]
            for route in routes
        )
        assert all(route["candidate"]["model"] != "stale-unverified" for route in routes)
        openrouter = next(
            route["candidate"] for route in routes
            if route["candidate"]["providerGroup"] == "openrouter"
        )
        assert (openrouter["provider"], openrouter["model"]) == ("openrouter", "openrouter/free")

        delegate = (HERMES_ROOT / "tools/delegate_tool.py").read_text()
        recovery = (HERMES_ROOT / "agent/agent_runtime_helpers.py").read_text()
        request_loop = (HERMES_ROOT / "run_agent.py").read_text()
        assert "child._api_max_retries = 1" in delegate
        assert 'getattr(agent, "_hermes_fleet_role", None) == "worker"' in recovery
        assert 'request_kwargs["max_retries"] = 0' in request_loop

        auxiliary_tree = ast.parse((HERMES_ROOT / "agent/auxiliary_client.py").read_text())
        free_model_node = next(
            node for node in auxiliary_tree.body
            if isinstance(node, ast.FunctionDef) and node.name == "_is_free_model"
        )
        namespace = {}
        exec(compile(ast.Module(body=[free_model_node], type_ignores=[]), "auxiliary_client.py", "exec"), namespace)
        assert namespace["_is_free_model"]("openrouter/free") is True
        assert namespace["_is_free_model"]("nvidia/example:free") is True
        assert namespace["_is_free_model"]("openai/gpt-paid") is False

        worker = type("Agent", (), {
            "_hermes_fleet_role": "worker", "provider": "opencode-zen", "model": "zen-verified",
        })()
        bridge.note_agent_failure(worker, TimeoutError("synthetic provider timeout"))
        assert bridge.should_skip_fallback(worker, "opencode-zen", "zen-verified") is True
        assert bridge.should_skip_fallback(worker, "nvidia", "nvidia-verified") is False
        recovered = bridge.resolve_role("worker")
        assert recovered["candidate"]["providerGroup"] != "zen"

        session = "synthetic-stale-pin-session"
        initial = bridge.resolve_role("main", session_key=session)
        registry_path = fleet_root / "registry/models.json"
        registry = json.loads(registry_path.read_text())
        group = initial["candidate"]["providerGroup"]
        registry["productionModels"][group] = [
            model for model in registry["productionModels"].get(group, [])
            if model != initial["candidate"]["model"]
        ]
        registry_path.write_text(json.dumps(registry))
        replacement = bridge.resolve_role("main", session_key=session)
        assert replacement["candidate"]["candidateKey"] != initial["candidate"]["candidateKey"]

        registry_path.write_text(json.dumps({
            "productionModels": {"zen": ["zen-verified"], "nvidia": ["nvidia-verified"]},
            "models": [],
        }))
        session = "synthetic-main-session"
        initial = bridge.resolve_role("main", session_key=session)
        main = type("Agent", (), {
            "_hermes_fleet_role": "main",
            "_hermes_fleet_session_key": session,
            "provider": initial["candidate"]["provider"],
            "model": initial["candidate"]["model"],
        })()
        bridge.note_agent_failure(main, RuntimeError("delegated child stopped"))
        still_pinned = bridge.resolve_role("main", session_key=session)
        assert still_pinned["candidate"]["candidateKey"] == initial["candidate"]["candidateKey"]
        bridge.note_agent_failure(main, TimeoutError("synthetic pinned route timeout"))
        replacement = bridge.resolve_role("main", session_key=session)
        assert replacement["candidate"]["candidateKey"] != initial["candidate"]["candidateKey"]

    print("PASS parallel workers spread across verified route pools")
    print("PASS worker fallback is bounded to one verified alternate route")
    print("PASS provider timeout cools the primary and permits verified fallback")
    print("PASS stale or unverified model is not selectable")
    print("PASS unhealthy main session pin is invalidated and replaced")
    print("PASS unknown non-provider failures preserve healthy main session pins")
    print("PASS stale persisted pins cannot select routes removed from production")
    print("PASS OpenRouter worker route remains exactly openrouter/free")
    print("PASS Fleet workers use one attempt per route with SDK retries disabled")
    print("PASS Hermes auxiliary free-only accepts openrouter/free")
    print("WORKER_FALLBACK_RELIABILITY: PASS")


if __name__ == "__main__":
    main()
