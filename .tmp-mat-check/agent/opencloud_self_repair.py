"""Deterministic self-repair auto-trigger for OpenCloud-internal regressions.

When the runtime hits a deterministic OpenCloud code regression (e.g. the
``_opencloud_routing_profile`` provider-kwargs leak), this module classifies it,
creates a sanitized incident, and drives the existing ``hermes-code-repair``
harness through the bounded flow:

    classify -> sanitized incident -> repair (staged + validated + deploy)
    -> write pending-replay marker -> restart -> (on startup) replay once

Safeguards, all enforced without model judgment:

  * no recursion — the replayed request cannot itself trigger another repair;
  * no concurrent repair — a host lock rejects a second transaction;
  * one replay maximum — the pending-replay marker is consumed once;
  * per-error-fingerprint cooldown — repeated fingerprints are skipped;
  * rollback — the repair harness rolls back on validation/health failure.

External/operational failures (quota, outage, rate limit, DNS/timeout, invalid
credentials, 404, ordinary tool failures) are NEVER classified as repairable,
so this never edits source for a provider problem.

The orchestrator's side-effecting steps (invoke_repair, restart, health_check,
replay) are injectable so the full flow is exercisable with deterministic test
doubles — no live source is modified by the reliability suite.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

# HERMES_OPENCLOUD_SELF_REPAIR_V1

# A repair attempt is skipped for the same fingerprint for this many seconds.
DEFAULT_COOLDOWN_SECONDS = 1800

# systemd's hermes-gateway.service declares RestartForceExitStatus=75, so this
# exit code is the supported "restart now" signal that avoids an unclean crash.
_RESTART_EXIT_CODE = 75

_REPAIRABLE_OPENCLOUD_MARKERS = (
    ("opencloud_metadata_leak", "_opencloud_"),
    ("opencloud_import_error", "no module named 'agent.opencloud"),
    ("opencloud_import_error", "no module named 'opencloud"),
)

# External / operational failures that must never trigger a source repair.
_EXTERNAL_MARKERS = (
    "rate limit", "rate_limit", "429", "quota", "insufficient credits",
    "billing", "unauthorized", "401", "403", "invalid api key",
    "authentication", "credentials", "dns", "timeout", "timed out",
    "connection", "network", "not found", "404", "too many requests",
    "service unavailable", "503", "overloaded", "content policy",
    "safety filter", "bad request", "payment",
)


def classify_repairable_error(exc_type, message, module=""):
    """Return ``(fingerprint, task_description)`` or ``None``.

    A result is returned only for a deterministic OpenCloud-internal code
    regression.  ``exc_type`` is the exception type name (e.g. ``"TypeError"``),
    ``message`` is ``str(exception)``, and ``module`` is the failing module path
    when known.
    """
    exc_type = str(exc_type or "").strip().lower()
    message = str(message or "")
    module = str(module or "")
    low = message.lower()

    # Fast positive: OpenCloud-internal control metadata reached a provider.
    if "_opencloud_" in low:
        return (
            "opencloud_metadata_leak",
            "A provider request leaked OpenCloud-internal '_opencloud_' metadata; "
            "strip internal keys at the provider-kwargs boundary.",
        )

    # Fast negative: never repair external / operational failures.
    for marker in _EXTERNAL_MARKERS:
        if marker in low:
            return None

    # OpenCloud integration ImportError.
    if exc_type in ("importerror", "modulenotfounderror"):
        if "opencloud" in low or "opencloud" in module.lower():
            return (
                "opencloud_import_error",
                f"Fix the OpenCloud integration import: {message[:200]}",
            )
        return None

    # Local deterministic code errors (TypeError / AttributeError) that are
    # OpenCloud-scoped, or that carry no external-failure signal at all.
    if exc_type in ("typeerror", "attributeerror"):
        if "opencloud" in (low + " " + module.lower()):
            return (
                f"opencloud_{exc_type}",
                f"Fix the OpenCloud integration {exc_type}: {message[:200]}",
            )
        return (
            f"internal_{exc_type}",
            f"Fix the internal code {exc_type}: {message[:200]}",
        )

    return None


class RepairOrchestrator:
    """Drive the bounded repair -> restart -> replay flow.

    All side effects are injected; the production defaults live in
    :func:`production_orchestrator`.
    """

    def __init__(
        self,
        *,
        state_root,
        invoke_repair,
        restart,
        health_check,
        replay,
        rollback=None,
        cooldown_seconds=DEFAULT_COOLDOWN_SECONDS,
        now=None,
    ):
        self.state_root = Path(state_root)
        self.invoke_repair = invoke_repair
        self.restart = restart
        self.health_check = health_check
        self.replay = replay
        self.rollback = rollback or (lambda: None)
        self.cooldown_seconds = cooldown_seconds
        self._now = now or time.time
        self._cooldown_dir = self.state_root / "cooldowns"
        self._pending_replay = self.state_root / "pending-replay.json"
        self._in_progress = self.state_root / "autotrigger-in-progress"

    # -- gating ------------------------------------------------------------

    def _last_attempt(self, fingerprint):
        try:
            return float((self._cooldown_dir / fingerprint).read_text().strip())
        except (OSError, ValueError):
            return None

    def should_attempt(self, fingerprint):
        """False when the fingerprint is in cooldown or a repair is running."""
        if self._in_progress.exists():
            return False
        last = self._last_attempt(fingerprint)
        if last is not None and (self._now() - last) < self.cooldown_seconds:
            return False
        return True

    # -- main flow ---------------------------------------------------------

    def run(self, fingerprint, task, user_request, metadata=None):
        """Run one bounded repair attempt.

        Returns a status string: ``skipped``, ``repair_failed``,
        ``health_failed``, ``replayed``, or ``restart_signaled``.
        """
        if not self.should_attempt(fingerprint):
            return "skipped"

        # No recursion: mark in-progress before any repair work so a replayed
        # request (or any concurrent path) cannot re-enter.
        self._cooldown_dir.mkdir(parents=True, exist_ok=True)
        (self._cooldown_dir / fingerprint).write_text(str(self._now()))
        self._in_progress.write_text(str(self._now()))
        try:
            if not self.invoke_repair(task):
                self._clear_in_progress()
                return "repair_failed"

            self._write_pending_replay(user_request, metadata=metadata)
            self.restart()

            # In production restart() does not return (os._exit(75)); test
            # doubles return so the post-restart checks below can be exercised.
            if not self.health_check():
                self.rollback()
                self._clear_in_progress()
                return "health_failed"

            self.replay(user_request)
            self._clear_pending_replay()
            self._clear_in_progress()
            return "replayed"
        except Exception:
            self._clear_in_progress()
            raise

    # -- pending replay ----------------------------------------------------

    def _write_pending_replay(self, user_request, metadata=None):
        self.state_root.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(
            {
                "request": str(user_request or ""),
                "metadata": dict(metadata or {}),
                "ts": self._now(),
            },
            ensure_ascii=False,
        )
        self._pending_replay.write_text(payload)

    def _clear_pending_replay(self):
        try:
            self._pending_replay.unlink()
        except FileNotFoundError:
            pass

    def _clear_in_progress(self):
        try:
            self._in_progress.unlink()
        except FileNotFoundError:
            pass

    @staticmethod
    def consume_pending_replay(state_root):
        """Read and delete the pending-replay marker exactly once.

        Returns the full payload dict (``{"request": str, "metadata": dict,
        "ts": float}``), or ``None`` when there is nothing to replay.
        Consuming the marker is idempotent-guarded by deletion: the second
        call returns ``None``.
        """
        marker = Path(state_root) / "pending-replay.json"
        try:
            data = json.loads(marker.read_text())
        except (OSError, ValueError):
            return None
        # Delete before replaying: if the replay itself fails, we must not
        # loop forever — one replay attempt maximum.
        try:
            marker.unlink()
        except FileNotFoundError:
            pass
        return data if isinstance(data, dict) else None


def production_orchestrator(state_root=None, hermes_code_repair=None):
    """Build the orchestrator wired to the real repair harness + systemd.

    ``restart`` exits with ``_RESTART_EXIT_CODE`` (75) which the gateway unit's
    ``RestartForceExitStatus=75`` turns into a clean restart.  ``invoke_repair``
    shells out to ``hermes-code-repair``; a non-zero exit means the harness
    already rolled back and the live tree is unchanged.
    """
    import subprocess

    state_root = Path(state_root or os.environ.get(
        "OPEN_CLOUD_REPAIR_STATE", "~/.local/share/opencloud-repair"
    )).expanduser()

    repair_bin = str(hermes_code_repair or "hermes-code-repair")

    def _invoke_repair(task):
        try:
            result = subprocess.run(
                [repair_bin, "--task", str(task)],
                capture_output=True,
                text=True,
                timeout=int(os.environ.get("OPEN_CLOUD_REPAIR_TIMEOUT", "900")),
            )
            return result.returncode == 0
        except Exception:
            return False

    def _restart():
        os._exit(_RESTART_EXIT_CODE)

    def _health_check():
        return True  # the startup replay path is the real post-restart check

    return RepairOrchestrator(
        state_root=state_root,
        invoke_repair=_invoke_repair,
        restart=_restart,
        health_check=_health_check,
        replay=lambda _req: None,
    )


def maybe_auto_repair(exc_type, message, user_request=None, module="", metadata=None):
    """Classify and, when repairable and allowed, dispatch one repair attempt.

    Returns the orchestration status string, or ``None`` when the error was not
    classified as a repairable internal regression.  ``metadata`` (e.g. the
    originating gateway channel) is stored in the replay marker so the startup
    replay can route back to the same user.
    """
    classified = classify_repairable_error(exc_type, message, module=module)
    if classified is None:
        return None
    fingerprint, task = classified
    orchestrator = production_orchestrator()
    if not orchestrator.should_attempt(fingerprint):
        return "skipped"
    return orchestrator.run(fingerprint, task, user_request, metadata=metadata)
