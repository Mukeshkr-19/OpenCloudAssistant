"""Runtime incident producer: hermes-gateway journal → classify → sanitize → queue.

Detector NEVER recovers: no process(), OpenCode, hermes-code-repair, Fleet,
systemctl restart, deploy, GH, or validation. Seconds-only tick.

ponytail: timer-driven journalctl (fixed argv, shell=False) over a long-running
watcher — same latency budget, far easier to test with a fake journal adapter.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Optional, Protocol

from .controller import sanitize_for_opencode


# Units we may read. Hermes gateway is primary; OpenCloud oneshots only when
# they emit actionable failures (not routine tick noise).
DEFAULT_UNITS = (
    "hermes-gateway.service",
    "opencloud-self-heal.service",
    "opencloud-runtime-update.service",
)

FIRST_WINDOW = os.environ.get("OPEN_CLOUD_SELF_HEAL_DETECT_SINCE", "2 min ago")
CURSOR_NAME = "journal.cursor"
LIFECYCLE_NAME = "gateway-lifecycle.json"
# Bound window for intentional stop→start sequences across detect ticks.
LIFECYCLE_TTL_SEC = int(
    os.environ.get("OPEN_CLOUD_SELF_HEAL_LIFECYCLE_TTL", "300")
)

# Lifecycle markers (controlled restart ≠ crash).
_RE_STOPPING = re.compile(
    r"(?i)\b(Stopping|stop-sigterm|Received SIGTERM|Killing process.*SIGTERM|"
    r"systemd\[.*\]:.*Stopping)\b"
)
_RE_SIGTERM = re.compile(r"(?i)\bSIGTERM\b|code=killed,\s*signal=TERM")
_RE_STOPPED = re.compile(r"(?i)\bStopped\b.*hermes-gateway|\bDeactivated successfully\b")
_RE_STARTED = re.compile(
    r"(?i)\bStarted\b.*hermes-gateway|hermes-gateway\.service:.*Started"
)
_RE_FAILED_RESULT = re.compile(
    r"(?i)(Main process exited|Failed with result|Unit entered failed state)"
)
_RE_GENUINE_CRASH = re.compile(
    r"(?i)(core-dump|segfault|SIGSEGV|SIGABRT|gateway.*crash|"
    r"Main process exited.*status=0/DUMP|"
    r"code=dumped|code=killed,\s*signal=SEGV)"
)

# Journal line → (exc_type, message). First match wins (after lifecycle filter).
LINE_RULES: tuple[tuple[re.Pattern[str], str, str], ...] = (
    (
        re.compile(r"(?i)choices must be a list of strings"),
        "ValueError",
        "clarify tool: choices must be a list of strings",
    ),
    (
        re.compile(r"(?i)same_tool_failure_halt"),
        "RuntimeError",
        "same_tool_failure_halt",
    ),
    (
        re.compile(r"(?i)\bTypeError\b[:\s]+(.+)$"),
        "TypeError",
        "",
    ),
    (
        re.compile(r"(?i)\bAttributeError\b[:\s]+(.+)$"),
        "AttributeError",
        "",
    ),
    (
        # Genuine crash markers only — intentional restart handled by lifecycle.
        _RE_GENUINE_CRASH,
        "RuntimeError",
        "hermes-gateway crash or abnormal exit",
    ),
    (
        re.compile(r"(?i)stuck turn|turn.*stuck|agent.*stuck"),
        "RuntimeError",
        "stuck turn detected",
    ),
    (
        re.compile(r"(?i)ReadTimeout|httpx\.ReadTimeout|APITimeoutError"),
        "APITimeoutError",
        "provider ReadTimeout",
    ),
)


class JournalReader(Protocol):
    def read(
        self, *, cursor: Optional[str], since: str, units: tuple[str, ...]
    ) -> tuple[list[str], Optional[str]]:
        """Return (lines, new_cursor). None cursor → do not advance."""


@dataclass
class DetectedEvent:
    exc_type: str
    message: str
    module: str
    context: str


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


class GatewayLifecycle:
    """Persist expected stop/SIGTERM/started/failed across detect ticks.

    Exact intentional systemd restart (Stopping → SIGTERM → Main process exited
    → Failed → Stopped → Started) → crash_match=0.
    """

    def __init__(self, path: Path, *, now: Optional[Callable[[], float]] = None):
        self.path = Path(path)
        self._now = now or time.time
        self.state: dict[str, Any] = {
            "expected_stop": False,
            "sigterm": False,
            "failed_under_stop": False,
            "stopped": False,
            "started": False,
            "updated_at": 0.0,
        }
        self.load()

    def load(self) -> None:
        if not self.path.is_file():
            return
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        if isinstance(raw, dict):
            self.state.update(raw)
        self._expire_if_stale()

    def save(self) -> None:
        self.state["updated_at"] = self._now()
        _atomic_write(self.path, json.dumps(self.state, indent=2) + "\n")

    def _expire_if_stale(self) -> None:
        updated = float(self.state.get("updated_at") or 0)
        if updated and (self._now() - updated) > LIFECYCLE_TTL_SEC:
            self.clear(persist=False)

    def clear(self, *, persist: bool = True) -> None:
        self.state = {
            "expected_stop": False,
            "sigterm": False,
            "failed_under_stop": False,
            "stopped": False,
            "started": False,
            "updated_at": self._now(),
        }
        if persist:
            self.save()

    @property
    def controlled(self) -> bool:
        """True when we are inside / completing an intentional stop sequence."""
        return bool(self.state.get("expected_stop") or self.state.get("sigterm"))

    def observe(self, line: str) -> str:
        """Update state from one journal line. Return kind: lifecycle|noise|crash_candidate."""
        self._expire_if_stale()
        text = (line or "").strip()
        if not text:
            return "noise"

        if _RE_STOPPING.search(text) or (
            "stopping" in text.lower() and "hermes-gateway" in text.lower()
        ):
            self.state["expected_stop"] = True
            self.state["started"] = False
            self.save()
            return "lifecycle"

        if _RE_SIGTERM.search(text) and (
            self.controlled or "hermes-gateway" in text.lower() or "under_systemd" in text.lower()
        ):
            self.state["sigterm"] = True
            self.state["expected_stop"] = True
            self.save()
            return "lifecycle"

        if _RE_FAILED_RESULT.search(text):
            if self.controlled:
                self.state["failed_under_stop"] = True
                self.save()
                return "lifecycle"
            # Unexpected failure → crash candidate (unless genuine crash rule fires elsewhere).
            return "crash_candidate"

        if _RE_STOPPED.search(text):
            if self.controlled:
                self.state["stopped"] = True
                self.save()
                return "lifecycle"
            return "noise"

        if _RE_STARTED.search(text):
            if self.controlled or self.state.get("failed_under_stop"):
                self.clear(persist=True)
                return "lifecycle"
            self.state["started"] = True
            self.save()
            return "lifecycle"

        return "noise"


def parse_journal_line(
    line: str,
    *,
    lifecycle: Optional[GatewayLifecycle] = None,
) -> Optional[DetectedEvent]:
    """Classify one journal line into a sanitize-ready event, or None to ignore."""
    text = (line or "").strip()
    if not text:
        return None
    low = text.lower()
    if any(
        m in low
        for m in (
            "deprecationwarning",
            "userwarning",
            "resourcewarning",
            "debug:",
            "trace:",
            "unavailable (optional)",
            "optional dependency",
        )
    ):
        return None

    kind = "noise"
    if lifecycle is not None:
        kind = lifecycle.observe(text)
        if kind == "lifecycle":
            return None

    # During controlled restart, suppress Main-process-exited style noise even if
    # observe already returned lifecycle — belt for multi-line windows.
    if lifecycle is not None and lifecycle.controlled and _RE_FAILED_RESULT.search(text):
        return None

    for pat, exc_type, fixed_msg in LINE_RULES:
        m = pat.search(text)
        if not m:
            continue
        msg = fixed_msg or (m.group(1).strip() if m.lastindex else text[:400])
        return DetectedEvent(
            exc_type=exc_type,
            message=sanitize_for_opencode(msg),
            module="hermes-gateway",
            context="journal",
        )

    # Unexpected Main process exited / Failed with result outside controlled stop.
    if kind == "crash_candidate" or (
        lifecycle is not None
        and not lifecycle.controlled
        and _RE_FAILED_RESULT.search(text)
        and "hermes-gateway" in low
    ):
        return DetectedEvent(
            exc_type="RuntimeError",
            message=sanitize_for_opencode("hermes-gateway crash or abnormal exit"),
            module="hermes-gateway",
            context="journal",
        )
    return None


class JournalctlAdapter:
    """Production journal reader. Always list argv + shell=False."""

    def __init__(
        self,
        *,
        runner: Optional[Callable[..., subprocess.CompletedProcess]] = None,
    ):
        self._runner = runner or subprocess.run

    def read(
        self, *, cursor: Optional[str], since: str, units: tuple[str, ...]
    ) -> tuple[list[str], Optional[str]]:
        argv: list[str] = [
            "journalctl",
            "--user",
            "--no-pager",
            "--output=short-iso",
            "--show-cursor",
        ]
        for unit in units:
            argv.extend(["-u", unit])
        if cursor:
            argv.extend(["--after-cursor", cursor])
        else:
            # First start: bounded recent window (no full-journal replay).
            argv.extend(["--since", since])
        try:
            proc = self._runner(
                argv,
                capture_output=True,
                text=True,
                timeout=int(
                    os.environ.get("OPEN_CLOUD_SELF_HEAL_DETECT_TIMEOUT", "20")
                ),
                shell=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            return [], None
        if proc.returncode not in (0, 1):
            return [], None
        lines: list[str] = []
        new_cursor: Optional[str] = None
        for line in (proc.stdout or "").splitlines():
            if line.startswith("-- cursor:"):
                new_cursor = line.split(":", 1)[1].strip()
                continue
            if line.startswith("-- No entries"):
                continue
            if line.strip():
                lines.append(line)
        return lines, new_cursor


class RuntimeDetector:
    """Journal → events → controller.ingest(auto_run=False). Cursor after safe persist."""

    def __init__(
        self,
        *,
        state_root: Path,
        ingest_fn: Callable[..., Any],
        journal: Optional[JournalReader] = None,
        units: tuple[str, ...] = DEFAULT_UNITS,
        since: str = FIRST_WINDOW,
        now: Optional[Callable[[], float]] = None,
    ):
        self.state_root = Path(state_root)
        self.cursor_path = self.state_root / CURSOR_NAME
        self.lifecycle = GatewayLifecycle(
            self.state_root / LIFECYCLE_NAME, now=now
        )
        self.ingest_fn = ingest_fn
        self.journal = journal or JournalctlAdapter()
        self.units = units
        self.since = since

    def load_cursor(self) -> Optional[str]:
        if not self.cursor_path.is_file():
            return None
        raw = self.cursor_path.read_text(encoding="utf-8").strip()
        return raw or None

    def save_cursor(self, cursor: str) -> None:
        _atomic_write(self.cursor_path, cursor + "\n")

    def detect(self, *, auto_run: bool = False) -> dict[str, Any]:
        """Always queue-only. ``auto_run`` is ignored (forced False) — recovery is
        the controller worker's job."""
        del auto_run  # detector never recovers
        prior = self.load_cursor()
        lines, new_cursor = self.journal.read(
            cursor=prior, since=self.since, units=self.units
        )
        events: list[DetectedEvent] = []
        ignored = 0
        lifecycle_suppressed = 0
        for line in lines:
            before = self.lifecycle.controlled
            ev = parse_journal_line(line, lifecycle=self.lifecycle)
            if ev is None:
                ignored += 1
                if before or self.lifecycle.controlled:
                    # Count lifecycle suppressions separately for operators.
                    if _RE_FAILED_RESULT.search(line) or _RE_STOPPING.search(line):
                        lifecycle_suppressed += 1
                continue
            events.append(ev)

        ingested: list[Any] = []
        for ev in events:
            row = self.ingest_fn(
                ev.exc_type,
                ev.message,
                module=ev.module,
                context=ev.context,
                auto_run=False,
            )
            if row:
                ingested.append(row.get("id") if isinstance(row, dict) else row)

        # Advance cursor only after safe inspect + ingest attempts.
        # Timeout/empty new_cursor → do not corrupt cursor.
        if new_cursor:
            self.save_cursor(new_cursor)

        return {
            "event": "self-heal-detect",
            "lines": len(lines),
            "ignored": ignored,
            "lifecycle_suppressed": lifecycle_suppressed,
            "matched": len(events),
            "ingested": len(ingested),
            "ingested_ids": ingested,
            "cursor_advanced": bool(new_cursor),
            "had_prior_cursor": prior is not None,
            "auto_run": False,
        }


def dump_event_json(ev: DetectedEvent) -> str:
    """Sanitized fields only — never raw journal text."""
    return json.dumps(
        {
            "exc_type": ev.exc_type,
            "message": ev.message,
            "module": ev.module,
            "context": ev.context,
        },
        ensure_ascii=False,
    )
