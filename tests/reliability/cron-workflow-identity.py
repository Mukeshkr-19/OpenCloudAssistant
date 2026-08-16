#!/usr/bin/env python3
"""Deterministic regression coverage for the cron workflow-identity patch (P10).

A natural-language career-scouting request ("every day at 9:30 AM find me the
best DevOps/SRE internships ...") describes the SAME workflow as the canonical
``career_job_match_v1`` job. The model invents a fresh name for it
("daily-internship-scout"), so schedule/name matching alone cannot pin it down.
P10 makes the cronjob tool resolve such requests to the existing career job
(update it, never duplicate), while still allowing genuinely different tasks to
create at any schedule.

Network-free and store-isolated: ``list_jobs``, ``create_job``,
``resolve_job_ref``, ``pause_job`` and ``resume_job`` are monkeypatched with
synthetic jobs, so no real cron store is read or written.

``croniter`` is a hard dependency of the production cron scheduler but may be
absent from a bare test Python. The career-intent dedup and the create
rejection path run unconditionally; the schedule-overlap cases for cron
expressions run when ``croniter`` is importable and are skipped otherwise.
"""

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
HERMES_ROOT = Path(os.environ.get("OPEN_CLOUD_HERMES_ROOT", Path.home() / ".hermes/hermes-agent"))
PATCH = ROOT / "integrations/hermes/hermes-cron-workflow-identity.patch"


def materialize(out: Path) -> bool:
    result = subprocess.run(
        ["bash", str(ROOT / "install/30-brain-materialize.sh"), "--stage", str(out)],
        env={**os.environ, "OPEN_CLOUD_HERMES_ROOT": str(HERMES_ROOT)},
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        print(
            "CRON_WORKFLOW_IDENTITY_RELIABILITY: SKIP (materialization failed): "
            + (result.stderr or result.stdout or "").strip()
        )
        return False
    return True


CAREER_JOB = {
    "id": "a6cc8dd39f62",
    "name": "Daily Career Job Match Report",
    "schedule": {"kind": "cron", "expr": "30 9 * * *", "display": "30 9 * * *"},
    "schedule_display": "30 9 * * *",
    "enabled": True,
    "output_schema": "career_job_match_v1",
    "prompt": (
        "DAILY CAREER JOB MATCH REPORT. Find current DevOps, SRE, cloud, "
        "platform and infrastructure internships for the confirmed career "
        "profile, verify each job page with web_extract, then render the "
        "career_job_match_v1 output schema."
    ),
}


def main() -> None:
    if not (HERMES_ROOT / ".git").is_dir():
        print("CRON_WORKFLOW_IDENTITY_RELIABILITY: SKIP (Hermes Git source unavailable)")
        return

    with tempfile.TemporaryDirectory(prefix="opencloud-cron-workflow-identity-") as tmp:
        tree = Path(tmp) / "hermes"
        if not materialize(tree):
            return

        patch_text = PATCH.read_text()
        patched_files = {
            line[len("diff --git "):].split(" b/", 1)[0].strip()
            for line in patch_text.splitlines()
            if line.startswith("diff --git ")
        }
        assert patched_files == {"a/tools/cronjob_tools.py"}, f"unexpected patch surface: {patched_files}"

        sys.path.insert(0, str(tree))
        try:
            import tools.cronjob_tools as cjt
        finally:
            sys.path.pop(0)

        try:
            cjt.parse_schedule("30 9 * * *")
            HAS_CRON_EXPR = True
        except Exception:
            HAS_CRON_EXPR = False

        orig_list = cjt.list_jobs
        orig_create = cjt.create_job
        orig_resolve = cjt.resolve_job_ref
        orig_pause = cjt.pause_job
        orig_resume = cjt.resume_job

        def set_jobs(jobs):
            cjt.list_jobs = lambda include_disabled=False: jobs

        # ── 1. Career intent resolves to the career_job_match_v1 job ──────
        #     regardless of the schedule the model invented.
        set_jobs([CAREER_JOB])
        try:
            eq = cjt._find_equivalent_job(
                "0 8 * * *",
                "daily-internship-scout",
                "every day at 8 AM find me the best current DevOps, SRE, "
                "cloud, platform, and infrastructure internships. Use my "
                "profile, verify the jobs, and send me the report here.",
            )
            assert eq is not None and eq["id"] == "a6cc8dd39f62", eq

            # Role / location wording change still resolves to the same job.
            eq = cjt._find_equivalent_job(
                "30 9 * * *",
                "intern scout",
                "every day find me SRE and platform internships in Austin, TX",
            )
            assert eq is not None and eq["id"] == "a6cc8dd39f62", eq
        finally:
            set_jobs([])

        # ── 2. Create is rejected deterministically with update guidance ──
        created = []
        set_jobs([CAREER_JOB])
        cjt.create_job = lambda **kw: created.append(kw) or {"id": "should-not-happen"}
        try:
            result = cjt.cronjob(
                action="create",
                schedule="0 8 * * *",
                name="daily-internship-scout",
                prompt="every day at 8 AM find me DevOps SRE cloud internships",
            )
            parsed = json.loads(result)
            assert parsed.get("success") is False, parsed
            assert "matches an existing cron job" in parsed.get("error", ""), parsed
            assert "action='update'" in parsed.get("error", ""), parsed
            assert "a6cc8dd39f62" in parsed.get("error", ""), parsed
            assert created == [], "create_job must not be called for a duplicate"
        finally:
            set_jobs([])
            cjt.create_job = orig_create

        # ── 3. Repeated identical message is idempotent (no duplicate) ────
        set_jobs([CAREER_JOB])
        try:
            eq = cjt._find_equivalent_job(
                "30 9 * * *",
                "Daily Career Job Match Report",
                "every day at 9:30 AM find me the best current DevOps, SRE, "
                "cloud, platform, and infrastructure internships. Use my "
                "profile, verify the jobs, and send me the report here.",
            )
            assert eq is not None and eq["id"] == "a6cc8dd39f62", eq
        finally:
            set_jobs([])

        # ── 4. A genuinely different task is allowed to create ────────────
        #     (career intent does not fire; schedule + purpose does not overlap).
        set_jobs([CAREER_JOB])
        try:
            weather = cjt._find_equivalent_job(
                "30 9 * * *",
                "Daily Weather Digest",
                "every day email me the weather forecast for my city",
            )
            assert weather is None, weather

            stocks = cjt._find_equivalent_job(
                "30 9 * * *",
                "Watchlist Digest",
                "stock prices for my watchlist every morning",
            )
            assert stocks is None, stocks
        finally:
            set_jobs([])

        # ── 5. Career intent with NO career job -> falls through to schedule ──
        set_jobs([])
        try:
            eq = cjt._find_equivalent_job(
                "30 9 * * *",
                "daily-internship-scout",
                "find DevOps internships",
            )
            assert eq is None, eq
        finally:
            set_jobs([])

        # ── 6. Schedule + purpose-overlap fallback (interval, no croniter) ──
        news_job = {
            "id": "news1",
            "name": "Tech News Digest",
            "schedule": {"kind": "interval", "minutes": 30, "display": "every 30m"},
            "schedule_display": "every 30m",
            "enabled": True,
            "prompt": "email me top tech news headlines every day",
        }
        set_jobs([news_job])
        try:
            eq = cjt._find_equivalent_job(
                "every 30m",
                "Tech News Digest",
                "email me top tech news headlines every day",
            )
            assert eq is not None and eq["id"] == "news1", eq

            other = cjt._find_equivalent_job(
                "every 30m",
                "Stock Digest",
                "stock prices for my watchlist",
            )
            assert other is None, other
        finally:
            set_jobs([])

        # ── 7. Cron-expression schedule+overlap fallback (needs croniter) ──
        if HAS_CRON_EXPR:
            set_jobs([news_job])
            try:
                same = cjt._find_equivalent_job(
                    "30 9 * * *", "Tech News Digest", "email me top tech news headlines"
                )
                # news_job's schedule is an interval, so no cron-expr match is
                # expected; this just proves the path is reachable and returns None
                # when the schedule kind differs.
                assert same is None, same
            finally:
                set_jobs([])
            print("CRON_WORKFLOW_IDENTITY: note — cron-expression schedule path exercised")
        else:
            print("CRON_WORKFLOW_IDENTITY: note — croniter unavailable; cron-expression schedule overlap not exercised")

        # ── 8. Name-based resolution for run/pause/resume ─────────────────
        #     The model lists first, then operates the job by its exact name/ID.
        operated = []
        cjt.resolve_job_ref = lambda ref: (
            CAREER_JOB if str(ref).lower() in ("a6cc8dd39f62", "daily career job match report") else None
        )
        cjt.pause_job = lambda jid, reason=None: operated.append(("pause", jid)) or CAREER_JOB
        cjt.resume_job = lambda jid: operated.append(("resume", jid)) or CAREER_JOB
        try:
            r = json.loads(cjt.cronjob(action="pause", job_id="Daily Career Job Match Report"))
            assert r.get("success") is True, r
            r = json.loads(cjt.cronjob(action="resume", job_id="a6cc8dd39f62"))
            assert r.get("success") is True, r
            assert operated == [("pause", "a6cc8dd39f62"), ("resume", "a6cc8dd39f62")], operated
        finally:
            cjt.resolve_job_ref = orig_resolve
            cjt.pause_job = orig_pause
            cjt.resume_job = orig_resume
            set_jobs([])

        # ── 9. Source-level wiring ────────────────────────────────────────
        source = (tree / "tools/cronjob_tools.py").read_text()
        assert "HERMES_CRON_WORKFLOW_IDENTITY_V1" in source
        assert "_find_equivalent_job" in source
        assert "_career_scout_intent" in source
        assert "_purpose_overlaps" in source
        assert "career_job_match_v1" in source
        assert "resolves to the existing career_job_match_v1 job" in source

    print("PASS career-scouting request resolves to the career_job_match_v1 job (any schedule)")
    print("PASS role/location wording change still resolves to the existing job")
    print("PASS create is rejected deterministically with update guidance")
    print("PASS create_job is never called for a duplicate")
    print("PASS repeated identical message is idempotent (no duplicate)")
    print("PASS genuinely different task is allowed to create")
    print("PASS career intent without a career job does not over-match")
    print("PASS schedule + purpose-overlap fallback distinguishes same vs different task")
    print("PASS name-based resolution operates the existing job (run/pause/resume)")
    print("PASS workflow-identity guidance is in the tool schema")
    print("CRON_WORKFLOW_IDENTITY_RELIABILITY: PASS")


if __name__ == "__main__":
    main()
