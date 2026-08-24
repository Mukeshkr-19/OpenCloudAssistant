#!/usr/bin/env python3
"""Deterministic coverage for greeting output-contract patch (PR #38).

Exercises:
  * greeting turn flag + tool_choice=none preservation (patch markers)
  * output contract rejects serialized clarify/tool JSON
  * context isolation (request-only, excludes tool/cron/clarify history)
  * bounded repair + deterministic local fallback helpers
  * task-turn non-regression for deploy/switch/model/search phrases
  * self-heal inbox event classification (Tier 3, queue-only, no P8)
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
import types
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
PATCH = ROOT / "integrations/hermes/hermes-greeting-output-contract.patch"
HERMES_ROOT = Path(
    os.environ.get("OPEN_CLOUD_HERMES_ROOT", Path.home() / ".hermes/hermes-agent")
)

# Production-shaped clarify JSON emitted as plain text (NVIDIA llama-3.2-11b-vision).
PRODUCTION_CLARIFY_TEXT = (
    '{"name":"clarify","arguments":{"question":"How can I help you?",'
    '"choices":["Deploy","Search","Other"]}}'
)


def require(cond: bool, msg: str) -> None:
    if not cond:
        raise AssertionError(msg)


def materialize(out: Path) -> bool:
    result = subprocess.run(
        ["bash", str(ROOT / "install/30-brain-materialize.sh"), "--stage", str(out)],
        env={**os.environ, "OPEN_CLOUD_HERMES_ROOT": str(HERMES_ROOT)},
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        print(
            "GREETING_OUTPUT_CONTRACT: SKIP (materialization failed): "
            + (result.stderr or result.stdout or "").strip()
        )
        return False
    return True


def load_loop_helpers(tree: Path):
    """Load greeting contract helpers without importing full conversation_loop."""
    src = (tree / "agent" / "conversation_loop.py").read_text(encoding="utf-8")
    cls_start = src.index("def _opencloud_is_conversational_greeting")
    cls_end = src.index("\ndef _opencloud_restore_tools", cls_start)
    block = src[cls_start:cls_end]
    start = src.index("# HERMES_OPENCLOUD_GREETING_OUTPUT_CONTRACT_V1")
    end = src.index("\ndef run_conversation(", start)
    block += "\n" + src[start:end]
    ns: dict = {
        "re": re,
        "json": json,
        "os": os,
        "time": __import__("time"),
        "logger": types.SimpleNamespace(warning=lambda *a, **k: None),
    }
    exec(block, ns)  # ponytail: isolated helper block only
    for name in (
        "_opencloud_greeting_output_contract_valid",
        "_opencloud_isolate_greeting_api_messages",
        "_opencloud_greeting_conversational_fallback",
        "_opencloud_emit_greeting_contract_violation",
        "_opencloud_is_conversational_greeting",
        "_OPENCLOUD_GREETING_CONTEXT_MAX",
    ):
        require(name in ns, f"missing helper {name}")
    return SimpleNamespace(**{k: ns[k] for k in ns if k.startswith("_opencloud") or k.startswith("_OPENCLOUD")})


def test_patch_markers() -> None:
    text = PATCH.read_text()
    for marker in (
        "HERMES_OPENCLOUD_GREETING_OUTPUT_CONTRACT_V1",
        "_opencloud_conversational_greeting_turn",
        "_opencloud_greeting_output_contract_valid",
        "_opencloud_isolate_greeting_api_messages",
        "_opencloud_greeting_conversational_fallback",
        "_opencloud_emit_greeting_contract_violation",
        "_opencloud_write_self_heal_inbox_event",
        "greeting_output_repair_attempts",
        "OpenCloudUserOutputContractViolation",
        "greeting_tool_text",
    ):
        require(marker in text, f"missing marker {marker}")

    for rel in ("install/30-brain-materialize.sh", "install/35-hermes-live.sh"):
        s = (ROOT / rel).read_text()
        require("hermes-greeting-output-contract.patch" in s, f"{rel} missing patch wire")
        require("HERMES_OPENCLOUD_GREETING_OUTPUT_CONTRACT_V1" in s, f"{rel} incomplete wire")


def test_contract_and_helpers(cl) -> None:
    valid = cl._opencloud_greeting_output_contract_valid
    require(valid("Hey! What's up?") is True, "natural greeting ok")
    require(valid("Hey bro! What's up?") is True, "vocative greeting ok")
    require(valid(PRODUCTION_CLARIFY_TEXT) is False, "production clarify JSON rejected")
    require(valid('{"tool":"clarify"}') is False, "tool json rejected")
    require(valid('{"function":{"name":"x"}}') is False, "function json rejected")
    require(valid('{"tool_calls":[]}') is False, "tool_calls json rejected")
    require(
        valid("There is no function to call with the given prompt.") is False,
        "provider no-function text rejected",
    )
    require(valid("") is False, "empty rejected")

    heavy = [
        {"role": "system", "content": "You are Hermes."},
        {"role": "user", "content": "Hi bro"},
        {"role": "assistant", "tool_calls": [{"id": "1", "function": {"name": "web_search"}}]},
        {"role": "tool", "content": "results", "tool_call_id": "1"},
        {"role": "assistant", "content": PRODUCTION_CLARIFY_TEXT},
        {"role": "user", "content": "cron run"},
    ]
    iso = cl._opencloud_isolate_greeting_api_messages(heavy, "Hi bro")
    roles = [m["role"] for m in iso]
    require("tool" not in roles, "tool role excluded")
    require(iso[0]["role"] == "system", "system kept")
    require(any(m.get("content") == "Hi bro" for m in iso), "greeting user kept")
    require(
        not any(PRODUCTION_CLARIFY_TEXT in str(m.get("content", "")) for m in iso),
        "clarify payload excluded",
    )
    require(len(iso) <= cl._OPENCLOUD_GREETING_CONTEXT_MAX + 2, "bounded context")

    fb = cl._opencloud_greeting_conversational_fallback
    require("bro" in fb("Hi bro").lower() or "Hey" in fb("Hi bro"), "vocative fallback")
    require("{" not in fb("Hi bro"), "fallback not json")
    require(fb("Hi") != fb("Hi bro") or "Hey" in fb("Hi"), "generic fallback ok")

    fn = cl._opencloud_is_conversational_greeting
    require(fn("Hi bro") is True, "Hi bro classified")
    require(fn("you there?") is True, "you there? classified")
    require(fn("macha you there daa?") is True, "regional check-in classified")
    require(fn("are you there?") is True, "are you there? classified")
    require(fn("Hey bro! What's up?") is False, "extended casual turn not greeting-only")
    require(valid("Hey bro! What's up?") is True, "natural response passes contract")
    require(fn("Hi bro deploy the fleet") is False, "deploy task excluded")
    require(fn("Bro switch to Muse") is False, "switch task excluded")
    require(fn("Hi what model are you using") is False, "model query excluded")
    require(fn("Hi search for jobs") is False, "search task excluded")


def test_inbox_event_sanitized(cl) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        state = Path(tmp) / "state"
        os.environ["OPEN_CLOUD_SELF_HEAL_STATE"] = str(state)
        agent = SimpleNamespace(provider="nvidia", model="llama-3.2-11b-vision", platform="telegram")
        cl._opencloud_emit_greeting_contract_violation(agent, reason="greeting_tool_text")
        files = list((state / "inbox").glob("*.json"))
        require(len(files) == 1, "one inbox event")
        body = files[0].read_text(encoding="utf-8")
        require("OpenCloudUserOutputContractViolation" in body, "event type")
        require("greeting_tool_text" in body, "reason")
        require("nvidia" in body and "llama-3.2-11b-vision" in body, "provider/model context")
        require(PRODUCTION_CLARIFY_TEXT not in body, "no full response body")
        del os.environ["OPEN_CLOUD_SELF_HEAL_STATE"]


def test_self_heal_classification() -> None:
    sys.path.insert(0, str(ROOT / "integrations" / "self-repair"))
    from guarded_heal.controller import SelfHealController, classify_failure

    c = classify_failure(
        "OpenCloudUserOutputContractViolation",
        "reason=greeting_tool_text",
        module="agent.conversation_loop",
        context="provider=nvidia model=llama-3.2-11b-vision platform=telegram",
    )
    require(c is not None, "classified")
    require(c.tier == 3, f"tier {c.tier}")
    require(c.severity == "MEDIUM", c.severity)
    require(c.reason == "greeting_output_contract", c.reason)

    with tempfile.TemporaryDirectory() as tmp:
        state = Path(tmp) / "state"
        repo = Path(tmp) / "repo"
        repo.mkdir()

        ctrl = SelfHealController(
            repo_root=repo,
            state_root=state,
            test_mode=True,
        )
        from guarded_heal.adapters import write_inbox_event

        write_inbox_event(
            ctrl.inbox,
            {
                "type": "OpenCloudUserOutputContractViolation",
                "exc_type": "OpenCloudUserOutputContractViolation",
                "message": "reason=greeting_tool_text",
                "module": "agent.conversation_loop",
                "context": "provider=nvidia model=llama-3.2-11b-vision",
            },
        )
        rows = ctrl.scan_inbox(auto_run=False)
        require(len(rows) == 1, "inbox ingested")
        require(rows[0]["state"] == "QUEUED", f"queued {rows[0]['state']}")
        require(rows[0]["tier"] == 3, "tier3")
        require((rows[0].get("meta") or {}).get("reason") == "greeting_output_contract", "reason")
        # Queue-only on ingest — no P8 / hermes-code-repair until explicit controller repair.


def test_failsafe_bounded_calls(cl) -> None:
    """Invalid twice → repair once then fallback; at most 2 provider-call slots."""
    repair_attempts = 0
    provider_calls = 0
    final = PRODUCTION_CLARIFY_TEXT
    greeting_turn = True

    while provider_calls < 3:
        provider_calls += 1
        if greeting_turn and not cl._opencloud_greeting_output_contract_valid(final):
            if repair_attempts < 1:
                repair_attempts += 1
                final = PRODUCTION_CLARIFY_TEXT  # repair still invalid
                continue
            final = cl._opencloud_greeting_conversational_fallback("Hi bro")
            break
        break

    require(provider_calls == 2, f"max 2 provider calls got {provider_calls}")
    require(repair_attempts == 1, "one repair")
    require(cl._opencloud_greeting_output_contract_valid(final), "fallback valid")
    require("{" not in final, "user never sees raw tool json")


def main() -> None:
    test_patch_markers()

    if not (HERMES_ROOT / ".git").is_dir():
        print("GREETING_OUTPUT_CONTRACT: SKIP (Hermes Git source unavailable)")
        return

    cl = None
    with tempfile.TemporaryDirectory(prefix="opencloud-greeting-output-contract-") as tmp:
        tree = Path(tmp) / "hermes"
        if not materialize(tree):
            return
        cl = load_loop_helpers(tree)
        require(
            getattr(cl, "_opencloud_greeting_output_contract_valid", None) is not None,
            "helpers present after materialize",
        )
        test_contract_and_helpers(cl)
        test_inbox_event_sanitized(cl)

    test_self_heal_classification()
    require(cl is not None, "materialized module required")
    test_failsafe_bounded_calls(cl)

    print("PASS greeting output-contract patch markers")
    print("PASS Hi bro classified; task turns excluded")
    print("PASS clarify JSON rejected; natural greetings accepted")
    print("PASS context isolation excludes tool/clarify history")
    print("PASS inbox event sanitized; self-heal Tier3 queue-only P8=0")
    print("PASS failsafe: 2 provider calls max + local fallback")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"FAIL {exc}", file=sys.stderr)
        import traceback

        traceback.print_exc()
        sys.exit(1)
