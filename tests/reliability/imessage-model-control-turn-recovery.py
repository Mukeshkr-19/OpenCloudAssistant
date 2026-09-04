#!/usr/bin/env python3
"""Deterministic coverage for iMessage model-control + turn-recovery patch.

Two layers:

1. Pure layer — patch-extracted modules: intent parsing, alias resolution,
   status-source precedence, provider-neutral eligibility boundary, read-only
   Fleet pin resolution against a real SQLite fixture.

2. Live layer — the materialized Hermes tree. The REAL
   ``GatewayRunner._maybe_handle_model_control_fast_path`` is invoked on a
   minimal real instance (``GatewayRunner.__new__``) with only external
   dependencies stubbed (Fleet client object, runtime resolution). No
   handwritten copy of the handler exists in this file; fault paths are
   injected through the real Fleet bridge functions.
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import os
import re
import sqlite3
import subprocess
import sys
import tempfile
import textwrap
import time
import types
from contextlib import contextmanager
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PATCH = ROOT / "integrations/hermes/hermes-imessage-model-control-turn-recovery.patch"
FLEET_BRIDGE_PATCH = ROOT / "integrations/hermes/hermes-fleet-bridge.patch"
HERMES_ROOT = Path(
    os.environ.get("OPEN_CLOUD_HERMES_ROOT", Path.home() / ".hermes/hermes-agent")
)
HERMES_BASELINE = "3fa318a50c02df8dbd2c55499f5f73d51ad77188"

_FIXTURE_BOOTSTRAP_MODEL = "fixture/bootstrap-model-a"
_FIXTURE_BOOTSTRAP_PROVIDER = "fixture-provider-a"
_FIXTURE_FLEET_MODEL = "fixture/fleet-candidate-b"
_FIXTURE_FLEET_PROVIDER = "fixture-provider-b"
_FIXTURE_OVERRIDE_MODEL = "fixture/override-model-c"
_FIXTURE_OVERRIDE_PROVIDER = "fixture-provider-c"
_FIXTURE_RUNTIME_MODEL = "fixture/runtime-model-d"
_FIXTURE_RUNTIME_PROVIDER = "fixture-provider-d"
_FIXTURE_NEW_MODEL = "fixture/discovered-model-e"
_FIXTURE_NEW_PROVIDER = "fixture-provider-e"
_FIXTURE_ALT_PROVIDER = "fixture-provider-f"
_FIXTURE_STABLE_MODEL = "fixture/stable-route-g"
_FIXTURE_STABLE_PROVIDER = "fixture-provider-g"
_FIXTURE_STABLE_GROUP = "fixture-group-g"
_FIXTURE_BLOCKED_MODEL = "fixture/blocked-model-h"
_FIXTURE_BLOCKED_PROVIDER = "fixture-provider-h"
_FIXTURE_BLOCKED_GROUP = "fixture-group-h"
_FIXTURE_COLLISION_SHORT = "fixture-collision-short"
_FIXTURE_COLLISION_LONG = "fixture-collision-long"
_FIXTURE_COLLISION_MODEL = "fixture/collision-model"

_SK = "sk-fixture"


def require(cond: bool, msg: str) -> None:
    if not cond:
        raise AssertionError(msg)


def load_module_from(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


def extract_new_file_from_patch(patch_text: str, relpath: str) -> str:
    lines: list[str] = []
    in_file = False
    for line in patch_text.splitlines():
        if line.startswith(f"diff --git a/{relpath} "):
            in_file = True
            continue
        if in_file and line.startswith("diff --git "):
            break
        if in_file and line.startswith("+") and not line.startswith("+++"):
            lines.append(line[1:])
    require(bool(lines), f"could not extract {relpath} from patch")
    return "\n".join(lines) + "\n"


def extract_fleet_bridge_insert(patch_text: str) -> str:
    chunk = patch_text.split("diff --git a/agent/hermes_fleet_bridge.py", 1)[1]
    chunk = chunk.split("diff --git a/gateway/model_control_fast_path.py", 1)[0]
    lines: list[str] = []
    for line in chunk.splitlines():
        if line.startswith("+") and not line.startswith("+++"):
            lines.append(line[1:])
        elif line.startswith(" def _configured_default"):
            break
    return "\n".join(lines) + "\n"


def extract_eligibility_fns(patch_text: str) -> str:
    """Shared eligibility boundary (_provider/_group/_model/_allowed)."""
    src = patch_text
    start = src.index("+def _group(")
    end = src.index("+def _all(")
    chunk = src[start:end]
    lines = []
    for line in chunk.splitlines():
        if line.startswith("+") and not line.startswith("+++"):
            lines.append(line[1:])
        elif line.startswith(" def "):
            lines.append(line[1:])
    return "\n".join(lines) + "\n"


def assert_canonical_runner_uses_hermes_python() -> None:
    """Regression: run.sh must not invoke this test with bare system python3."""
    run_sh = (ROOT / "tests/reliability/run.sh").read_text()
    require(
        "python3 tests/reliability/imessage-model-control-turn-recovery.py" not in run_sh,
        "run.sh must not invoke PR-A test with bare python3",
    )
    require(
        '"$HERMES_PYTHON" tests/reliability/imessage-model-control-turn-recovery.py' in run_sh,
        "run.sh must invoke PR-A test via HERMES_PYTHON",
    )


@contextmanager
def _dotenv_stub_if_needed():
    """Install a scoped test-only dotenv stub when the interpreter lacks it."""
    had_dotenv = importlib.util.find_spec("dotenv") is not None
    saved = sys.modules.get("dotenv")
    if not had_dotenv:
        stub = types.ModuleType("dotenv")
        stub.load_dotenv = lambda *a, **k: False
        stub.dotenv_values = lambda *a, **k: {}
        sys.modules["dotenv"] = stub
    try:
        yield had_dotenv
    finally:
        if not had_dotenv:
            if saved is None:
                sys.modules.pop("dotenv", None)
            else:
                sys.modules["dotenv"] = saved


def patch_applies() -> None:
    stat = subprocess.run(
        ["git", "apply", "--stat", str(PATCH)],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    require(stat.returncode == 0, f"patch corrupt: {(stat.stderr or stat.stdout).strip()}")
    require(
        "gateway/model_control_fast_path.py" in stat.stdout,
        "patch missing model_control_fast_path.py",
    )
    extracted = extract_new_file_from_patch(
        PATCH.read_text(), "gateway/model_control_fast_path.py"
    )
    declared = re.search(
        r"\+\+\+ b/gateway/model_control_fast_path\.py\n@@ -0,0 \+1,(\d+) @@",
        PATCH.read_text(),
    )
    require(
        declared is not None
        and int(declared.group(1)) == len(extracted.splitlines()),
        "model-control new-file hunk count must match its complete body",
    )
    require(
        extracted.rstrip().endswith("return False"),
        "model-control new-file hunk must not truncate the truthfulness helper",
    )


def materialize(out: Path) -> None:
    result = subprocess.run(
        ["bash", str(ROOT / "install/30-brain-materialize.sh"), "--stage", str(out)],
        env={**os.environ, "OPEN_CLOUD_HERMES_ROOT": str(HERMES_ROOT)},
        capture_output=True,
        text=True,
    )
    require(
        result.returncode == 0,
        "materialization failed: " + (result.stderr or result.stdout or "").strip(),
    )


def hermes_git_available() -> bool:
    if not HERMES_ROOT.is_dir() or not (HERMES_ROOT / ".git").is_dir():
        return False
    pin = subprocess.run(
        ["git", "-C", str(HERMES_ROOT), "cat-file", "-e", f"{HERMES_BASELINE}^{{commit}}"],
        capture_output=True,
    )
    return pin.returncode == 0


def fixture_candidates():
    return [
        {
            "provider": _FIXTURE_FLEET_PROVIDER,
            "model": _FIXTURE_FLEET_MODEL,
            "providerGroup": "fixture-group-b",
        },
        {
            "provider": _FIXTURE_ALT_PROVIDER,
            "model": _FIXTURE_FLEET_MODEL,
            "providerGroup": "fixture-group-f",
        },
        {
            "provider": _FIXTURE_NEW_PROVIDER,
            "model": _FIXTURE_NEW_MODEL,
            "providerGroup": "fixture-group-e",
        },
    ]


# ---------------------------------------------------------------------------
# Pure layer — patch-extracted real code
# ---------------------------------------------------------------------------


def test_status_precedence(m) -> None:
    snap = m.resolve_model_status_snapshot(
        configured_provider=_FIXTURE_BOOTSTRAP_PROVIDER,
        configured_model=_FIXTURE_BOOTSTRAP_MODEL,
        pin_info={
            "provider": _FIXTURE_FLEET_PROVIDER,
            "model": _FIXTURE_FLEET_MODEL,
            "fleet_pin": True,
            "resolved": True,
        },
    )
    require(snap.source == "FLEET_PIN", "Fleet pin beats stale bootstrap config")
    fleet_status = m.format_model_status(
        provider=snap.provider,
        model=snap.model,
        source=snap.source,
        fleet_pin=True,
        routing_mode="pinned",
    )
    require("Fleet pinned route:" in fleet_status, "Fleet pin not labeled active")
    require("Active route:" not in fleet_status, "Fleet pin must not say active")

    snap_cfg = m.resolve_model_status_snapshot(
        configured_provider=_FIXTURE_BOOTSTRAP_PROVIDER,
        configured_model=_FIXTURE_BOOTSTRAP_MODEL,
    )
    require(snap_cfg.source == "CONFIGURED_FALLBACK", "config-only is fallback")
    cfg_status = m.format_model_status(
        provider=snap_cfg.provider,
        model=snap_cfg.model,
        source=snap_cfg.source,
        fleet_pin=False,
    )
    require("Configured default:" in cfg_status, "config-only not active route")
    require("Active route:" not in cfg_status, "bootstrap must not say active")

    snap_fleet_auto = m.resolve_model_status_snapshot(
        configured_provider=_FIXTURE_BOOTSTRAP_PROVIDER,
        configured_model=_FIXTURE_BOOTSTRAP_MODEL,
        fleet_enabled=True,
    )
    require(snap_fleet_auto.source == "FLEET_AUTO", "Fleet auto beats bootstrap")
    auto_waiting_status = m.format_model_status(
        provider=snap_fleet_auto.provider,
        model=snap_fleet_auto.model,
        source=snap_fleet_auto.source,
        fleet_pin=False,
        routing_mode=snap_fleet_auto.routing_mode,
    )
    require(
        "next model selected dynamically" in auto_waiting_status,
        "automatic status is explicit before runtime selection",
    )
    require(
        _FIXTURE_BOOTSTRAP_MODEL not in auto_waiting_status,
        "automatic status must hide stale bootstrap model",
    )

    snap_manual = m.resolve_model_status_snapshot(
        session_override={
            "provider": _FIXTURE_OVERRIDE_PROVIDER,
            "model": _FIXTURE_OVERRIDE_MODEL,
        },
        pin_info={"error": True},
    )
    require(snap_manual.source == "MANUAL_SESSION", "manual override wins over Fleet error")

    snap_runtime = m.resolve_model_status_snapshot(
        running_provider=_FIXTURE_RUNTIME_PROVIDER,
        running_model=_FIXTURE_RUNTIME_MODEL,
        pin_info={"error": True},
    )
    require(snap_runtime.source == "RUNNING_AGENT", "running runtime wins over Fleet error")

    snap_auto = m.resolve_model_status_snapshot(
        running_provider="auto",
        running_model=_FIXTURE_RUNTIME_MODEL,
    )
    require(snap_auto.provider == "", "provider auto is blank in snapshot")
    auto_status = m.format_model_status(
        provider=snap_auto.provider,
        model=snap_auto.model,
        source=snap_auto.source,
        fleet_pin=False,
    )
    require("auto/" not in auto_status, "provider auto not shown in route")

    snap_switch_only = m.resolve_model_status_snapshot(
        last_requested_switch=f"{_FIXTURE_FLEET_PROVIDER}/{_FIXTURE_FLEET_MODEL}",
        configured_model=_FIXTURE_BOOTSTRAP_MODEL,
    )
    require(
        snap_switch_only.source == "CONFIGURED_FALLBACK",
        "last switch metadata must not decide route",
    )

    snap_unresolved = m.resolve_model_status_snapshot(
        pin_info={"fleet_pin": True, "resolved": False},
        configured_model=_FIXTURE_BOOTSTRAP_MODEL,
    )
    require(snap_unresolved.source == "FLEET_PIN_UNRESOLVED", "unresolved pin not bootstrap")

    status_text = m.format_model_status(
        provider=_FIXTURE_BOOTSTRAP_PROVIDER,
        model=_FIXTURE_BOOTSTRAP_MODEL,
        source="CONFIGURED_FALLBACK",
        fleet_pin=False,
    )
    require(
        "runtime routing state" not in status_text.lower(),
        "status must not claim universal runtime sourcing",
    )


def test_nl_switch_forms(m) -> None:
    cases = [
        ("switch to fixture-alpha", "fixture-alpha", ""),
        ("use fixture-alpha", "fixture-alpha", ""),
        (
            f"switch to {_FIXTURE_FLEET_MODEL} on {_FIXTURE_FLEET_PROVIDER}",
            _FIXTURE_FLEET_MODEL,
            _FIXTURE_FLEET_PROVIDER,
        ),
        (
            f"switch to {_FIXTURE_FLEET_MODEL} and provider {_FIXTURE_FLEET_PROVIDER}",
            _FIXTURE_FLEET_MODEL,
            _FIXTURE_FLEET_PROVIDER,
        ),
        (
            f"use provider {_FIXTURE_FLEET_PROVIDER} model {_FIXTURE_FLEET_MODEL}",
            _FIXTURE_FLEET_MODEL,
            _FIXTURE_FLEET_PROVIDER,
        ),
        (
            f"change the model to {_FIXTURE_FLEET_MODEL} using {_FIXTURE_FLEET_PROVIDER}",
            _FIXTURE_FLEET_MODEL,
            _FIXTURE_FLEET_PROVIDER,
        ),
        (
            f"can u switch to {_FIXTURE_FLEET_MODEL} in {_FIXTURE_FLEET_PROVIDER}?",
            _FIXTURE_FLEET_MODEL,
            _FIXTURE_FLEET_PROVIDER,
        ),
        (
            f"I will try {_FIXTURE_FLEET_MODEL} can u switch to this in {_FIXTURE_FLEET_PROVIDER}!!",
            _FIXTURE_FLEET_MODEL,
            _FIXTURE_FLEET_PROVIDER,
        ),
    ]
    for text, model_q, prov in cases:
        intent = m.detect_model_control_intent(text)
        require(intent is not None and intent.kind == "switch", f"switch intent: {text}")
        require(model_q in intent.model_query, f"model query for {text}")
        if prov:
            require(intent.provider_hint == prov.lower(), f"provider hint for {text}")


def test_catalog_intent_and_bounds(m) -> None:
    intent = m.detect_model_control_intent("list model catalog")
    require(intent is not None and intent.kind == "catalog", "catalog intent")
    provider_intent = m.detect_model_control_intent("show nvidia models")
    require(provider_intent is not None and provider_intent.kind == "catalog", "provider catalog intent")
    require(provider_intent.provider_hint == "nvidia", "provider catalog hint")
    trailing = m.detect_model_control_intent("list the complete list of free models in OpenCode Zen")
    require(trailing is not None and trailing.kind == "catalog", "trailing provider catalog intent")
    require(trailing.provider_hint == "opencode-zen", "multiword provider catalog hint")
    fast = m.detect_model_control_intent("Which fast model do u have !!")
    require(fast is not None and fast.kind == "catalog", "natural fast catalog intent")
    rows = [
        {"provider": "nvidia", "group": "nvidia", "model": f"fixture/{i}", "status": "ready"}
        for i in range(100)
    ]
    rendered = m.format_fleet_catalog(rows, max_chars=500)
    require(len(rendered) < 800, "catalog response is bounded")
    require("Showing" in rendered and "provider-only catalog" in rendered, "catalog truncation is explicit")
    filtered = m.format_fleet_catalog(
        rows + [{"provider": "gemini", "group": "gemini", "model": "fixture/g", "status": "ready"}],
        provider_hint=provider_intent.provider_hint,
    )
    require("nvidia/fixture/0" in filtered and "gemini/" not in filtered, "provider catalog filter")
    alias_filtered = m.format_fleet_catalog(
        [{
            "provider": "opencode-zen",
            "group": "zen",
            "aliases": ["opencode"],
            "model": "fixture/free",
            "status": "verified",
        }],
        provider_hint="opencode",
    )
    require("opencode-zen/fixture/free" in alias_filtered, "policy alias catalog filter")
    require("subject to live health checks" in alias_filtered, "catalog truth label")


def test_alias_resolution(m) -> None:
    cands = fixture_candidates()
    unique, amb = m.resolve_model_alias(
        "fleet-candidate-b",
        provider_hint=_FIXTURE_FLEET_PROVIDER,
        candidates=cands,
    )
    require(unique is not None and amb == [], "unique resolve by suffix")
    require(unique.model == _FIXTURE_FLEET_MODEL, "resolved fleet model")

    none, amb2 = m.resolve_model_alias("fleet-candidate-b", candidates=cands)
    require(none is None and len(amb2) >= 2, "ambiguous across providers")

    new_only, amb3 = m.resolve_model_alias(
        "discovered-model-e",
        provider_hint=_FIXTURE_NEW_PROVIDER,
        candidates=cands,
    )
    require(new_only is not None and amb3 == [], "arbitrary discovered model resolves")

    prov_from_data, _ = m.resolve_model_alias(
        _FIXTURE_NEW_MODEL,
        provider_hint=_FIXTURE_NEW_PROVIDER,
        candidates=cands,
    )
    require(
        prov_from_data is not None
        and prov_from_data.provider == _FIXTURE_NEW_PROVIDER,
        "provider derived from candidate data",
    )

    alias_model, alias_amb = m.resolve_model_alias(
        "fixture/free",
        provider_hint="opencode",
        candidates=[{
            "provider": "opencode-zen",
            "providerGroup": "zen",
            "providerAliases": ["opencode"],
            "model": "fixture/free",
        }],
    )
    require(alias_model is not None and alias_amb == [], "policy provider alias resolves")


def test_provider_prefix_collision(m) -> None:
    cands = [
        {
            "provider": _FIXTURE_COLLISION_LONG,
            "model": _FIXTURE_COLLISION_MODEL,
            "providerGroup": _FIXTURE_COLLISION_LONG,
        },
        {
            "provider": _FIXTURE_COLLISION_SHORT,
            "model": "fixture/other-model",
            "providerGroup": _FIXTURE_COLLISION_SHORT,
        },
    ]
    exact, amb = m.resolve_model_alias(
        _FIXTURE_COLLISION_MODEL.split("/")[-1],
        provider_hint=_FIXTURE_COLLISION_SHORT,
        candidates=cands,
    )
    require(exact is None and amb == [], "short hint must not match long provider")
    exact_long, _ = m.resolve_model_alias(
        _FIXTURE_COLLISION_MODEL.split("/")[-1],
        provider_hint=_FIXTURE_COLLISION_LONG,
        candidates=cands,
    )
    require(
        exact_long is not None and exact_long.provider == _FIXTURE_COLLISION_LONG,
        "exact long provider resolves",
    )


def test_ambiguity_followup_parsing(m) -> None:
    cands = fixture_candidates()
    _, amb = m.resolve_model_alias("fleet-candidate-b", candidates=cands)
    require(len(amb) >= 2, "fixture ambiguity")
    msg = m.format_switch_ambiguous(amb)
    require(
        "switch to <model-id> on <provider>" in msg,
        "ambiguity must instruct structured follow-up",
    )
    follow = f"switch to {_FIXTURE_FLEET_MODEL} on {_FIXTURE_FLEET_PROVIDER}"
    intent = m.detect_model_control_intent(follow)
    require(intent is not None and intent.kind == "switch", "follow-up is switch")
    require(intent.model_query == _FIXTURE_FLEET_MODEL, "follow-up model")
    require(intent.provider_hint == _FIXTURE_FLEET_PROVIDER.lower(), "follow-up provider")
    resolved, amb2 = m.resolve_model_alias(
        intent.model_query,
        provider_hint=intent.provider_hint,
        candidates=cands,
    )
    require(resolved is not None and amb2 == [], "follow-up resolves uniquely")


def test_shared_eligibility_boundary(m) -> None:
    """The name boundary is provider-neutral; Fleet owns verified eligibility."""
    require(
        m._allowed({"provider": "gemini", "providerGroup": "gemini", "model": "m"}) is True,
        "verified Gemini candidates are not name-blocked",
    )
    require(
        m._allowed({"provider": "openrouter", "providerGroup": "openrouter", "model": "fixture/free"}) is True,
        "dynamic verified OpenRouter routes are not name-blocked",
    )
    require(
        m._allowed(
            {"provider": _FIXTURE_FLEET_PROVIDER, "providerGroup": "fixture-group-b", "model": "fixture/m"}
        )
        is True,
        "normal fixture candidate allowed",
    )
    require(
        m._allowed(
            {"provider": "openrouter", "providerGroup": "openrouter", "model": "openrouter/free"}
        )
        is True,
        "openrouter/free remains the stable escape",
    )
    require(
        m._allowed(
            {"provider": "openrouter", "providerGroup": "openrouter", "model": "partner/x"}
        )
        is True,
        "dynamic OpenRouter candidates are provider-neutral at the name boundary",
    )
    require(
        m._allowed({"provider": "", "providerGroup": "fixture-group-b", "model": "fixture/m"})
        is False,
        "empty provider rejected",
    )


def test_readonly_status_no_mutation(patch_text: str) -> None:
    status_chunk = patch_text.split('if intent.kind == "status":', 1)[1].split(
        'if intent.kind != "switch":', 1
    )[0]
    for forbidden in (
        "_set_pin(",
            "clear_session_pin(",
            "_session_model_overrides[session_key] =",
            "_opencloud_last_requested_switch[session_key] =",
            "_evict_cached_agent(",
        ):
        require(forbidden not in status_chunk, f"status path must not mutate via {forbidden}")


def test_switch_failure_no_exception_leak(patch_text: str) -> None:
    switch_tail = patch_text.split('if intent.kind != "switch":', 1)[1]
    require("Model switch failed ({exc})" not in switch_tail, "no raw exc in user message")
    require(
        '"Model switch failed. Route unchanged.' in switch_tail,
        "stable generic switch failure message",
    )
    require(
        "Model switch failed; route state could not be confirmed." in switch_tail,
        "honest unconfirmed-state message",
    )


def test_readonly_pin_helper(fleet_bridge_src: str) -> None:
    fn = fleet_bridge_src.split("def resolve_session_pin_readonly(", 1)[1].split(
        "def _configured_default():", 1
    )[0]
    for forbidden in ("_fleet(", "_get_pin(", "_set_pin(", "commit(", "_ensure_pin_table("):
        require(forbidden not in fn, f"readonly pin helper must not call {forbidden}")
    require("mode=ro" in fn or "?mode=ro" in fn, "readonly pin opens sqlite read-only")
    require("_readonly_fleet_candidate" in fn, "dynamic fleet candidate resolver")
    require("routing_profile" in fn, "readonly pin preserves routing profile")


def extract_fleet_bridge_functions(src: str) -> str:
    start = src.index("def _readonly_fleet_candidate(")
    end = src.index("def _configured_default():")
    return src[start:end]


def test_stable_and_dynamic_readonly_routes(fleet_src: str) -> None:
    require("_readonly_fleet_candidate" in fleet_src, "fleet candidate resolver")
    require("stable-route" in fleet_src, "stable-route support")
    require('pool_type != "registry"' in fleet_src, "dynamic registry support")


def test_readonly_sqlite_no_writes(fleet_src: str) -> None:
    import hashlib
    import hmac

    with tempfile.TemporaryDirectory(prefix="oca-pin-ro-") as tmp:
        base = Path(tmp)
        fleet_home = base / "fleet"
        fleet_home.mkdir()
        db_path = fleet_home / "health.sqlite"
        pin_key = fleet_home / "session-pin.key"
        key_bytes = b"x" * 32
        pin_key.write_bytes(key_bytes)
        fleet_home.joinpath("fleet.json").write_text(
            json.dumps(
                {
                    "enabled": True,
                    "roles": {"main": ["stable-pool"]},
                    "pools": {
                        "stable-pool": {
                            "type": "stable-route",
                            "providerGroup": _FIXTURE_STABLE_GROUP,
                            "provider": _FIXTURE_STABLE_PROVIDER,
                            "route": _FIXTURE_STABLE_MODEL,
                        }
                    },
                }
            )
        )
        session_key = "session-fixture"
        digest = hmac.new(key_bytes, session_key.encode(), hashlib.sha256).hexdigest()
        conn = sqlite3.connect(db_path)
        conn.execute(
            """
            CREATE TABLE fleet_session_pins (
                session_digest TEXT NOT NULL,
                role TEXT NOT NULL,
                candidate_key TEXT NOT NULL,
                routing_profile TEXT NOT NULL DEFAULT '',
                updated_at REAL NOT NULL DEFAULT 0,
                PRIMARY KEY (session_digest, role)
            )
            """
        )
        pin = f"{_FIXTURE_STABLE_GROUP}:{_FIXTURE_STABLE_PROVIDER}:{_FIXTURE_STABLE_MODEL}"
        conn.execute(
            "INSERT INTO fleet_session_pins VALUES (?, ?, ?, '', 0)",
            (digest, "main", pin),
        )
        conn.commit()
        row_count_before = conn.execute(
            "SELECT COUNT(*) FROM fleet_session_pins"
        ).fetchone()[0]
        conn.close()

        mod_path = base / "hermes_fleet_bridge.py"
        mod_path.write_text(
            "import json, os, hashlib, hmac\nfrom pathlib import Path\n"
            + "ROOT = Path(os.environ['OCA_FLEET_ROOT'])\n"
            + "CONFIG = ROOT / 'fleet.json'\n"
            + "PIN_KEY = ROOT / 'session-pin.key'\n"
            + "def enabled(): return True\n"
            + "def _provider(c): return c.get('provider','')\n"
            + "def _model(c): return c.get('model','')\n"
            + "def _session_digest(sk):\n"
            + "  return hmac.new(PIN_KEY.read_bytes(), sk.encode(), hashlib.sha256).hexdigest()\n"
            + fleet_src
        )
        os.environ["OCA_FLEET_ROOT"] = str(fleet_home)
        os.environ["HERMES_FLEET_HEALTH_DB"] = str(db_path)
        m = load_module_from(mod_path, "oca_fleet_bridge_ro")
        for _ in range(3):
            info = m.resolve_session_pin_readonly(session_key, "main")
            require(info and info.get("resolved"), "stable-route pin resolves without registry")
            require(info.get("model") == _FIXTURE_STABLE_MODEL, "stable model resolved")

        conn = sqlite3.connect(db_path)
        row_count_after = conn.execute(
            "SELECT COUNT(*) FROM fleet_session_pins"
        ).fetchone()[0]
        conn.close()
        require(
            row_count_after == row_count_before,
            "repeated readonly pin lookups must not write sqlite",
        )

        require(info.get("routing_profile") == "", "readonly routing profile returned")


_FORBIDDEN_CONCRETE_MODEL_TERMS = (
    "glm-",
    "llama",
    "muse-",
    "kimi",
    "claude",
    "gpt-4",
    "gemini-",
    "nemotron",
)


def scan_no_concrete_models(patch_text: str) -> None:
    low = patch_text.lower()
    for term in _FORBIDDEN_CONCRETE_MODEL_TERMS:
        require(term not in low, f"patch contains concrete model id: {term}")


# ---------------------------------------------------------------------------
# Live layer — the real materialized GatewayRunner handler
# ---------------------------------------------------------------------------


def _cand(provider: str, group: str, model: str) -> dict:
    return {
        "provider": provider,
        "providerGroup": group,
        "model": model,
        "candidateKey": f"{group}:{provider}:{model}",
    }


class _FixtureFleet:
    """Minimal Fleet client stub: real sqlite3 handle + candidate rows.

    Only the external Fleet client object is stubbed; the bridge functions
    (_set_pin / _clear_pin / _available / _allowed / resolve_session_pin_*
    / _readonly_fleet_candidate) are the real materialized code.
    """

    def __init__(self, db, candidates, close_raises: bool = False) -> None:
        self.db = db
        self._candidates = list(candidates)
        self.close_raises = close_raises

    def candidates(self, role):
        return list(self._candidates)

    def _provider_cooling(self, group):
        return 0

    def _candidate_cooling(self, key):
        return 0

    def _registry_model_rows(self):
        return {}

    def _candidate_eligibility(self, candidate, registry_rows=None):
        return True, "fixture_verified", {}

    def routing_profile(self, role, requested=None):
        return None

    def close(self):
        if self.close_raises:
            raise RuntimeError("fixture fleet close failure")


class _LiveGatewayEnv:
    """Real GatewayRunner instance (__new__) wired to the real materialized
    agent.hermes_fleet_bridge, pointed at an isolated fixture Fleet home.

    Only external dependencies are stubbed: the Fleet client object, runtime
    resolution, and fault injection wrappers around the real pin functions.
    """

    def __init__(self, out: Path, candidates, *, close_fault: str = "none") -> None:
        self.fb = None
        self.gateway_run = None
        self.out = out
        self.tmp = Path(tempfile.mkdtemp(prefix="oca-mc-live-"))
        fleet_home = self.tmp / "fleet"
        fleet_home.mkdir()
        self.fleet_home = fleet_home
        self.db_path = fleet_home / "health.sqlite"
        (fleet_home / "session-pin.key").write_bytes(b"k" * 32)
        self.db = sqlite3.connect(self.db_path)
        self.db.execute(
            """
            CREATE TABLE fleet_session_pins (
                session_digest TEXT NOT NULL,
                role TEXT NOT NULL,
                candidate_key TEXT NOT NULL,
                routing_profile TEXT NOT NULL DEFAULT '',
                updated_at REAL NOT NULL DEFAULT 0,
                PRIMARY KEY (session_digest, role)
            )
            """
        )
        self.db.commit()
        fleet_home.joinpath("fleet.json").write_text(
            json.dumps(
                {
                    "enabled": True,
                    "roles": {"main": ["pool-b", "pool-e"]},
                    "pools": {
                        "pool-b": {
                            "type": "registry",
                            "providerGroup": "fixture-group-b",
                            "provider": _FIXTURE_FLEET_PROVIDER,
                            "discoveryAliases": [_FIXTURE_FLEET_PROVIDER, "fixture-alias-b"],
                            "freeOnly": True,
                        },
                        "pool-e": {
                            "type": "registry",
                            "providerGroup": "fixture-group-e",
                            "provider": _FIXTURE_NEW_PROVIDER,
                            "discoveryAliases": [_FIXTURE_NEW_PROVIDER],
                            "freeOnly": False,
                        },
                    },
                }
            )
        )
        registry = fleet_home / "registry"
        registry.mkdir()
        registry.joinpath("models.json").write_text(
            json.dumps(
                {
                    "productionModels": {
                        "fixture-group-b": [_FIXTURE_FLEET_MODEL, _FIXTURE_STABLE_MODEL],
                        "fixture-group-e": [_FIXTURE_NEW_MODEL],
                    }
                }
            )
        )

        import agent.hermes_fleet_bridge as fb

        # The handler imports from agent.hermes_fleet_bridge at call time and
        # binds the module-level names. Each env patches the SAME module
        # object (no reload — reload wipes patches), snapshots the original
        # attributes, and restores them in close() so scenarios do not leak.
        self.fb = fb
        self._mod_originals = {}
        for _name in ("_fleet", "_set_pin", "clear_session_pin", "_runtime"):
            self._mod_originals[_name] = getattr(fb, _name)
        fb.ROOT = fleet_home
        fb.CONFIG = fleet_home / "fleet.json"
        fb.PIN_KEY = fleet_home / "session-pin.key"
        os.environ["HERMES_FLEET_HEALTH_DB"] = str(self.db_path)

        self._real_set_pin = fb._set_pin
        self._real_clear = fb.clear_session_pin
        self.set_pin_calls: list[tuple[str, str]] = []
        self.clear_calls: list[tuple[str, str]] = []
        self.faults: dict[str, bool] = {}
        self.close_fault = close_fault  # "none" | "pin" | "restore"
        self._fleet_states: list[str] = []
        self._candidates = list(candidates)

        fb._fleet = self._fake_fleet_factory
        fb._set_pin = self._wrap_set_pin
        fb.clear_session_pin = self._wrap_clear
        fb._runtime = self._fake_runtime

        import gateway.run as gr

        self.gateway_run = gr
        self.runner = gr.GatewayRunner.__new__(gr.GatewayRunner)
        self.runner._session_model_overrides = {}
        self.runner._running_agents = {}
        self.runner._opencloud_last_requested_switch = {}
        self.evict_calls = 0
        self.evict_raises = False
        self.runner._evict_cached_agent = self._evict

    # -- fleet stubs -------------------------------------------------------

    def _fake_fleet_factory(self):
        state = "ok"
        if self._fleet_states:
            state = self._fleet_states.pop(0)
        return _FixtureFleet(
            self.db, self._candidates, close_raises=(state == "raise")
        )

    def _wrap_set_pin(self, fleet, role, session_key, candidate, profile=None):
        key = (
            candidate.get("candidateKey")
            if isinstance(candidate, dict)
            else getattr(candidate, "candidateKey", "")
        )
        self.set_pin_calls.append((session_key, str(key or "")))
        if self.faults.get("set_pin_raises"):
            raise RuntimeError("fixture set_pin failure")
        if self.faults.get("restore_fails") and len(self.set_pin_calls) >= 2:
            raise RuntimeError("fixture pin restore failure")
        return self._real_set_pin(fleet, role, session_key, candidate, profile)

    def _wrap_clear(self, session_key, role="main"):
        self.clear_calls.append((session_key, role))
        if self.faults.get("clear_raises"):
            raise RuntimeError("fixture clear_session_pin failure")
        return self._real_clear(session_key, role)

    def _fake_runtime(self, candidate):
        if self.faults.get("runtime_raises"):
            raise RuntimeError("fixture runtime resolution failure")
        return {
            "api_key": "fixture-key",
            "base_url": "https://fixture.invalid",
            "api_mode": "chat",
            "provider": (
                candidate.get("provider")
                if isinstance(candidate, dict)
                else getattr(candidate, "provider", "")
            ),
        }

    def _evict(self, session_key):
        self.evict_calls += 1
        if self.evict_raises:
            raise RuntimeError("fixture eviction failure")

    # -- setup helpers -----------------------------------------------------

    def set_prev_pin(self, candidate: dict) -> None:
        fleet = _FixtureFleet(self.db, [], False)
        self._real_set_pin(fleet, "main", _SK, candidate, profile=None)

    def current_pin_key(self) -> str:
        info = self.fb.resolve_session_pin_readonly(_SK, "main")
        return str((info or {}).get("candidate_key") or "")

    def arm_close_fault(self) -> None:
        # instance order inside one handler call: 0 = candidate listing,
        # 1 = pin write, 2 = rollback restore / clear.
        self._fleet_states = ["ok", "ok", "ok", "ok"]
        if self.close_fault == "pin":
            self._fleet_states[1] = "raise"
        elif self.close_fault == "restore":
            self._fleet_states[2] = "raise"

    async def _invoke_async(self, text: str) -> str:
        event = types.SimpleNamespace(text=text)
        source = types.SimpleNamespace(platform="imessage")
        result = await self.runner._maybe_handle_model_control_fast_path(
            event, source, _SK
        )
        return str(result or "")

    def invoke(self, text: str) -> str:
        self.arm_close_fault()
        return asyncio.run(self._invoke_async(text))

    def close(self) -> None:
        try:
            self.db.close()
        except Exception:
            pass
        try:
            for _name, _orig in (getattr(self, "_mod_originals", {}) or {}).items():
                setattr(self.fb, _name, _orig)
        except Exception:
            pass


def _assert_failure(out: str, label: str) -> None:
    require("✅" not in out, f"{label}: must not success-ack")
    require("Model switch failed" in out, f"{label}: failure message")
    require("{exc" not in out, f"{label}: no raw exception in user message")
    require("Traceback" not in out, f"{label}: no traceback in user message")


def _assert_no_mutation(env: _LiveGatewayEnv, pin_before: str, override_before: dict) -> None:
    require(env.current_pin_key() == pin_before, "pin must be unchanged")
    cur = env.runner._session_model_overrides.get(_SK) or {}
    require(
        cur.get("model") == override_before.get("model")
        and (cur.get("provider") or "").lower()
        == (override_before.get("provider") or "").lower(),
        "override must be unchanged",
    )
    require(
        _SK not in env.runner._opencloud_last_requested_switch,
        "last_requested_switch must not be set on failure",
    )
    require(env.evict_calls == 0, "evict must not run before failure point")


def test_live_status_executes_readonly(env: _LiveGatewayEnv) -> None:
    override = {
        "provider": _FIXTURE_OVERRIDE_PROVIDER,
        "model": _FIXTURE_OVERRIDE_MODEL,
        "api_key": "k",
    }
    env.runner._session_model_overrides[_SK] = dict(override)
    env.runner._running_agents[_SK] = types.SimpleNamespace(
        model=_FIXTURE_RUNTIME_MODEL, provider=_FIXTURE_RUNTIME_PROVIDER
    )
    last_req = f"{_FIXTURE_FLEET_PROVIDER}/{_FIXTURE_FLEET_MODEL}"
    env.runner._opencloud_last_requested_switch[_SK] = last_req
    env.set_prev_pin(_cand(_FIXTURE_FLEET_PROVIDER, "fixture-group-b", _FIXTURE_FLEET_MODEL))
    pin_before = env.current_pin_key()
    calls_before = (len(env.set_pin_calls), len(env.clear_calls), env.evict_calls)

    out = env.invoke("what model are you using?")
    require("Active route:" in out, "status headline")
    require(_FIXTURE_OVERRIDE_MODEL in out, "status shows manual override model")
    require(f"last_requested_switch={last_req}" in out, "status echoes last_requested_switch")
    require("source=MANUAL_SESSION" in out, "status source is manual session")
    # strictly read-only: pin, override, metadata, and external calls unchanged
    require(env.current_pin_key() == pin_before, "status must not touch the pin")
    require(
        env.runner._session_model_overrides.get(_SK) == override,
        "status must not mutate the override",
    )
    require(
        env.runner._opencloud_last_requested_switch[_SK] == last_req,
        "status must not mutate switch metadata",
    )
    require(
        (len(env.set_pin_calls), len(env.clear_calls), env.evict_calls) == calls_before,
        "status must not call pin/evict machinery",
    )


def test_live_auto_status_ignores_bootstrap(env: _LiveGatewayEnv) -> None:
    calls_before = (len(env.set_pin_calls), len(env.clear_calls), env.evict_calls)
    out = env.invoke("which model are u using?")
    require("Automatic Fleet routing:" in out, "automatic status headline")
    require("source=FLEET_AUTO" in out, "automatic status source")
    require("Configured default:" not in out, "automatic status hides bootstrap")
    require("fleet_pin=no" in out, "automatic status reports no manual pin")
    require(
        (len(env.set_pin_calls), len(env.clear_calls), env.evict_calls) == calls_before,
        "automatic status remains read-only",
    )


def test_live_nl_switch_success(env: _LiveGatewayEnv) -> None:
    prev_override = {"provider": _FIXTURE_OVERRIDE_PROVIDER, "model": _FIXTURE_OVERRIDE_MODEL}
    env.runner._session_model_overrides[_SK] = dict(prev_override)
    prev_pin = _cand(_FIXTURE_FLEET_PROVIDER, "fixture-group-b", _FIXTURE_FLEET_MODEL)
    env.set_prev_pin(prev_pin)

    out = env.invoke(
        f"can u switch to {_FIXTURE_NEW_MODEL} in {_FIXTURE_NEW_PROVIDER}?"
    )
    require("✅" in out, "success ack")
    require(
        f"(was {_FIXTURE_OVERRIDE_PROVIDER}/{_FIXTURE_OVERRIDE_MODEL})" in out,
        "previous route echoed",
    )
    new_pin = _cand(_FIXTURE_NEW_PROVIDER, "fixture-group-e", _FIXTURE_NEW_MODEL)
    require(env.current_pin_key() == new_pin["candidateKey"], "pin replaced (read back)")
    cur = env.runner._session_model_overrides.get(_SK) or {}
    require(
        cur.get("model") == _FIXTURE_NEW_MODEL
        and (cur.get("provider") or "").lower() == _FIXTURE_NEW_PROVIDER,
        "override updated",
    )
    require(env.evict_calls == 1, "cached agent evicted on success")
    require(
        env.runner._opencloud_last_requested_switch.get(_SK)
        == f"{_FIXTURE_NEW_PROVIDER}/{_FIXTURE_NEW_MODEL}",
        "last_requested_switch recorded",
    )
    require(
        env.set_pin_calls[-1][1] == new_pin["candidateKey"],
        "pin write recorded for the target candidate",
    )


def test_live_provider_catalog_truth(env: _LiveGatewayEnv) -> None:
    now_ms = int(time.time() * 1000)
    env.fleet_home.joinpath("registry/models.json").write_text(json.dumps({
        "models": [
            {
                "provider": _FIXTURE_FLEET_PROVIDER,
                "providerGroup": "fixture-group-b",
                "id": "fixture/free-good",
                "explicitFree": True,
                "verification": "verified",
                "verifiedAtMs": now_ms,
                "productionEligible": True,
            },
            {
                "provider": _FIXTURE_FLEET_PROVIDER,
                "providerGroup": "fixture-group-b",
                "id": "fixture/nonfree-specialist",
                "explicitFree": False,
                "excludedReason": "specialist",
                "verification": "unverified",
            },
            {
                "provider": _FIXTURE_FLEET_PROVIDER,
                "providerGroup": "fixture-group-b",
                "id": "fixture/bad-timestamp",
                "explicitFree": True,
                "verification": "verified",
                "verifiedAtMs": "not-a-timestamp",
                "productionEligible": True,
            },
            {
                "provider": _FIXTURE_NEW_PROVIDER,
                "providerGroup": "fixture-group-e",
                "id": "fixture/other-provider",
                "verification": "verified",
                "verifiedAtMs": now_ms,
                "productionEligible": True,
            },
        ]
    }))
    out = env.invoke("list the complete list of free models in fixture-alias-b")
    require("fixture/free-good" in out, "provider alias catalog includes matching free row")
    require("fixture/nonfree-specialist" not in out, "free-only catalog excludes nonfree specialist")
    require("fixture/bad-timestamp" in out, "bad timestamp rejects only its row")
    require("verification-expired" in out, "bad timestamp is not labeled verified")
    require("fixture/other-provider" not in out, "provider-specific catalog excludes other providers")


def test_live_contextual_switch_mismatch_fails_fast(env: _LiveGatewayEnv) -> None:
    prev_override = {
        "provider": _FIXTURE_OVERRIDE_PROVIDER,
        "model": _FIXTURE_OVERRIDE_MODEL,
    }
    env.runner._session_model_overrides[_SK] = dict(prev_override)
    pin_before = env.current_pin_key()

    out = env.invoke(
        f"I will try fixture missing model can u switch to this in {_FIXTURE_NEW_PROVIDER}!!"
    )
    require("Could not switch" in out, "contextual mismatch is handled deterministically")
    _assert_no_mutation(env, pin_before, prev_override)


def test_live_exact_provider_collision(env: _LiveGatewayEnv) -> None:
    env.runner._session_model_overrides[_SK] = {
        "provider": _FIXTURE_OVERRIDE_PROVIDER,
        "model": _FIXTURE_OVERRIDE_MODEL,
    }
    pin_before = env.current_pin_key()

    out = env.invoke(
        f"switch to {_FIXTURE_COLLISION_MODEL} on {_FIXTURE_COLLISION_SHORT}"
    )
    require("Could not switch" in out, "short hint must not resolve the long provider")
    _assert_no_mutation(env, pin_before, env.runner._session_model_overrides[_SK])

    out_long = env.invoke(
        f"switch to {_FIXTURE_COLLISION_MODEL} on {_FIXTURE_COLLISION_LONG}"
    )
    require("✅" in out_long, "exact long provider resolves at the handler")
    cur = env.runner._session_model_overrides.get(_SK) or {}
    require(
        (cur.get("provider") or "").lower() == _FIXTURE_COLLISION_LONG,
        "long provider selected",
    )


def test_live_verified_gemini_switch(env: _LiveGatewayEnv) -> None:
    env.runner._session_model_overrides[_SK] = {
        "provider": _FIXTURE_OVERRIDE_PROVIDER,
        "model": _FIXTURE_OVERRIDE_MODEL,
    }
    out_group = env.invoke(
        f"switch to {_FIXTURE_BLOCKED_MODEL} on {_FIXTURE_BLOCKED_PROVIDER}"
    )
    require("✅" in out_group, "verified Gemini candidate is switchable")

    cur = env.runner._session_model_overrides.get(_SK) or {}
    require(cur.get("model") == _FIXTURE_BLOCKED_MODEL, "Gemini override stored")


def test_live_eviction_failure_verified_rollback(env: _LiveGatewayEnv) -> None:
    prev_override = {"provider": _FIXTURE_OVERRIDE_PROVIDER, "model": _FIXTURE_OVERRIDE_MODEL}
    env.runner._session_model_overrides[_SK] = dict(prev_override)
    prev_pin = _cand(_FIXTURE_FLEET_PROVIDER, "fixture-group-b", _FIXTURE_FLEET_MODEL)
    env.set_prev_pin(prev_pin)
    env.evict_raises = True

    out = env.invoke(
        f"switch to {_FIXTURE_NEW_MODEL} on {_FIXTURE_NEW_PROVIDER}"
    )
    _assert_failure(out, "eviction failure")
    require("Route unchanged." in out, "verified rollback reports unchanged route")
    require(
        env.current_pin_key() == prev_pin["candidateKey"],
        "pin restored exactly (read back)",
    )
    cur = env.runner._session_model_overrides.get(_SK) or {}
    require(
        cur.get("model") == prev_override["model"]
        and (cur.get("provider") or "").lower() == prev_override["provider"],
        "override restored",
    )
    require(env.evict_calls == 1, "eviction attempted once")
    require(
        len(env.set_pin_calls) == 2 and env.set_pin_calls[1][1] == prev_pin["candidateKey"],
        "restore wrote the previous pin",
    )
    require(
        _SK not in env.runner._opencloud_last_requested_switch,
        "no success metadata on rollback",
    )


def test_live_set_pin_failure_rolls_back_override(env: _LiveGatewayEnv) -> None:
    prev_override = {"provider": _FIXTURE_OVERRIDE_PROVIDER, "model": _FIXTURE_OVERRIDE_MODEL}
    env.runner._session_model_overrides[_SK] = dict(prev_override)
    prev_pin = _cand(_FIXTURE_FLEET_PROVIDER, "fixture-group-b", _FIXTURE_FLEET_MODEL)
    env.set_prev_pin(prev_pin)
    env.faults["set_pin_raises"] = True
    out = env.invoke(f"switch to {_FIXTURE_NEW_MODEL} on {_FIXTURE_NEW_PROVIDER}")
    _assert_failure(out, "set-pin failure")
    require("Route unchanged." in out, "set-pin failure verifies rollback")
    require(env.current_pin_key() == prev_pin["candidateKey"], "previous pin remains")
    cur = env.runner._session_model_overrides.get(_SK) or {}
    require(cur.get("model") == prev_override["model"], "override restored after pin failure")


def test_live_clear_failure_unconfirmed(env: _LiveGatewayEnv) -> None:
    prev_override = {"provider": _FIXTURE_OVERRIDE_PROVIDER, "model": _FIXTURE_OVERRIDE_MODEL}
    env.runner._session_model_overrides[_SK] = dict(prev_override)
    env.evict_raises = True
    env.faults["clear_raises"] = True

    out = env.invoke(
        f"switch to {_FIXTURE_NEW_MODEL} on {_FIXTURE_NEW_PROVIDER}"
    )
    _assert_failure(out, "clear failure")
    require(
        "route state could not be confirmed" in out,
        "unverified rollback must not claim unchanged",
    )
    require("Route unchanged." not in out, "no false unchanged claim")
    cur = env.runner._session_model_overrides.get(_SK) or {}
    require(
        cur.get("model") == prev_override["model"],
        "override still restored even when pin state is uncertain",
    )
    new_pin = _cand(_FIXTURE_NEW_PROVIDER, "fixture-group-e", _FIXTURE_NEW_MODEL)
    require(
        env.current_pin_key() == new_pin["candidateKey"],
        "new pin remains (honest unconfirmed state)",
    )
    require(len(env.clear_calls) == 1, "clear attempted once")


def test_live_unresolvable_prev_pin_refusal(env: _LiveGatewayEnv) -> None:
    prev_override = {"provider": _FIXTURE_OVERRIDE_PROVIDER, "model": _FIXTURE_OVERRIDE_MODEL}
    env.runner._session_model_overrides[_SK] = dict(prev_override)
    ghost = _cand(_FIXTURE_FLEET_PROVIDER, "fixture-group-b", "fixture/ghost-model")
    env.set_prev_pin(ghost)
    pin_before = env.current_pin_key()
    require(bool(pin_before), "ghost pin seeded")

    out = env.invoke(
        f"switch to {_FIXTURE_NEW_MODEL} on {_FIXTURE_NEW_PROVIDER}"
    )
    _assert_failure(out, "unresolvable previous pin")
    require(
        "route state could not be confirmed" in out,
        "refusal before mutation uses honest message",
    )
    require(env.current_pin_key() == pin_before, "ghost pin untouched")
    require(len(env.set_pin_calls) == 0, "no pin write attempted")
    require(env.evict_calls == 0, "no eviction attempted")
    cur = env.runner._session_model_overrides.get(_SK) or {}
    require(
        cur.get("model") == prev_override["model"],
        "override untouched before refusal",
    )


def test_live_restore_failure_unconfirmed(env: _LiveGatewayEnv) -> None:
    prev_override = {"provider": _FIXTURE_OVERRIDE_PROVIDER, "model": _FIXTURE_OVERRIDE_MODEL}
    env.runner._session_model_overrides[_SK] = dict(prev_override)
    prev_pin = _cand(_FIXTURE_FLEET_PROVIDER, "fixture-group-b", _FIXTURE_FLEET_MODEL)
    env.set_prev_pin(prev_pin)
    env.evict_raises = True
    env.faults["restore_fails"] = True

    out = env.invoke(
        f"switch to {_FIXTURE_NEW_MODEL} on {_FIXTURE_NEW_PROVIDER}"
    )
    _assert_failure(out, "restore failure")
    require(
        "route state could not be confirmed" in out,
        "restore failure must not claim unchanged",
    )
    require("Route unchanged." not in out, "no false unchanged claim")
    cur = env.runner._session_model_overrides.get(_SK) or {}
    require(
        cur.get("model") == prev_override["model"],
        "override restored even when pin restore failed",
    )
    new_pin = _cand(_FIXTURE_NEW_PROVIDER, "fixture-group-e", _FIXTURE_NEW_MODEL)
    require(
        env.current_pin_key() == new_pin["candidateKey"],
        "new pin remains (honest unconfirmed state)",
    )
    require(len(env.set_pin_calls) == 2, "write + failed restore attempted")


def test_live_set_pin_commit_close_failure(env: _LiveGatewayEnv) -> None:
    # (a) committed _set_pin() + fleet.close() raises, eviction succeeds:
    #     the new pin is real, so the success ack is truthful.
    env.runner._session_model_overrides[_SK] = {
        "provider": _FIXTURE_OVERRIDE_PROVIDER,
        "model": _FIXTURE_OVERRIDE_MODEL,
    }
    out = env.invoke(
        f"switch to {_FIXTURE_NEW_MODEL} on {_FIXTURE_NEW_PROVIDER}"
    )
    require("✅" in out, "close noise after committed pin must not suppress truthful ack")
    new_pin = _cand(_FIXTURE_NEW_PROVIDER, "fixture-group-e", _FIXTURE_NEW_MODEL)
    require(
        env.current_pin_key() == new_pin["candidateKey"],
        "committed pin read back as the new route",
    )
    require(env.evict_calls == 1, "eviction completed")

    # (b) same close failure but eviction fails: rollback must restore the
    #     previous pin and verify it — not leave the new pin behind.
    env2 = _LiveGatewayEnv(env.out, [env._candidates[0], env._candidates[1]])
    env2.runner._session_model_overrides[_SK] = {
        "provider": _FIXTURE_OVERRIDE_PROVIDER,
        "model": _FIXTURE_OVERRIDE_MODEL,
    }
    prev_pin = _cand(_FIXTURE_FLEET_PROVIDER, "fixture-group-b", _FIXTURE_FLEET_MODEL)
    env2.set_prev_pin(prev_pin)
    env2.evict_raises = True
    out2 = env2.invoke(
        f"switch to {_FIXTURE_NEW_MODEL} on {_FIXTURE_NEW_PROVIDER}"
    )
    _assert_failure(out2, "close failure + eviction failure")
    require("Route unchanged." in out2, "verified rollback after close failure")
    require(
        env2.current_pin_key() == prev_pin["candidateKey"],
        "previous pin restored after close failure (read back)",
    )
    env2.close()


def test_live_no_prev_pin_success(env: _LiveGatewayEnv) -> None:
    out = env.invoke(
        f"switch to {_FIXTURE_NEW_MODEL} on {_FIXTURE_NEW_PROVIDER}"
    )
    require("✅" in out, "fresh session switch succeeds")
    new_pin = _cand(_FIXTURE_NEW_PROVIDER, "fixture-group-e", _FIXTURE_NEW_MODEL)
    require(env.current_pin_key() == new_pin["candidateKey"], "pin written for fresh session")
    require(env.evict_calls == 1, "cached agent evicted")
    require(
        env.runner._opencloud_last_requested_switch.get(_SK)
        == f"{_FIXTURE_NEW_PROVIDER}/{_FIXTURE_NEW_MODEL}",
        "success metadata recorded",
    )


def run_live_handler_tests(out: Path) -> None:
    """Invoke the REAL materialized GatewayRunner handler end to end."""
    import sys as _sys

    # Live handler import needs yaml (HERMES_PYTHON gate) and may transitively
    # touch dotenv via gateway.run even though model-control itself does not.
    try:
        import yaml  # noqa: F401
    except ImportError as exc:
        raise AssertionError(
            "yaml required for materialized handler tests; run via HERMES_PYTHON"
        ) from exc

    # The OCI Hermes venv is an editable install of the live tree.  Remove any
    # modules it loaded before importing the disposable materialized fixture.
    for name in tuple(_sys.modules):
        if name in {"agent", "gateway"} or name.startswith(("agent.", "gateway.")):
            _sys.modules.pop(name, None)
    _sys.path.insert(0, str(out))
    with _dotenv_stub_if_needed():
        try:
            import gateway.run as gr  # noqa: F401 — real class + handler
        except Exception as exc:  # pragma: no cover - environment failure
            raise AssertionError(
                f"could not import real gateway.run from materialized tree: {exc}"
            ) from exc

        require(
            Path(gr.__file__).resolve().is_relative_to(out.resolve()),
            f"gateway.run imported outside materialized tree: {gr.__file__}",
        )
        import agent.hermes_fleet_bridge as fixture_bridge

        require(
            Path(fixture_bridge.__file__).resolve().is_relative_to(out.resolve()),
            "Fleet bridge imported outside materialized tree: "
            f"{fixture_bridge.__file__}",
        )

        handler = getattr(
            gr.GatewayRunner, "_maybe_handle_model_control_fast_path", None
        )
        require(
            callable(handler),
            "materialized GatewayRunner must expose _maybe_handle_model_control_fast_path",
        )

        # One env per scenario, created right before its test and closed right
        # after so module-level patches never leak across scenarios.
        scenarios = [
            (
                [
                    _cand(_FIXTURE_FLEET_PROVIDER, "fixture-group-b", _FIXTURE_FLEET_MODEL),
                ],
                test_live_status_executes_readonly,
                {},
            ),
            (
                [
                    _cand(_FIXTURE_FLEET_PROVIDER, "fixture-group-b", _FIXTURE_FLEET_MODEL),
                ],
                test_live_auto_status_ignores_bootstrap,
                {},
            ),
            (
                [
                    _cand(_FIXTURE_FLEET_PROVIDER, "fixture-group-b", _FIXTURE_FLEET_MODEL),
                ],
                test_live_provider_catalog_truth,
                {},
            ),
            (
                [
                    _cand(_FIXTURE_FLEET_PROVIDER, "fixture-group-b", _FIXTURE_FLEET_MODEL),
                    _cand(_FIXTURE_NEW_PROVIDER, "fixture-group-e", _FIXTURE_NEW_MODEL),
                ],
                test_live_nl_switch_success,
                {},
            ),
            (
                [
                    _cand(_FIXTURE_NEW_PROVIDER, "fixture-group-e", _FIXTURE_NEW_MODEL),
                ],
                test_live_contextual_switch_mismatch_fails_fast,
                {},
            ),
            (
                [
                    _cand(_FIXTURE_COLLISION_LONG, _FIXTURE_COLLISION_LONG, _FIXTURE_COLLISION_MODEL),
                    _cand(_FIXTURE_COLLISION_SHORT, _FIXTURE_COLLISION_SHORT, "fixture/other-model"),
                ],
                test_live_exact_provider_collision,
                {},
            ),
            (
                [
                    _cand(_FIXTURE_BLOCKED_PROVIDER, "gemini", _FIXTURE_BLOCKED_MODEL),
                    _cand("google-gemini", "google-gemini", "fixture/blocked-provider-model"),
                    _cand(_FIXTURE_FLEET_PROVIDER, "fixture-group-b", _FIXTURE_FLEET_MODEL),
                ],
                test_live_verified_gemini_switch,
                {},
            ),
            (
                [
                    _cand(_FIXTURE_FLEET_PROVIDER, "fixture-group-b", _FIXTURE_FLEET_MODEL),
                    _cand(_FIXTURE_NEW_PROVIDER, "fixture-group-e", _FIXTURE_NEW_MODEL),
                ],
                test_live_eviction_failure_verified_rollback,
                {},
            ),
            (
                [
                    _cand(_FIXTURE_FLEET_PROVIDER, "fixture-group-b", _FIXTURE_FLEET_MODEL),
                    _cand(_FIXTURE_NEW_PROVIDER, "fixture-group-e", _FIXTURE_NEW_MODEL),
                ],
                test_live_set_pin_failure_rolls_back_override,
                {},
            ),
            (
                [
                    _cand(_FIXTURE_FLEET_PROVIDER, "fixture-group-b", _FIXTURE_FLEET_MODEL),
                    _cand(_FIXTURE_NEW_PROVIDER, "fixture-group-e", _FIXTURE_NEW_MODEL),
                ],
                test_live_clear_failure_unconfirmed,
                {},
            ),
            (
                [
                    _cand(_FIXTURE_FLEET_PROVIDER, "fixture-group-b", _FIXTURE_FLEET_MODEL),
                    _cand(_FIXTURE_NEW_PROVIDER, "fixture-group-e", _FIXTURE_NEW_MODEL),
                ],
                test_live_unresolvable_prev_pin_refusal,
                {},
            ),
            (
                [
                    _cand(_FIXTURE_FLEET_PROVIDER, "fixture-group-b", _FIXTURE_FLEET_MODEL),
                    _cand(_FIXTURE_NEW_PROVIDER, "fixture-group-e", _FIXTURE_NEW_MODEL),
                ],
                test_live_restore_failure_unconfirmed,
                {},
            ),
            (
                [
                    _cand(_FIXTURE_FLEET_PROVIDER, "fixture-group-b", _FIXTURE_FLEET_MODEL),
                    _cand(_FIXTURE_NEW_PROVIDER, "fixture-group-e", _FIXTURE_NEW_MODEL),
                ],
                test_live_set_pin_commit_close_failure,
                {"close_fault": "pin"},
            ),
            (
                [
                    _cand(_FIXTURE_NEW_PROVIDER, "fixture-group-e", _FIXTURE_NEW_MODEL),
                ],
                test_live_no_prev_pin_success,
                {},
            ),
        ]
        for cands, test_fn, kwargs in scenarios:
            env = _LiveGatewayEnv(out, cands, **kwargs)
            try:
                test_fn(env)
            finally:
                env.close()


def test_automatic_bootstrap_fails_closed() -> None:
    patch = (ROOT / 'integrations/hermes/hermes-live.patch').read_text()
    added = '\n'.join(line[1:] for line in patch.splitlines() if line.startswith('+') and not line.startswith('+++'))
    body = added.split('# HERMES_FLEET_MAIN_ATTACH_BEGIN', 1)[1].split('# HERMES_FLEET_MAIN_ATTACH_END', 1)[0]
    bridge_name = 'agent.hermes_fleet_bridge'
    saved = sys.modules.get(bridge_name)
    bridge = types.ModuleType(bridge_name)
    bridge.should_manage_main = lambda **kwargs: True
    def unavailable(*args, **kwargs):
        raise RuntimeError('fixture unavailable')
    bridge.resolve_role = unavailable
    sys.modules[bridge_name] = bridge
    ns = dict(model=_FIXTURE_BOOTSTRAP_MODEL, provider=_FIXTURE_BOOTSTRAP_PROVIDER,
              requested_provider=None, gateway_session_key=_SK,
              logger=types.SimpleNamespace(warning=lambda *args: None))
    try:
        try:
            exec(textwrap.dedent(body), ns)
        except RuntimeError as exc:
            require('Automatic Fleet routing unavailable' in str(exc), 'sanitized bootstrap failure')
        else:
            raise AssertionError('automatic Fleet failure must not use legacy configured model')
        bridge.should_manage_main = lambda **kwargs: False
        exec(textwrap.dedent(body), ns)
        require(ns['model'] == _FIXTURE_BOOTSTRAP_MODEL, 'non-Fleet explicit routes remain untouched')
    finally:
        if saved is None:
            sys.modules.pop(bridge_name, None)
        else:
            sys.modules[bridge_name] = saved


def main() -> None:
    test_automatic_bootstrap_fails_closed()
    print('PASS automatic bootstrap failure cannot use legacy configured model')
    assert_canonical_runner_uses_hermes_python()
    print("PASS canonical runner uses HERMES_PYTHON for PR-A")
    patch_applies()
    text = PATCH.read_text()
    for marker in (
        "HERMES_OPENCLOUD_MODEL_CONTROL_FAST_PATH_V1",
        "HERMES_OPENCLOUD_CLARIFY_RELEASE_V1",
        "HERMES_OPENCLOUD_STOP_RECOVERY_V1",
        "HERMES_OPENCLOUD_PROGRESS_STATE_V1",
        "HERMES_OPENCLOUD_TOOL_RESULT_TRUTH_V1",
        "WAIT_FOR_USER",
        "_maybe_handle_model_control_fast_path",
        "gateway/model_control_fast_path.py",
        "resolve_model_status_snapshot",
        "resolve_session_pin_readonly",
        "CONFIGURED_FALLBACK",
        "Fleet pinned route",
        "_opencloud_last_requested_switch",
        "last_requested_switch",
        "_readonly_fleet_candidate",
    ):
        require(marker in text, f"missing marker/content {marker}")

    require(
        "_rollback_model_switch" in text,
        "verified rollback helper must be wired",
    )
    require(
        "Model switch failed; route state could not be confirmed." in text,
        "honest unconfirmed-state message required",
    )
    require(
        "LAST_SUCCESSFUL" not in text.split("def resolve_model_status_snapshot(", 1)[1].split(
            "def format_model_status(", 1
        )[0],
        "LAST_SUCCESSFUL must not drive precedence",
    )
    require(
        "last_successful" not in text,
        "last_successful metadata must be renamed",
    )
    require(
        "_PROVIDER_ALIASES" not in text,
        "hard-coded provider aliases must not remain",
    )
    require(
        "runtime = await asyncio.to_thread(_runtime, resolved.candidate)" in text,
        "switch validates runtime construction",
    )
    require(
        "self._session_model_overrides[session_key] = prev_override" in text,
        "failed switch rolls back override",
    )
    require("last_ok" not in text, "status handler must not reference undefined last_ok")
    require(
        "model-control evict cached agent failed" in text,
        "eviction failure logged server-side",
    )
    require(
        "pin rollback failed" in text or "pin rollback" in text.lower(),
        "pin rollback on eviction failure",
    )
    require(
        "switch to <model-id> on <provider>" in text,
        "ambiguity instructs structured follow-up",
    )
    require(
        "Exact normalized match only" in text or "acme ≠ acme-plus" in text,
        "provider hint exact-match guard",
    )
    require(
        "list_fleet_main_candidates" in text and "_available" in text,
        "candidates come from verified Fleet availability",
    )

    status_chunk = text.split('if intent.kind == "status":', 1)[1].split(
        'if intent.kind != "switch":', 1
    )[0]
    for forbidden in (
        "_set_pin(",
        "clear_session_pin(",
        "resolve_role(",
        "session_is_pinned(",
    ):
        require(forbidden not in status_chunk, f"status path must not call {forbidden}")
    require(
        "resolve_session_pin_readonly" in status_chunk,
        "status path must resolve Fleet pin read-only",
    )
    test_readonly_status_no_mutation(text)
    test_switch_failure_no_exception_leak(text)

    bridge_chunk = extract_fleet_bridge_insert(text)
    test_readonly_pin_helper(bridge_chunk)
    test_stable_and_dynamic_readonly_routes(bridge_chunk)

    for rel in ("install/30-brain-materialize.sh", "install/35-hermes-live.sh"):
        s = (ROOT / rel).read_text()
        require(
            "hermes-imessage-model-control-turn-recovery.patch" in s,
            f"{rel} missing patch wire",
        )

    with tempfile.TemporaryDirectory(prefix="oca-model-control-pure-") as pure_tmp:
        pure_path = Path(pure_tmp) / "model_control_fast_path.py"
        pure_path.write_text(
            extract_new_file_from_patch(text, "gateway/model_control_fast_path.py")
        )
        m = load_module_from(pure_path, "opencloud_model_control_fast_path_pure")
        test_status_precedence(m)
        test_nl_switch_forms(m)
        test_catalog_intent_and_bounds(m)
        test_alias_resolution(m)
        test_provider_prefix_collision(m)
        test_ambiguity_followup_parsing(m)
        test_readonly_sqlite_no_writes(bridge_chunk)

        # Shared eligibility boundary from the REAL fleet-bridge patch code.
        eligibility_path = Path(pure_tmp) / "eligibility.py"
        eligibility_path.write_text(
            extract_eligibility_fns(FLEET_BRIDGE_PATCH.read_text())
        )
        em = load_module_from(eligibility_path, "opencloud_eligibility_boundary")
        test_shared_eligibility_boundary(em)

    scan_no_concrete_models(text)
    scan_no_concrete_models(FLEET_BRIDGE_PATCH.read_text())

    print("PASS patch parses (git apply --stat)")
    print("PASS status-source precedence (patch-extracted)")
    print("PASS natural-language switch forms")
    print("PASS natural-language catalog + bounded provider filtering")
    print("PASS Fleet alias resolve + ambiguity")
    print("PASS provider-prefix collision guard")
    print("PASS ambiguity follow-up parsing")
    print("PASS provider-neutral shared eligibility boundary")
    print("PASS stable switch failure messages")
    print("PASS read-only status path")
    print("PASS stable-route readonly pin (no registry)")
    print("PASS dynamic registry + readonly sqlite")
    print("PASS no concrete model ids in patches")

    if not hermes_git_available():
        print("SKIP materialize (Hermes Git unavailable)")
        print("SKIP gateway/Fleet behavioral checks (Hermes Git unavailable)")
        return

    with tempfile.TemporaryDirectory(prefix="oca-model-control-") as tmp:
        out = Path(tmp) / "hermes"
        materialize(out)

        mod_path = out / "gateway" / "model_control_fast_path.py"
        require(mod_path.is_file(), "model_control_fast_path.py missing after materialize")
        m = load_module_from(mod_path, "opencloud_model_control_fast_path")
        test_status_precedence(m)
        test_nl_switch_forms(m)
        test_catalog_intent_and_bounds(m)
        test_alias_resolution(m)
        test_provider_prefix_collision(m)
        test_ambiguity_followup_parsing(m)

        require(
            m.detect_model_control_intent("What model are you using?").kind == "status",
            "status intent",
        )
        require(
            m.tool_result_is_success('{"exit_code": 1, "output": "nope"}') is False,
            "nonzero is failure",
        )

        fleet_bridge = (out / "agent" / "hermes_fleet_bridge.py").read_text()
        test_readonly_pin_helper(fleet_bridge)
        test_stable_and_dynamic_readonly_routes(fleet_bridge)
        test_readonly_sqlite_no_writes(extract_fleet_bridge_functions(fleet_bridge))

        run_src = (out / "gateway" / "run.py").read_text()
        require("resolve_model_status_snapshot" in run_src, "status snapshot wire")
        require("last_ok" not in run_src, "materialized status must not use last_ok")
        require("last_requested_switch=last_req" in run_src, "status uses last_req")
        require("_opencloud_last_requested_switch" in run_src, "requested-switch metadata")
        require("HERMES_OPENCLOUD_CLARIFY_RELEASE_V1" in run_src, "clarify release")
        idx = run_src.index("HERMES_OPENCLOUD_CLARIFY_RELEASE_V1")
        require("wait_for_response" not in run_src[idx : idx + 3500], "clarify must release")
        require("_rollback_model_switch" in run_src, "verified rollback helper wired")
        require(
            "Model switch failed; route state could not be confirmed." in run_src,
            "honest unconfirmed-state message in materialized handler",
        )

        # Compile materialized Python touched by the patch.
        py_targets = [
            out / "gateway" / "model_control_fast_path.py",
            out / "agent" / "hermes_fleet_bridge.py",
            out / "gateway" / "run.py",
            out / "tools" / "terminal_tool.py",
        ]
        compile_result = subprocess.run(
            [sys.executable, "-m", "py_compile", *[str(p) for p in py_targets]],
            capture_output=True,
            text=True,
        )
        require(
            compile_result.returncode == 0,
            "materialized python compile failed: "
            + (compile_result.stderr or compile_result.stdout or "").strip(),
        )

        term = (out / "tools" / "terminal_tool.py").read_text()
        require("HERMES_OPENCLOUD_TOOL_RESULT_TRUTH_V1" in term, "terminal truth marker")

        # The real materialized handler, invoked end to end.
        run_live_handler_tests(out)

    print("PASS materialize")
    print("PASS pinned Hermes materialization")
    print("PASS materialized python compile")
    print("PASS clarify release markers")
    print("PASS installers wire model-control patch")
    print("PASS real GatewayRunner handler: status executes read-only")
    print("PASS real GatewayRunner handler: NL switch + exact provider")
    print("PASS real GatewayRunner handler: verified Gemini switch")
    print("PASS real GatewayRunner handler: pin rollback fault paths (truthful)")
    print("PASS real GatewayRunner handler: no false success, generic errors")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        import traceback

        traceback.print_exc()
        print(f"FAIL {exc}", file=sys.stderr)
        sys.exit(1)
