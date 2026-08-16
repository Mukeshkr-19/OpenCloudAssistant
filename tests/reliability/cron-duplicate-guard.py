#!/usr/bin/env python3
"""Deterministic regression coverage for the cron duplicate-guard patch (P9).

A natural-language cron request ("every day at 9:30 AM ...") can silently
duplicate an existing recurring job at the same schedule and — worse — lose
the existing job's OpenCloud configuration (required_to_execute,
output_schema, routing_profile). P9 makes the cronjob tool reject a create at
a schedule that already has an active recurring job and point the model at
``action='update'`` instead.

Network-free and store-isolated: ``list_jobs`` is monkeypatched with synthetic
jobs, so no real cron store is read or written.

``croniter`` is a hard dependency of the production cron scheduler but may be
absent from a bare test Python. The interval-based dedup and the create
rejection path run unconditionally; the cron-expression cases run when
``croniter`` is importable and are skipped otherwise.
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
PATCH = ROOT / "integrations/hermes/hermes-cron-duplicate-guard.patch"


def materialize(out: Path) -> bool:
    result = subprocess.run(
        ["bash", str(ROOT / "install/30-brain-materialize.sh"), "--stage", str(out)],
        env={**os.environ, "OPEN_CLOUD_HERMES_ROOT": str(HERMES_ROOT)},
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        print(
            "CRON_DUPLICATE_GUARD_RELIABILITY: SKIP (materialization failed): "
            + (result.stderr or result.stdout or "").strip()
        )
        return False
    return True


def _job(jid, name, schedule, enabled=True, output_schema=None, prompt=None):
    job = {
        "id": jid,
        "name": name,
        "schedule": schedule,
        "schedule_display": schedule.get("display", schedule.get("expr", "")),
        "enabled": enabled,
    }
    if output_schema:
        job["output_schema"] = output_schema
    if prompt:
        job["prompt"] = prompt
    return job


def main() -> None:
    if not (HERMES_ROOT / ".git").is_dir():
        print("CRON_DUPLICATE_GUARD_RELIABILITY: SKIP (Hermes Git source unavailable)")
        return

    with tempfile.TemporaryDirectory(prefix="opencloud-cron-duplicate-guard-") as tmp:
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
            "a/tools/cronjob_tools.py",
        }, f"unexpected patch surface: {patched_files}"

        sys.path.insert(0, str(tree))
        try:
            import tools.cronjob_tools as cjt
        finally:
            sys.path.pop(0)

        # croniter gates the cron-expression path of parse_schedule; the
        # interval + create-rejection paths below do not need it.
        try:
            cjt.parse_schedule("30 9 * * *")
            HAS_CRON_EXPR = True
        except Exception:
            HAS_CRON_EXPR = False

        orig_list = cjt.list_jobs

        # ── 1. Same recurring cron schedule is detected (needs croniter) ──
        if HAS_CRON_EXPR:
            cron_930 = {"kind": "cron", "expr": "30 9 * * *", "display": "30 9 * * *"}
            cron_900 = {"kind": "cron", "expr": "0 9 * * *", "display": "0 9 * * *"}
            existing = [
                _job("a6cc8dd39f62", "Daily Career Job Match Report", cron_930),
            ]
            cjt.list_jobs = lambda include_disabled=False: existing
            try:
                dup = cjt._existing_recurring_job_at_schedule("30 9 * * *")
                assert dup is not None and dup["id"] == "a6cc8dd39f62", dup
                # different time -> no duplicate.
                assert cjt._existing_recurring_job_at_schedule("0 9 * * *") is None
                # one-shot schedule -> never a duplicate.
                assert cjt._existing_recurring_job_at_schedule("2026-09-01T09:00:00") is None
                # disabled job -> not a duplicate.
                cjt.list_jobs = lambda include_disabled=False: [
                    _job("x", "paused career", cron_900, enabled=False),
                ]
                assert cjt._existing_recurring_job_at_schedule("0 9 * * *") is None
            finally:
                cjt.list_jobs = orig_list
        else:
            print("CRON_DUPLICATE_GUARD: note — croniter unavailable; cron-expression dedup not exercised")

        # ── 2. Recurring interval collision (no croniter needed) ──────────
        interval = {"kind": "interval", "minutes": 30, "display": "every 30m"}
        cjt.list_jobs = lambda include_disabled=False: [
            _job("y", "scout", interval),
        ]
        try:
            dup = cjt._existing_recurring_job_at_schedule("every 30m")
            assert dup is not None and dup["id"] == "y", dup
            assert cjt._existing_recurring_job_at_schedule("every 45m") is None
        finally:
            cjt.list_jobs = orig_list

        # ── 3. create is rejected deterministically for a duplicate ───────
        #     (P10: workflow-identity — a career-scouting request resolves to
        #      the existing career_job_match_v1 job, whatever the schedule.)
        created = []
        orig_create = cjt.create_job
        cjt.list_jobs = lambda include_disabled=False: [
            _job(
                "y",
                "scout",
                interval,
                output_schema="career_job_match_v1",
                prompt="find current DevOps and cloud internships",
            ),
        ]
        cjt.create_job = lambda **kw: created.append(kw) or {"id": "should-not-happen"}
        try:
            result = cjt.cronjob(
                action="create",
                schedule="every 30m",
                prompt="find internships",
            )
            parsed = json.loads(result)
            assert parsed.get("success") is False, parsed
            assert "matches an existing cron job" in parsed.get("error", ""), parsed
            assert "action='update'" in parsed.get("error", ""), parsed
            assert "y" in parsed.get("error", ""), parsed
            assert created == [], "create_job must not be called for a duplicate"
        finally:
            cjt.list_jobs = orig_list
            cjt.create_job = orig_create

        # ── 4. Source-level wiring ────────────────────────────────────────
        source = (tree / "tools/cronjob_tools.py").read_text()
        assert "HERMES_CRON_DUPLICATE_GUARD_V1" in source
        assert "_existing_recurring_job_at_schedule" in source
        assert "always action='list'" in source
        assert "update that job instead of creating a duplicate" in source

    print("PASS recurring interval collision is detected")
    print("PASS create is rejected deterministically with update guidance")
    print("PASS create_job is never called for a duplicate")
    print("PASS list-before-create guidance is in the tool schema")
    if HAS_CRON_EXPR:
        print("PASS same recurring cron schedule is detected as a duplicate")
        print("PASS different schedule / one-shot / disabled are not duplicates")
    print("CRON_DUPLICATE_GUARD_RELIABILITY: PASS")


if __name__ == "__main__":
    main()
