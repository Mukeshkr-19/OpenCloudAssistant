#!/usr/bin/env python3
"""Execute one queued Vellum bridge task and atomically publish its state."""

from __future__ import annotations

import json
import fcntl
import os
import shlex
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path


BASE_DIR = Path.home() / ".config" / "hermes-vellum" / "mcp"
STATE_DIR = BASE_DIR / "state"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def write(path: Path, state: dict) -> None:
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(state, indent=2, ensure_ascii=False) + "\n")
    os.chmod(temp, 0o600)
    os.replace(temp, path)


def transition(path: Path, update) -> dict:
    lock_path = path.with_suffix(".lock")
    with lock_path.open("a") as lock:
        os.chmod(lock_path, 0o600)
        fcntl.flock(lock, fcntl.LOCK_EX)
        state = json.loads(path.read_text())
        update(state)
        write(path, state)
        return state


def main() -> int:
    if len(sys.argv) != 2:
        raise SystemExit("usage: worker.py TASK_ID")
    path = STATE_DIR / f"{sys.argv[1]}.json"
    def start(value):
        if value.get("status") in {"queued", "starting"}:
            value.update(status="running", updated_at=utc_now())
    state = transition(path, start)
    if state.get("status") != "running":
        return 1

    command = shlex.split(os.environ.get("OPEN_CLOUD_VELLUM_TASK_COMMAND", "vellum message"))
    try:
        proc = subprocess.run(
            command + [state["prompt"]],
            text=True,
            capture_output=True,
            timeout=int(state.get("timeout_seconds", 600)),
            check=False,
        )
        if proc.returncode:
            state.update(status="failed", error="Vellum task command failed")
        else:
            state.update(status="completed", result=proc.stdout.strip())
    except subprocess.TimeoutExpired:
        state.update(status="failed", error="Vellum task timed out")
    except Exception as exc:
        state.update(status="failed", error=type(exc).__name__)
    state["updated_at"] = utc_now()
    terminal = dict(state)
    transition(path, lambda value: value.update(terminal) if value.get("status") != "cancelled" else None)
    return 0 if state["status"] == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
