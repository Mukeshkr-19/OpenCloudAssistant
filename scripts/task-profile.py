#!/usr/bin/env python3
"""Apply or verify a private local task profile without exposing its contents."""

import argparse
import fcntl
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
NAME = re.compile(r"[a-z0-9][a-z0-9_-]{0,39}")


def atomic_marker(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = path.read_text().splitlines() if path.exists() else []
    lines = [line for line in lines if not line.startswith("OPEN_CLOUD_RESTRICTIVE_PROFILE=")]
    content = "\n".join(lines + ["OPEN_CLOUD_RESTRICTIVE_PROFILE=1"]) + "\n"
    fd, tmp = tempfile.mkstemp(prefix=path.name + ".", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def reject_link(path, label):
    if path.is_symlink():
        raise SystemExit(f"ERROR: {label} cannot be a symbolic link")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("apply", "verify"))
    parser.add_argument("--name", required=True)
    args = parser.parse_args()
    if not NAME.fullmatch(args.name):
        raise SystemExit("ERROR: invalid task profile name")
    home = Path(os.environ.get("OPEN_CLOUD_HOME", str(Path.home())))
    profile_dir = home / ".opencloud" / "task-profiles"
    private = profile_dir / f"{args.name}.json"
    hermes = home / ".hermes" / "profiles" / args.name / "config.yaml"
    for path, label in ((profile_dir, "task profile directory"), (private, "task profile"),
                        (hermes.parent, "Hermes profile directory"), (hermes, "Hermes profile config")):
        reject_link(path, label)
    if not private.is_file() or not hermes.is_file():
        raise SystemExit("ERROR: named task profile or matching Hermes profile is missing")
    os.chmod(profile_dir, 0o700)
    os.chmod(hermes.parent, 0o700)
    os.chmod(hermes, 0o600)
    if private.stat().st_mode & 0o077:
        raise SystemExit("ERROR: private task profile permissions must be 0600")
    python = Path(os.environ.get("OPEN_CLOUD_HERMES_PYTHON", sys.executable))
    if not python.is_file():
        python = Path(sys.executable)
    marker = hermes.parent / ".env"
    reject_link(marker, "restrictive profile marker")
    lock_path = profile_dir / f".{args.name}.lock"
    reject_link(lock_path, "task profile lock")
    with lock_path.open("a") as lock:
        os.chmod(lock_path, 0o600)
        fcntl.flock(lock, fcntl.LOCK_EX)
        if args.action == "apply":
            atomic_marker(marker)
        elif not marker.is_file() or "OPEN_CLOUD_RESTRICTIVE_PROFILE=1" not in marker.read_text().splitlines():
            raise SystemExit("ERROR: restrictive Hermes profile marker is missing or invalid")

        common = ["--policy", str(ROOT / "config/hermes/orchestration.json"),
                  "--server", str(home / ".config/hermes-vellum/mcp/server.py"), "--python", str(python)]
        for config, extra in ((home / ".hermes/config.yaml", []), (hermes, ["--task-profile", str(private)])):
            command = [str(python), str(ROOT / "scripts/hermes-config.py"), args.action,
                       "--config", str(config), *common, *extra]
            result = subprocess.run(command, check=False)
            if result.returncode:
                raise SystemExit(result.returncode)
        job_command = [str(python), str(ROOT / "scripts/task-profile-job.py"), args.action,
            "--name", args.name, "--profile", str(private), "--profile-home", str(hermes.parent),
            "--hermes-root", os.environ.get("OPEN_CLOUD_HERMES_ROOT", str(home / ".hermes/hermes-agent"))]
        raise SystemExit(subprocess.run(job_command, check=False).returncode)


if __name__ == "__main__":
    main()
