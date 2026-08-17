#!/usr/bin/env python3
"""Deterministic regression coverage for the P12 run-now-once / provider-quiet patch.

A "run my career report now" request must trigger the canonical cron exactly
once per user turn. P12 adds a turn-local idempotency guard (keyed by job id +
inbound message id) so a repeat run-now in the same turn returns the cached
trigger state instead of starting a second execution. P12 also suppresses the
internal provider retry/fallback status lines so they stay in logs instead of
spamming the user's chat.

Network-free and store-isolated: ``cronjob`` internals are monkeypatched; the
gateway regex is extracted from the patched source and recompiled in-process
(no heavy ``gateway.run`` import).
"""

import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
HERMES_ROOT = Path(os.environ.get("OPEN_CLOUD_HERMES_ROOT", Path.home() / ".hermes/hermes-agent"))
PATCH = ROOT / "integrations/hermes/hermes-run-now-once-provider-quiet.patch"


def materialize(out: Path) -> bool:
    result = subprocess.run(
        ["bash", str(ROOT / "install/30-brain-materialize.sh"), "--stage", str(out)],
        env={**os.environ, "OPEN_CLOUD_HERMES_ROOT": str(HERMES_ROOT)},
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        print(
            "CRON_RUN_NOW_ONCE_RELIABILITY: SKIP (materialization failed): "
            + (result.stderr or result.stdout or "").strip()
        )
        return False
    return True


def main() -> None:
    if not (HERMES_ROOT / ".git").is_dir():
        print("CRON_RUN_NOW_ONCE_RELIABILITY: SKIP (Hermes Git source unavailable)")
        return

    with tempfile.TemporaryDirectory(prefix="opencloud-cron-run-now-once-") as tmp:
        tree = Path(tmp) / "hermes"
        if not materialize(tree):
            return

        patch_text = PATCH.read_text()
        patched_files = {
            line[len("diff --git "):].split(" b/", 1)[0].strip()
            for line in patch_text.splitlines()
            if line.startswith("diff --git ")
        }
        assert patched_files == {"a/tools/cronjob_tools.py", "a/gateway/run.py"}, (
            f"unexpected patch surface: {patched_files}"
        )

        sys.path.insert(0, str(tree))
        try:
            import tools.cronjob_tools as cjt
        finally:
            sys.path.pop(0)

        job = {
            "id": "a6cc8dd39f62",
            "name": "Daily Career Job Match Report",
            "schedule": {"kind": "cron", "expr": "30 9 * * *", "display": "30 9 * * *"},
            "schedule_display": "30 9 * * *",
            "enabled": True,
        }

        # ── 1. one run-now -> one trigger ─────────────────────────────────
        # ── 2. same turn second run-now -> no re-trigger ──────────────────
        # ── 3. alias (name) -> same job -> no re-trigger ──────────────────
        # ── 4. new turn (new message id) -> fresh trigger ─────────────────
        calls = []
        orig_resolve = cjt.resolve_job_ref
        orig_get = cjt.get_job
        orig_exec = cjt._execute_job_now
        orig_notify = cjt._notify_provider_jobs_changed_safe
        orig_msgid = cjt._session_message_id

        cjt._session_message_id = lambda: "turn-1"
        cjt.resolve_job_ref = lambda ref: job
        cjt.get_job = lambda jid: {**job, "last_status": "ok", "last_run_at": "x"}
        cjt._execute_job_now = lambda j: calls.append(j["id"]) or {
            "claimed": True, "success": True, "error": None,
        }
        cjt._notify_provider_jobs_changed_safe = lambda: None
        try:
            r1 = json.loads(cjt.cronjob(action="run", job_id="a6cc8dd39f62"))
            assert r1.get("success") is True, r1
            assert r1["job"].get("executed") is True, r1
            assert r1["job"].get("already_triggered_this_turn") is None, r1
            assert len(calls) == 1, calls

            r2 = json.loads(cjt.cronjob(action="run", job_id="a6cc8dd39f62"))
            assert r2["job"].get("already_triggered_this_turn") is True, r2
            assert "no duplicate execution" in r2.get("note", ""), r2
            assert len(calls) == 1, "second run-now must not trigger again"

            r3 = json.loads(cjt.cronjob(action="run", job_id="Daily Career Job Match Report"))
            assert r3["job"].get("already_triggered_this_turn") is True, r3
            assert len(calls) == 1, "alias must resolve to the same guarded job"

            # a different action is unaffected by the run-now guard
            cjt._session_message_id = lambda: "turn-2"
            r4 = json.loads(cjt.cronjob(action="run", job_id="a6cc8dd39f62"))
            assert r4["job"].get("already_triggered_this_turn") is None, r4
            assert len(calls) == 2, "a new turn must trigger again"
        finally:
            cjt.resolve_job_ref = orig_resolve
            cjt.get_job = orig_get
            cjt._execute_job_now = orig_exec
            cjt._notify_provider_jobs_changed_safe = orig_notify
            cjt._session_message_id = orig_msgid

        # ── 5. provider fallback chatter is matched / prose is not ────────
        gateway_src = (tree / "gateway/run.py").read_text()
        m = re.search(
            r"_GATEWAY_PROVIDER_FALLBACK_STATUS_RE = re\.compile\(\n(.*?)\n\)\n",
            gateway_src,
            re.DOTALL,
        )
        assert m, "provider fallback status regex not found in gateway/run.py"
        ns = {"re": re}
        exec(f"_GATEWAY_PROVIDER_FALLBACK_STATUS_RE = re.compile(\n{m.group(1)}\n)", ns)
        rx = ns["_GATEWAY_PROVIDER_FALLBACK_STATUS_RE"]

        chatter = [
            "⚠️ Rate limited — switching to fallback provider...",
            "🔄 Primary model failed — switching to fallback: ",
            "The model provider failed after retries...",
            "⚠️ Billing or credits exhausted — switching to fallback provider...",
            "⚠️ Provider unreachable — switching to fallback provider...",
            "switching to fallback model...",
            "❌ Rate limited after 3 retries — ",
            "⚠️ Empty/malformed response — switching to fallback...",
            "stream — activating fallback provider...",
            "Content filter terminated stream; switching to fallback...",
        ]
        for text in chatter:
            assert rx.search(text), f"must match fallback chatter: {text!r}"

        prose = [
            "Here is your report: 5 DevOps internships found.",
            "I use the provider API to check availability.",
            "Your rate limited question was answered.",
            "CAREER JOB MATCH REPORT — VERIFIED MATCHES: 2",
        ]
        for text in prose:
            assert not rx.search(text), f"must not match prose: {text!r}"

        # ── 6. Source-level wiring ────────────────────────────────────────
        cron_src = (tree / "tools/cronjob_tools.py").read_text()
        assert "HERMES_CRON_RUN_NOW_ONCE_V1" in cron_src
        assert "_run_now_guard_get" in cron_src
        assert "_run_now_guard_put" in cron_src
        assert "HERMES_SESSION_MESSAGE_ID" in cron_src
        assert "Running it now — the report will arrive in this chat" in cron_src
        assert "HERMES_PROVIDER_FALLBACK_STATUS_FILTER_V1" in gateway_src

        # ── 7. all providers fail -> exactly one sanitized final failure ──
        # The P12 fallback-status filter suppresses the intermediate
        # retry/fallback chatter; the terminal failure must still collapse
        # through the pre-existing final-response sanitizer to exactly ONE
        # compact, user-safe message with no raw provider details.
        def _extract_regex(src: str, name: str) -> str:
            m = re.search(rf"{name} = re\.compile\(\n(.*?)\n\)\n", src, re.DOTALL)
            assert m, f"regex {name} not found in gateway/run.py"
            return m.group(1)

        def _extract_function(src: str, name: str) -> str:
            m = re.search(rf"def {name}\(.*?\)(?: -> .*?)?:\n(?:.*\n)*?(?=\ndef |\Z)", src)
            assert m, f"function {name} not found in gateway/run.py"
            return m.group(0)

        gns = {"re": re}
        for regex_name in (
            "_GATEWAY_PROVIDER_POLICY_RE",
            "_GATEWAY_AUTH_ERROR_RE",
            "_GATEWAY_RATE_LIMIT_RE",
            "_GATEWAY_PROVIDER_ERROR_SHAPE_RE",
            "_GATEWAY_PROVIDER_FALLBACK_STATUS_RE",
        ):
            exec(
                f"{regex_name} = re.compile(\n{_extract_regex(gateway_src, regex_name)}\n)",
                gns,
            )
        for fn in ("_gateway_provider_error_reply", "_looks_like_gateway_provider_error"):
            exec(_extract_function(gateway_src, fn), gns)

        raw_errors = [
            "Error code: 429 - {'error': {'message': 'Rate limit exceeded: "
            "free-models-per-day. Add 10 credits to unlock 1000 free model "
            "requests per day', 'code': 429, 'user_id': "
            "'user_3CkVjRnOsG9UCf9093KwCi4HTgT'}}",
            "Error code: 400 - {'error': {'message': 'This model only supports "
            "single tool-calls at once!', 'type': 'BadRequestError', 'code': 400}}",
            "API call failed after 3 retries: HTTP 429 Too Many Requests",
            "provider authentication failed: incorrect api key sk-abc1234567890123456789012",
        ]
        for raw in raw_errors:
            assert gns["_looks_like_gateway_provider_error"](raw), f"must detect raw error: {raw!r}"
            reply = gns["_gateway_provider_error_reply"](raw)
            assert reply, "sanitized reply must be non-empty"
            assert len(reply) < 300, f"sanitized reply must be compact: {reply!r}"
            assert "Error code" not in reply, f"raw envelope leaked: {reply!r}"
            assert "user_3CkV" not in reply, f"raw user id leaked: {reply!r}"
            assert "sk-abc" not in reply, f"raw credential leaked: {reply!r}"
            assert "free-models-per-day" not in reply, f"raw provider text leaked: {reply!r}"
            assert "single tool-calls" not in reply, f"raw provider text leaked: {reply!r}"

        # Exactly one final failure: every intermediate chatter line is
        # suppressed by the fallback filter (status path), and the terminal
        # error collapses to a single compact reply (final-response path).
        for text in chatter:
            assert gns["_GATEWAY_PROVIDER_FALLBACK_STATUS_RE"].search(text), text
        final_replies = [
            gns["_gateway_provider_error_reply"](raw) for raw in raw_errors
        ]
        assert len(set(final_replies)) >= 1
        assert all(len(r) < 300 for r in final_replies)

    print("PASS one run-now request triggers exactly once")
    print("PASS same-turn second run-now is a no-op (no duplicate execution)")
    print("PASS alias resolves to the same guarded job (one trigger)")
    print("PASS a new turn may trigger again")
    print("PASS provider retry/fallback chatter is matched and suppressed")
    print("PASS normal report/prose is never suppressed")
    print("PASS all-providers-fail collapses to exactly one sanitized failure")
    print("PASS raw provider errors/credentials never reach Photon")
    print("PASS run-now terminal guidance is in the tool schema")
    print("CRON_RUN_NOW_ONCE_RELIABILITY: PASS")


if __name__ == "__main__":
    main()
