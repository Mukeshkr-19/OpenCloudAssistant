"""Runtime incident producer: hermes-gateway journal → classify → sanitize → ingest.

ponytail: timer-driven journalctl (fixed argv, shell=False) over a long-running
watcher — same latency budget, far easier to test with a fake journal adapter.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
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

# Journal line → (exc_type, message). First match wins.
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
        re.compile(
            r"(?i)(Main process exited|Failed with result|core-dump|segfault|"
            r"gateway.*crash)"
        ),
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


def parse_journal_line(line: str) -> Optional[DetectedEvent]:
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
                    os.environ.get("OPEN_CLOUD_SELF_HEAL_DETECT_TIMEOUT", "60")
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


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


class RuntimeDetector:
    """Journal → events → controller.ingest. Cursor advanced only after inspect."""

    def __init__(
        self,
        *,
        state_root: Path,
        ingest_fn: Callable[..., Any],
        journal: Optional[JournalReader] = None,
        units: tuple[str, ...] = DEFAULT_UNITS,
        since: str = FIRST_WINDOW,
    ):
        self.state_root = Path(state_root)
        self.cursor_path = self.state_root / CURSOR_NAME
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

    def detect(self, *, auto_run: bool = True) -> dict[str, Any]:
        prior = self.load_cursor()
        lines, new_cursor = self.journal.read(
            cursor=prior, since=self.since, units=self.units
        )
        events: list[DetectedEvent] = []
        ignored = 0
        for line in lines:
            ev = parse_journal_line(line)
            if ev is None:
                ignored += 1
                continue
            events.append(ev)

        ingested: list[Any] = []
        for ev in events:
            row = self.ingest_fn(
                ev.exc_type,
                ev.message,
                module=ev.module,
                context=ev.context,
                auto_run=auto_run,
            )
            if row:
                ingested.append(row.get("id") if isinstance(row, dict) else row)

        # Advance cursor only after safe inspect + ingest attempts.
        if new_cursor:
            self.save_cursor(new_cursor)

        return {
            "event": "self-heal-detect",
            "lines": len(lines),
            "ignored": ignored,
            "matched": len(events),
            "ingested": len(ingested),
            "ingested_ids": ingested,
            "cursor_advanced": bool(new_cursor),
            "had_prior_cursor": prior is not None,
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
