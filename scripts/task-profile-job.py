#!/usr/bin/env python3
"""Materialize an optional private task without exposing its content."""

import argparse
import importlib.util
import json
import os
import stat
import sys
import tempfile
from pathlib import Path

# Shared task-profile derivation (same source of truth as the validation in
# hermes-config.py). Loaded by path because the module filename contains a hyphen.
_spec = importlib.util.spec_from_file_location(
    "hermes_config", Path(__file__).resolve().parent / "hermes-config.py"
)
hermes_config = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(hermes_config)
required_operations = hermes_config.required_operations


def atomic_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=path.name + ".", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as handle:
            json.dump(data, handle)
            handle.write("\n")
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


def task_prompt(task):
    parts = [task["prompt"].strip()]
    topics = task.get("research_topics") or []
    if topics:
        parts.extend(("", "Research topics:", *[f"- {item.strip()}" for item in topics]))
    if task.get("use_vellum_context"):
        parts.extend(("", "Use the permitted read-only user context when relevant."))
    for key, label in (("output_policy", "Output policy"), ("scoring_policy", "Scoring policy")):
        if task.get(key) is not None:
            rendered = task[key] if isinstance(task[key], str) else json.dumps(task[key], sort_keys=True)
            parts.extend(("", f"{label}:", rendered))
    return "\n".join(parts)


def load_state(path):
    if path.is_symlink():
        raise SystemExit("ERROR: task-profile state cannot be a symbolic link")
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError) as exc:
        raise SystemExit("ERROR: task-profile state is malformed") from exc
    if not isinstance(data, dict) or data.get("version") != 1 or not isinstance(data.get("job_id"), str):
        raise SystemExit("ERROR: task-profile state is malformed")
    return data


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("apply", "verify"))
    parser.add_argument("--name", required=True)
    parser.add_argument("--profile", required=True)
    parser.add_argument("--profile-home", required=True)
    parser.add_argument("--hermes-root", required=True)
    args = parser.parse_args()
    profile_path = Path(args.profile)
    try:
        profile = json.loads(profile_path.read_text())
    except (OSError, ValueError) as exc:
        raise SystemExit("ERROR: private task profile is malformed") from exc
    task = profile.get("task")
    hermes_root = Path(args.hermes_root)
    profile_home = Path(args.profile_home).resolve()
    state_path = profile_path.with_suffix(".state.json")
    state = load_state(state_path)
    if task is None and not state:
        print("TASK_PROFILE_JOB: NOT_CONFIGURED")
        return
    if not (hermes_root / "cron/jobs.py").is_file():
        raise SystemExit("ERROR: Hermes cron runtime is missing")
    scheduler_provider = hermes_root / "cron/scheduler_provider.py"
    scheduler_source = scheduler_provider.read_text() if scheduler_provider.is_file() else ""
    if "def _start_multiplex(" not in scheduler_source or "profile_homes" not in scheduler_source:
        raise SystemExit("ERROR: Hermes runtime cannot tick profile-scoped cron jobs")
    os.environ["HERMES_HOME"] = str(profile_home)
    sys.path.insert(0, str(hermes_root))
    from cron.jobs import create_job, get_job, load_jobs, parse_schedule, pause_job, update_job

    job = get_job(state.get("job_id", "")) if state.get("job_id") else None
    if task is None:
        if args.action == "apply" and job and job.get("enabled", True):
            pause_job(job["id"], "private task removed from profile")
            job = get_job(job["id"])
        if job and job.get("enabled", True):
            raise SystemExit("ERROR: removed private task still has an enabled job; run apply")
        print("TASK_PROFILE_JOB: DISABLED")
        return

    prompt = task_prompt(task)
    protected, must_execute = required_operations(profile)
    required_fields = {
        "required_tools": protected,
        "required_to_execute": must_execute,
    }
    expected = {
        "name": task.get("name") or f"OpenCloud task profile: {args.name}",
        "prompt": prompt,
        "schedule": parse_schedule(task["schedule"]),
        "deliver": task.get("deliver", "local"),
        "enabled_toolsets": profile["enabled_toolsets"],
    }
    expected.update(required_fields)
    matches = [candidate for candidate in load_jobs() if all(candidate.get(key) == value for key, value in expected.items())]
    if len(matches) > 1:
        raise SystemExit("ERROR: duplicate managed task-profile jobs detected")
    if not job:
        if matches:
            job = matches[0]
    if args.action == "apply":
        if job:
            job = update_job(job["id"], expected)
        else:
            job = create_job(
                prompt=prompt,
                schedule=task["schedule"],
                name=expected["name"],
                deliver=expected["deliver"],
                enabled_toolsets=expected["enabled_toolsets"],
            )
            # create_job has no required-operation parameters; persist them
            # via update_job so the fields ride the same storage path as edits.
            job = update_job(job["id"], required_fields)
        atomic_json(state_path, {"version": 1, "job_id": job["id"]})
    elif not job:
        raise SystemExit("ERROR: materialized task-profile job is missing")

    for key, value in expected.items():
        if job.get(key) != value:
            raise SystemExit(f"ERROR: materialized task-profile job mismatch: {key}")
    if stat.S_IMODE(state_path.stat().st_mode) != 0o600:
        raise SystemExit("ERROR: task-profile state must use mode 0600")
    print("TASK_PROFILE_JOB: PASS")


if __name__ == "__main__":
    main()
