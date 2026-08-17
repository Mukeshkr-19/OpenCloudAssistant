#!/usr/bin/env python3
"""Deterministic regression coverage for the P13 cron-control fast-path patch.

A high-confidence career cron-control message ("run my career report now",
"pause/resume/status my career report") must be handled *before* the gateway
loads a long session transcript, runs context compression, or invokes the
conversational LLM. P13 adds:

  * ``gateway/cron_control_fast_path.py`` — a pure intent classifier + canonical
    job resolver (id ``a6cc8dd39f62`` / ``output_schema=="career_job_match_v1"``),
  * ``gateway/run.py`` wiring that short-circuits ``_handle_message`` ahead of
    ``_handle_message_with_agent``, and
  * ``HERMES_P13_DIAGNOSTIC_SUPPRESSION_V1`` — suppresses compression/cooldown/
    timeout/iteration diagnostics from chat surfaces (they stay in logs).

Network-free and store-isolated: the tree is materialized in a temp dir via the
real ``install/30-brain-materialize.sh`` path; ``gateway.cron_control_fast_path``
and ``tools.cronjob_tools`` are imported from that tree with internals
monkeypatched; the gateway regex/wiring is asserted at source level so no heavy
``gateway.run`` import is required.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import tempfile
import types
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
HERMES_ROOT = Path(os.environ.get("OPEN_CLOUD_HERMES_ROOT", Path.home() / ".hermes/hermes-agent"))
PATCH = ROOT / "integrations/hermes/hermes-cron-control-fast-path.patch"

CANONICAL_JOB_ID = "a6cc8dd39f62"
CAREER_OUTPUT_SCHEMA = "career_job_match_v1"
RECURRING_SCHEDULE = "30 9 * * *"


def _career_job() -> dict:
    return {
        "id": CANONICAL_JOB_ID,
        "name": "Daily Career Job Match Report",
        "output_schema": CAREER_OUTPUT_SCHEMA,
        "enabled": True,
        "state": "scheduled",
        "schedule_display": RECURRING_SCHEDULE,
        "schedule": {"kind": "cron", "expr": RECURRING_SCHEDULE, "display": RECURRING_SCHEDULE},
    }


def materialize(out: Path) -> bool:
    result = subprocess.run(
        ["bash", str(ROOT / "install/30-brain-materialize.sh"), "--stage", str(out)],
        env={**os.environ, "OPEN_CLOUD_HERMES_ROOT": str(HERMES_ROOT)},
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        print(
            "CRON_CONTROL_FAST_PATH_RELIABILITY: SKIP (materialization failed): "
            + (result.stderr or result.stdout or "").strip()
        )
        return False
    return True


def _install_fake_cron_jobs(job: dict) -> None:
    """Inject a fake ``cron.jobs`` package so ``resolve_career_job`` resolves
    deterministically without touching a real cron store."""
    cron_pkg = types.ModuleType("cron")
    cron_pkg.__path__ = []  # type: ignore[attr-defined]
    cron_jobs = types.ModuleType("cron.jobs")
    cron_jobs.get_job = lambda job_id: job if job_id == CANONICAL_JOB_ID else None
    cron_jobs.list_jobs = lambda include_disabled=False: [job]
    sys.modules["cron"] = cron_pkg
    sys.modules["cron.jobs"] = cron_jobs


def _extract_regex(src: str, name: str) -> str:
    m = re.search(rf"{name} = re\.compile\(\n(.*?)\n\)\n", src, re.DOTALL)
    assert m, f"regex {name} not found in gateway/run.py"
    return m.group(1)


def main() -> None:
    if not (HERMES_ROOT / ".git").is_dir():
        print("CRON_CONTROL_FAST_PATH_RELIABILITY: SKIP (Hermes Git source unavailable)")
        return

    with tempfile.TemporaryDirectory(prefix="opencloud-cron-control-fast-path-") as tmp:
        tree = Path(tmp) / "hermes"
        if not materialize(tree):
            return

        # ── 0. patch surface ────────────────────────────────────────────────
        patch_text = PATCH.read_text()
        patched_files = {
            line[len("diff --git "):].split(" b/", 1)[0].strip()
            for line in patch_text.splitlines()
            if line.startswith("diff --git ")
        }
        assert patched_files == {
            "a/gateway/cron_control_fast_path.py",
            "a/gateway/run.py",
        }, f"unexpected P13 patch surface: {patched_files}"

        sys.path.insert(0, str(tree))
        try:
            import gateway.cron_control_fast_path as fastpath
            import tools.cronjob_tools as cronjob_tools
        finally:
            sys.path.pop(0)

        job = _career_job()
        _install_fake_cron_jobs(job)

        # ── cases 1-5, 8-10: control intents classify deterministically ─────
        expected = {
            "run my Daily Career Job Match Report now": "run",
            "run my career report now": "run",
            "run the job search now": "run",
            "pause my career report": "pause",
            "resume my career report": "resume",
            "when does my career report run?": "status",
            "is my career report enabled?": "status",
        }
        for text, want in expected.items():
            got = fastpath.detect_career_control_intent(text)
            assert got == want, f"{text!r}: expected {want!r}, got {got!r}"

        # ── case 11: unrelated messages fall through ────────────────────────
        for text in (
            "how was your day?",
            "what's the weather in Toronto?",
            "how do I run my career report?",  # a question, not a command
            "I found a great job posting, can you look at it?",
        ):
            assert fastpath.detect_career_control_intent(text) is None, text

        # ── canonical resolution: id preferred, schema fallback ─────────────
        assert fastpath.CAREER_CONTROL_CANONICAL_JOB_ID == CANONICAL_JOB_ID
        assert fastpath.CAREER_OUTPUT_SCHEMA == CAREER_OUTPUT_SCHEMA
        resolved = fastpath.resolve_career_job()
        assert resolved is not None
        assert resolved["id"] == CANONICAL_JOB_ID

        fallback = dict(job, id="some-rotated-id")
        sys.modules["cron.jobs"].get_job = lambda job_id: None  # type: ignore[attr-defined]
        sys.modules["cron.jobs"].list_jobs = lambda include_disabled=False: [fallback]  # type: ignore[attr-defined]
        assert fastpath.resolve_career_job()["id"] == "some-rotated-id"
        sys.modules["cron.jobs"].get_job = lambda job_id: job if job_id == CANONICAL_JOB_ID else None  # type: ignore[attr-defined]
        sys.modules["cron.jobs"].list_jobs = lambda include_disabled=False: [job]  # type: ignore[attr-defined]

        # ── cases 6-7: exactly one trigger, schedule unchanged ──────────────
        calls = []
        orig_cronjob = cronjob_tools.cronjob
        cronjob_tools.cronjob = lambda **kw: calls.append(kw) or '{"success": true}'
        try:
            fastpath._run_career_job_now(job["id"])
        finally:
            cronjob_tools.cronjob = orig_cronjob
        assert calls == [{"action": "run", "job_id": CANONICAL_JOB_ID}], calls
        assert len(calls) == 1, "run-now must trigger exactly once"
        assert job["schedule"]["expr"] == RECURRING_SCHEDULE, "schedule must be unchanged"

        # ── cases 1, 8-10: fast path is wired BEFORE the agent/LLM path ─────
        gateway_src = (tree / "gateway/run.py").read_text()
        assert "HERMES_CRON_CONTROL_FAST_PATH_V1" in gateway_src
        assert "_maybe_handle_cron_control_fast_path" in gateway_src
        assert "_bind_cron_control_session_env" in gateway_src
        # The call must precede _handle_message_with_agent in the handler and
        # return immediately (never falling through to the conversational LLM).
        fast_call = gateway_src.index("_maybe_handle_cron_control_fast_path(")
        agent_call = gateway_src.index("_handle_message_with_agent(")
        assert fast_call < agent_call, "fast path must run before the LLM/compression path"
        assert "if _cron_control is not None:" in gateway_src
        assert "return _cron_control" in gateway_src

        # ── case 15: early return cleans session context (try/finally) ──────
        method_src = gateway_src[
            gateway_src.index("async def _maybe_handle_cron_control_fast_path"):
        ]
        assert "finally:" in method_src, "fast path must unwind through finally"
        assert "clear_session_vars" in method_src, "fast path must clear session vars"

        # ── case 12: compression diagnostics suppressed at the status layer ──
        # The gateway suppresses a status message when EITHER the pre-existing
        # noisy-status filter (which already owns the token-threshold overflow
        # phrasing) OR the P13 diagnostic filter matches. Assert the union so
        # the test mirrors _prepare_gateway_status_message exactly.
        ns = {"re": re}
        for rx_name in (
            "_TELEGRAM_NOISY_STATUS_RE",
            "_GATEWAY_COMPRESSION_DIAGNOSTIC_STATUS_RE",
        ):
            exec(
                f"{rx_name} = re.compile(\n" + _extract_regex(gateway_src, rx_name) + "\n)",
                ns,
            )
        noisy_rx = ns["_TELEGRAM_NOISY_STATUS_RE"]
        diag_rx = ns["_GATEWAY_COMPRESSION_DIAGNOSTIC_STATUS_RE"]

        def _suppressed(text: str) -> bool:
            return bool(noisy_rx.search(text) or diag_rx.search(text))

        diagnostics = [
            "context compression in progress",
            "compression timed out after 120.0s with no output",
            "context too large (~148000 tokens) — compressing",
            "compression cooldown active for 280s",
            "compression backoff active",
            "iteration 12/20 — still working",
        ]
        for text in diagnostics:
            assert _suppressed(text), f"must suppress compression diagnostic: {text!r}"

        # The P13 regex itself must own the cooldown/timeout/iteration phrases
        # the pre-existing filter did not already cover.
        for text in (
            "context compression in progress",
            "compression timed out after 120.0s with no output",
            "compression cooldown active for 280s",
            "compression backoff active",
            "iteration 12/20 — still working",
        ):
            assert diag_rx.search(text), f"P13 regex must match: {text!r}"

        prose = [
            "Here is your report: 5 DevOps internships found.",
            "Your career report will run at 9:30.",
            "I compressed my notes into a summary for you.",
        ]
        for text in prose:
            assert not _suppressed(text), f"must not match normal prose: {text!r}"

        # ── case B: bounded-compression failsafe marker present ──────────────
        assert "HERMES_COMPRESSION_FAILSAFE_V1" in gateway_src
        assert "_hyg_total_ceiling_seconds" in gateway_src
        assert "_hyg_timeout_seconds" in gateway_src

        # ── P12 guard still wired (fast path preserves run-now-once) ────────
        cron_src = (tree / "tools/cronjob_tools.py").read_text()
        assert "HERMES_CRON_RUN_NOW_ONCE_V1" in cron_src

    print("PASS run-now career command classifies as control 'run'")
    print("PASS pause/resume/status career commands classify deterministically")
    print("PASS unrelated messages fall through to the ordinary path")
    print("PASS canonical job a6cc8dd39f62 resolves; output_schema fallback works")
    print("PASS run-now triggers exactly once and leaves the schedule 30 9 * * * unchanged")
    print("PASS fast path is wired before the agent/LLM and compression path")
    print("PASS early fast-path return unwinds through finally and clears session context")
    print("PASS compression diagnostics match the suppression regex; prose does not")
    print("PASS bounded-compression failsafe marker and ceilings are present")
    print("PASS P12 run-now-once guard is preserved")
    print("CRON_CONTROL_FAST_PATH_RELIABILITY: PASS")


if __name__ == "__main__":
    main()
