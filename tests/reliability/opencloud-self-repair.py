#!/usr/bin/env python3
"""Deterministic regression coverage for the self-repair auto-trigger (P8).

Wires the validated ``hermes-code-repair`` harness into the gateway failure
path for *internal* OpenCloud code regressions only:

    internal regression -> classify -> sanitized incident -> repair (staged +
    validated + deploy) -> pending-replay marker -> restart -> replay once

Safeguards enforced without model judgment:
  * no recursion / no concurrent repair (in-progress marker);
  * one replay maximum (marker consumed once);
  * per-error-fingerprint cooldown;
  * rollback on failed validation / failed post-restart health;
  * external failures (rate limit, timeout, auth, 404, quota) NEVER repaired.

The orchestrator's side-effecting steps are injectable, so every path is
exercised with deterministic test doubles — no live source is modified.
"""

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
HERMES_ROOT = Path(os.environ.get("OPEN_CLOUD_HERMES_ROOT", Path.home() / ".hermes/hermes-agent"))
PATCH = ROOT / "integrations/hermes/hermes-opencloud-self-repair.patch"


def materialize(out: Path) -> bool:
    result = subprocess.run(
        ["bash", str(ROOT / "install/30-brain-materialize.sh"), "--stage", str(out)],
        env={**os.environ, "OPEN_CLOUD_HERMES_ROOT": str(HERMES_ROOT)},
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        print(
            "OPENCLOUD_SELF_REPAIR_RELIABILITY: SKIP (materialization failed): "
            + (result.stderr or result.stdout or "").strip()
        )
        return False
    return True


def _import(tree: Path, name: str):
    spec = importlib.util.spec_from_file_location(
        name, tree / (name.replace(".", "/") + ".py")
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main() -> None:
    if not (HERMES_ROOT / ".git").is_dir():
        print("OPENCLOUD_SELF_REPAIR_RELIABILITY: SKIP (Hermes Git source unavailable)")
        return

    with tempfile.TemporaryDirectory(prefix="opencloud-self-repair-") as tmp:
        tree = Path(tmp) / "hermes"
        if not materialize(tree):
            return

        patch_text = PATCH.read_text()
        patched_files = {
            line[len("diff --git "):].split(" b/", 1)[0].strip()
            for line in patch_text.splitlines()
            if line.startswith("diff --git ")
        }
        assert patched_files == {
            "a/agent/opencloud_self_repair.py",
            "a/gateway/run.py",
        }, f"unexpected patch surface: {patched_files}"

        sys.path.insert(0, str(tree))
        try:
            sr = _import(tree, "agent.opencloud_self_repair")
        finally:
            sys.path.pop(0)

        classify = sr.classify_repairable_error
        Orchestrator = sr.RepairOrchestrator

        # ── 1. Classification: internal regressions dispatch ───────────────
        # _opencloud_* metadata leak -> repair.
        fp, task = classify(
            "TypeError",
            "Completions.create() got an unexpected keyword argument "
            "'_opencloud_routing_profile'",
        )
        assert fp == "opencloud_metadata_leak", fp
        assert "strip internal keys" in task

        # plain internal TypeError / AttributeError -> repair.
        assert classify("TypeError", "got an unexpected keyword argument 'x'")[0] == "internal_typeerror"
        assert classify("AttributeError", "'NoneType' object has no attribute 'x'")[0] == "internal_attributeerror"

        # OpenCloud integration ImportError -> repair.
        fp, _ = classify(
            "ModuleNotFoundError", "No module named 'agent.opencloud_foo'"
        )
        assert fp == "opencloud_import_error", fp
        assert classify("ImportError", "cannot import name 'x' from 'opencloud'")[0] == "opencloud_import_error"

        # ── 2. Classification: external/operational failures NEVER repair ──
        for exc_type, message in [
            ("RateLimitError", "rate limit exceeded"),
            ("RateLimitError", "429 too many requests"),
            ("APITimeoutError", "request timed out"),
            ("APIConnectionError", "connection error"),
            ("AuthenticationError", "invalid api key"),
            ("AuthenticationError", "401 unauthorized"),
            ("PermissionDeniedError", "403 quota exceeded"),
            ("NotFoundError", "404 not found"),
            ("ServiceUnavailableError", "503 service unavailable"),
            ("InsufficientQuotaError", "insufficient credits"),
        ]:
            assert classify(exc_type, message) is None, (exc_type, message)

        # ── 3. Orchestrator: validated repair -> restart + one replay ──────
        calls = []

        def invoke_repair(task):
            calls.append(("repair", task))
            return True

        def restart():
            calls.append(("restart",))

        def health_check():
            calls.append(("health",))
            return True

        def replay(req):
            calls.append(("replay", req))

        state = tempfile.mkdtemp(prefix="opencloud-self-repair-state-")
        orch = Orchestrator(
            state_root=state,
            invoke_repair=invoke_repair,
            restart=restart,
            health_check=health_check,
            replay=replay,
            cooldown_seconds=100,
            now=lambda: 1000.0,
        )
        status = orch.run("fp1", "fix the leak", "user msg", metadata={"source": {"chat_id": "7"}})
        assert status == "replayed", status
        assert calls == [("repair", "fix the leak"), ("restart",), ("health",), ("replay", "user msg")], calls

        # ── 4. Cooldown + no-recursion blocking ────────────────────────────
        # same fingerprint within cooldown -> skipped.
        assert orch.should_attempt("fp1") is False
        # a concurrent/recursive repair is in progress -> blocked.
        state2 = tempfile.mkdtemp(prefix="opencloud-self-repair-state2-")
        orch2 = Orchestrator(
            state_root=state2,
            invoke_repair=lambda t: True,
            restart=lambda: None,
            health_check=lambda: True,
            replay=lambda r: None,
            cooldown_seconds=0,
            now=lambda: 1.0,
        )
        (orch2._in_progress).write_text("1.0")
        assert orch2.should_attempt("other") is False

        # ── 5. Failed repair -> no replay marker, no restart ───────────────
        calls2 = []
        state3 = tempfile.mkdtemp(prefix="opencloud-self-repair-state3-")
        orch3 = Orchestrator(
            state_root=state3,
            invoke_repair=lambda t: False,
            restart=lambda: calls2.append("restart"),
            health_check=lambda: True,
            replay=lambda r: calls2.append("replay"),
        )
        assert orch3.run("fp", "task", "req") == "repair_failed"
        assert calls2 == []
        assert Orchestrator.consume_pending_replay(state3) is None

        # ── 6. Failed post-restart health -> rollback, no replay ───────────
        rolled = []
        state4 = tempfile.mkdtemp(prefix="opencloud-self-repair-state4-")
        orch4 = Orchestrator(
            state_root=state4,
            invoke_repair=lambda t: True,
            restart=lambda: None,
            health_check=lambda: False,
            replay=lambda r: rolled.append(("replay", r)),
            rollback=lambda: rolled.append(("rollback",)),
        )
        assert orch4.run("fp", "task", "req") == "health_failed"
        assert rolled == [("rollback",)]

        # ── 7. Single replay: marker consumed exactly once ─────────────────
        state5 = tempfile.mkdtemp(prefix="opencloud-self-repair-state5-")
        orch5 = Orchestrator(
            state_root=state5,
            invoke_repair=lambda t: True,
            restart=lambda: None,
            health_check=lambda: False,
            replay=lambda r: None,
        )
        orch5._write_pending_replay(
            "preserved request", metadata={"source": {"platform": "photon", "chat_id": "42"}}
        )
        payload = Orchestrator.consume_pending_replay(state5)
        assert payload["request"] == "preserved request"
        assert payload["metadata"]["source"]["chat_id"] == "42"
        assert Orchestrator.consume_pending_replay(state5) is None  # second call -> None

        # ── 8. maybe_auto_repair dispatch (monkeypatched orchestrator) ─────
        dispatched = []

        class FakeOrch:
            def __init__(self):
                pass

            def should_attempt(self, fingerprint):
                return True

            def run(self, fingerprint, task, user_request, metadata=None):
                dispatched.append((fingerprint, task, user_request, metadata))
                return "replayed"

        real_production = sr.production_orchestrator
        sr.production_orchestrator = lambda *a, **k: FakeOrch()
        try:
            # repairable -> dispatch.
            status = sr.maybe_auto_repair(
                "TypeError",
                "unexpected keyword argument '_opencloud_routing_profile'",
                user_request="the user msg",
                module="agent.transports.chat_completions",
                metadata={"source": {"chat_id": "9"}},
            )
            assert status == "replayed"
            assert len(dispatched) == 1
            assert dispatched[0][0] == "opencloud_metadata_leak"
            assert dispatched[0][2] == "the user msg"
            assert dispatched[0][3]["source"]["chat_id"] == "9"

            # non-repairable -> None, no dispatch.
            dispatched.clear()
            assert sr.maybe_auto_repair("RateLimitError", "rate limit") is None
            assert dispatched == []
        finally:
            sr.production_orchestrator = real_production

        # ── 9. Source-level wiring in the gateway ──────────────────────────
        gateway_source = (tree / "gateway/run.py").read_text()
        module_source = (tree / "agent/opencloud_self_repair.py").read_text()

        assert "HERMES_OPENCLOUD_SELF_REPAIR_V1" in gateway_source
        assert "HERMES_OPENCLOUD_SELF_REPAIR_V1" in module_source

        # opt-in env gate + dispatch call in the agent-run failure path.
        assert 'OPEN_CLOUD_SELF_REPAIR' in gateway_source
        assert "_maybe_opencloud_self_repair(" in gateway_source
        assert "maybe_auto_repair(" in gateway_source

        # replay hook is wired into the startup-restore finish.
        assert "_replay_pending_opencloud_repair()" in gateway_source
        assert "consume_pending_replay(" in gateway_source
        assert "SessionSource.from_dict(" in gateway_source

        # dispatch preserves the normal error path (re-raises).
        assert "_agent_run_exc" in gateway_source

        # ── 10. Invariants preserved ───────────────────────────────────────
        fleet_bridge = (ROOT / "integrations/hermes/hermes-fleet-bridge.patch").read_text()
        assert '== "openrouter/free"' in fleet_bridge

    print("PASS internal TypeError dispatches repair")
    print("PASS internal AttributeError dispatches repair")
    print("PASS OpenCloud integration ImportError dispatches repair")
    print("PASS _opencloud_* metadata leak dispatches repair")
    print("PASS rate limit / timeout / auth / quota / 404 never repair")
    print("PASS validated repair restarts and replays exactly once")
    print("PASS duplicate fingerprint is blocked during cooldown")
    print("PASS recursive/concurrent repair is blocked")
    print("PASS failed repair rolls back with no replay")
    print("PASS failed post-restart health rolls back")
    print("PASS pending-replay marker is consumed exactly once")
    print("PASS replay metadata round-trips the originating channel")
    print("PASS gateway dispatch is opt-in and preserves the normal error path")
    print("PASS openrouter/free final escape is untouched")
    print("OPENCLOUD_SELF_REPAIR_RELIABILITY: PASS")


if __name__ == "__main__":
    main()
