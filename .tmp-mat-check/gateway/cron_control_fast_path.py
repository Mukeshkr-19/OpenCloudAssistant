"""Deterministic cron-control fast path for the messaging gateway.

# HERMES_CRON_CONTROL_FAST_PATH_V1

A small, self-contained intent classifier + resolver that lets the gateway
handle high-confidence cron-control messages ("run my career report now",
"pause my career report", ...) *before* it loads a long session transcript,
runs context compression, or invokes the conversational LLM.

Why this exists
---------------
A long-lived chat session can accumulate enough history that every inbound
message rehydrates an oversized transcript and triggers pre-agent compression.
For a control message whose entire job is to trigger/pause/resume/inspect an
existing cron job, that compression round-trip (and the conversational model
that would otherwise run) is pure waste — and in the failure case it strands
the turn behind a compression timeout/cooldown while the user waits for a
one-word acknowledgement.

This module is deliberately tiny and pure: intent detection and job resolution
are plain functions with no gateway state, so the reliability suite can
exercise them deterministically. The gateway only calls ``dispatch`` and owns
the session-context bind/clear invariants.

Deterministic resolution contract
---------------------------------
The "career workflow" is identified by two independent signals, either of
which resolves to the same job:

  * the canonical job id ``CAREER_CONTROL_CANONICAL_JOB_ID`` (the existing
    Daily Career Job Match Report job), and
  * ``output_schema == "career_job_match_v1"`` (the opt-in output contract).

``resolve_career_job`` prefers the canonical id and falls back to the schema
scan, so the fast path still works if the canonical id is ever rotated or the
job is re-created under a different id.
"""

from __future__ import annotations

import re
import threading
from typing import Any, Dict, Optional

# Canonical "Daily Career Job Match Report" job. Prefer this exact id, then
# fall back to the output_schema scan in ``resolve_career_job``.
CAREER_CONTROL_CANONICAL_JOB_ID = "a6cc8dd39f62"
CAREER_OUTPUT_SCHEMA = "career_job_match_v1"

# Conservative career-workflow keywords. Kept disjoint from the ordinary
# assistant vocabulary so an unrelated message (e.g. "how was your day?")
# never trips the fast path. Mirrors _CAREER_SCOUT_KEYWORDS in
# tools/cronjob_tools.py, but only the phrases a user would say to control
# their report.
_CAREER_CONTROL_KEYWORDS = (
    "career",
    "job match",
    "job search",
    "job-match",
    "job-scout",
    "job scouting",
)

# Explicit "run it now" verbs. We require BOTH a run verb AND "now" so a
# question like "how do I run my career report?" never triggers a run.
_RUN_VERB_RE = re.compile(r"\b(?:run|trigger|start|fire)\b", re.IGNORECASE)
_NOW_RE = re.compile(r"\bnow\b", re.IGNORECASE)
_PAUSE_RE = re.compile(r"\bpause\b", re.IGNORECASE)
_RESUME_RE = re.compile(r"\bresume\b|re-?enable\b|turn\s+back\s+on", re.IGNORECASE)
_STATUS_RE = re.compile(
    r"\b(?:status|enabled|when|schedule|next\s+run|runs?\s+at|running)\b",
    re.IGNORECASE,
)


def detect_career_control_intent(text: Optional[str]) -> Optional[str]:
    """Return the cron-control action for ``text``, or ``None`` if it is not a
    high-confidence career-report control message.

    Return values: ``"run"``, ``"pause"``, ``"resume"``, ``"status"``, or
    ``None`` (fall through to the ordinary conversational path).

    Ordering matters: run-now is checked first (it requires the strongest
    signal — a run verb *and* "now"), then pause/resume, then status. A
    message that merely mentions a career report without a control verb (or a
    question) still returns ``None`` so the conversational model handles it.
    """
    if not text or not isinstance(text, str):
        return None
    lowered = text.strip().lower()
    if not lowered:
        return None
    # Slash commands are handled by the normal command dispatch, never here.
    if lowered.startswith("/"):
        return None
    if not any(kw in lowered for kw in _CAREER_CONTROL_KEYWORDS):
        return None

    if _RUN_VERB_RE.search(lowered) and _NOW_RE.search(lowered):
        return "run"
    if _PAUSE_RE.search(lowered):
        return "pause"
    if _RESUME_RE.search(lowered):
        return "resume"
    if _STATUS_RE.search(lowered):
        return "status"
    return None


def resolve_career_job() -> Optional[Dict[str, Any]]:
    """Resolve the canonical career job-match job, or ``None`` when absent.

    Prefers the canonical id; falls back to the first active-or-disabled job
    whose ``output_schema`` is ``career_job_match_v1``. Pure lookup — no
    gateway state, no LLM, no compression.
    """
    try:
        from cron.jobs import get_job, list_jobs
    except Exception:
        return None

    job = None
    try:
        job = get_job(CAREER_CONTROL_CANONICAL_JOB_ID)
    except Exception:
        job = None
    if job is not None:
        return job

    try:
        for candidate in list_jobs(include_disabled=True):
            if (candidate.get("output_schema") or "") == CAREER_OUTPUT_SCHEMA:
                return candidate
    except Exception:
        pass
    return None


def _run_career_job_now(job_id: str) -> None:
    """Synchronous run-now body — executes on a background thread so the
    gateway can acknowledge immediately and end the turn.

    Delegates to the existing ``cronjob`` tool so every P12 guarantee is
    preserved: the turn-local run-now-once guard (HERMES_CRON_RUN_NOW_ONCE_V1),
    the at-most-once claim CAS inside ``_execute_job_now``, the recurring
    schedule untouched, and cron-owned generation/delivery. Provider retries
    happen *inside* ``run_one_job`` and never re-enter this trigger.
    """
    from tools.cronjob_tools import cronjob

    cronjob(action="run", job_id=job_id)


def spawn_run_now(job_id: str, *, _context: Any = None) -> None:
    """Fire run-now on a daemon thread and return immediately.

    ``_context`` is an optional ``contextvars.Context`` captured by the caller
    after binding session vars, so the run-now-once guard sees the current
    turn's message id even though the work runs on another thread.
    """
    import logging

    def _runner() -> None:
        try:
            if _context is not None:
                _context.run(_run_career_job_now, job_id)
            else:
                _run_career_job_now(job_id)
        except Exception:
            # Never let a cron-control trigger blow up the gateway handler
            # thread. The cron delivery path owns user-visible failures.
            logging.getLogger(__name__).exception(
                "cron-control run-now background trigger failed"
            )

    threading.Thread(
        target=_runner,
        daemon=True,
        name="cron-control-run-now",
    ).start()


# ─────────────────────────────────────────────────────────────────────────
# User-facing acknowledgements (sanitized — no provider/compression internals)
# ─────────────────────────────────────────────────────────────────────────

RUN_ACK = "Running it now — the report will arrive in this chat."
NOT_FOUND_MSG = (
    "I couldn't find your Daily Career Job Match Report job. "
    "It may not be set up yet."
)
FAILURE_MSG = (
    "Something went wrong with your career report request. "
    "Please try again."
)


def pause_ack(job: Optional[Dict[str, Any]]) -> str:
    name = (job or {}).get("name") or "career report"
    return f"Paused your {name}. It won't run again until you resume it."


def resume_ack(job: Optional[Dict[str, Any]]) -> str:
    name = (job or {}).get("name") or "career report"
    return f"Resumed your {name}."


def status_ack(job: Dict[str, Any]) -> str:
    name = job.get("name") or "career report"
    enabled = bool(job.get("enabled", True)) and (job.get("state") != "paused")
    schedule = job.get("schedule_display") or job.get("schedule", {}).get("display") or "?"
    if not enabled:
        return f"Your {name} is paused."
    return f"Your {name} is enabled. Schedule: {schedule}."
