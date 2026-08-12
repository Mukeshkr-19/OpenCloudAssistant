#!/usr/bin/env python3
"""Canonical Fleet runtime path and verification-freshness policy."""

import os
import fcntl
from contextlib import contextmanager
from pathlib import Path


DEFAULT_TTL_SECONDS = 86400
MAX_TTL_SECONDS = 31536000


def fleet_root() -> Path:
    return Path(os.environ.get("OPEN_CLOUD_FLEET_HOME", Path.home() / ".local/share/hermes-fleet")).expanduser().resolve()


def verification_ttl_ms() -> int:
    raw = os.environ.get("OPEN_CLOUD_MODEL_VERIFICATION_TTL_SECONDS", str(DEFAULT_TTL_SECONDS)).strip()
    try:
        value = int(raw)
    except ValueError as exc:
        raise SystemExit("ERROR: OPEN_CLOUD_MODEL_VERIFICATION_TTL_SECONDS must be an integer") from exc
    if value < 0:
        raise SystemExit("ERROR: OPEN_CLOUD_MODEL_VERIFICATION_TTL_SECONDS cannot be negative")
    if value > MAX_TTL_SECONDS:
        raise SystemExit(f"ERROR: OPEN_CLOUD_MODEL_VERIFICATION_TTL_SECONDS cannot exceed {MAX_TTL_SECONDS}")
    return value * 1000


@contextmanager
def registry_lock(registry_dir=None):
    directory = Path(registry_dir) if registry_dir else fleet_root() / "registry"
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / "registry.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        yield
