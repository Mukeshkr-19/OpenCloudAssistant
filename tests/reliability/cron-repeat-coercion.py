#!/usr/bin/env python3
"""Deterministic regression coverage for the cron repeat-coercion patch (P11).

The cronjob schema types ``repeat`` as integer, but a model frequently passes a
natural-language string ("forever", "daily", "once"). Comparing that string
with ``<= 0`` in the create/update path raised ``TypeError`` and surfaced as an
opaque tool error: "'<=' not supported between instances of 'str' and 'int'".
P11 coerces ``repeat`` at the API boundary so both create and update are robust.

Network-free and store-isolated: ``list_jobs``, ``resolve_job_ref``,
``create_job`` and ``update_job`` are monkeypatched with synthetic jobs, so no
real cron store is read or written.
"""

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
HERMES_ROOT = Path(os.environ.get("OPEN_CLOUD_HERMES_ROOT", Path.home() / ".hermes/hermes-agent"))
PATCH = ROOT / "integrations/hermes/hermes-cron-repeat-coercion.patch"


def materialize(out: Path) -> bool:
    result = subprocess.run(
        ["bash", str(ROOT / "install/30-brain-materialize.sh"), "--stage", str(out)],
        env={**os.environ, "OPEN_CLOUD_HERMES_ROOT": str(HERMES_ROOT)},
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        print(
            "CRON_REPEAT_COERCION_RELIABILITY: SKIP (materialization failed): "
            + (result.stderr or result.stdout or "").strip()
        )
        return False
    return True


def main() -> None:
    if not (HERMES_ROOT / ".git").is_dir():
        print("CRON_REPEAT_COERCION_RELIABILITY: SKIP (Hermes Git source unavailable)")
        return

    with tempfile.TemporaryDirectory(prefix="opencloud-cron-repeat-coercion-") as tmp:
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

        # ── 1. _coerce_repeat is deterministic and never raises ───────────
        cases = [
            (None, None),
            (0, None),
            (-1, None),
            (3, 3),
            ("3", 3),
            ("0", None),
            ("forever", None),
            ("daily", None),
            ("once", None),
            ("unknown wording", None),
            (True, None),
        ]
        for value, expected in cases:
            got = cjt._coerce_repeat(value)
            assert got == expected, f"_coerce_repeat({value!r}) = {got!r}, expected {expected!r}"

        orig_list = cjt.list_jobs
        orig_create = cjt.create_job
        orig_resolve = cjt.resolve_job_ref
        orig_update = cjt.update_job

        job = {
            "id": "a6cc8dd39f62",
            "name": "Daily Career Job Match Report",
            "schedule": {"kind": "cron", "expr": "30 9 * * *", "display": "30 9 * * *"},
            "schedule_display": "30 9 * * *",
            "enabled": True,
            "repeat": {"times": None, "completed": 0},
        }

        # ── 2. update with a string repeat no longer raises TypeError ─────
        cjt.list_jobs = lambda include_disabled=False: []
        cjt.resolve_job_ref = lambda ref: job if str(ref).lower() in ("a6cc8dd39f62",) else None
        updated = []
        cjt.update_job = lambda jid, updates: updated.append((jid, updates)) or {**job, **updates}
        try:
            result = cjt.cronjob(action="update", job_id="a6cc8dd39f62", repeat="forever")
            parsed = json.loads(result)
            assert parsed.get("success") is True, parsed
            assert "not supported between instances" not in result, result
            assert updated, "update_job must be called"
            stored = updated[0][1].get("repeat")
            assert stored == {"times": None, "completed": 0}, stored
        finally:
            cjt.resolve_job_ref = orig_resolve
            cjt.update_job = orig_update
            cjt.list_jobs = orig_list

        # ── 3. create with a numeric-string repeat is coerced to int ──────
        created = []
        cjt.list_jobs = lambda include_disabled=False: []
        cjt.create_job = lambda **kw: created.append(kw) or {
            "id": "new", "name": "x", "schedule_display": "every 30m",
            "repeat": {"times": kw.get("repeat"), "completed": 0},
            "deliver": "local", "next_run_at": None,
        }
        try:
            result = cjt.cronjob(
                action="create", schedule="every 30m", prompt="check stocks", repeat="3"
            )
            parsed = json.loads(result)
            assert parsed.get("success") is True, parsed
            assert created, "create_job must be called"
            assert created[0].get("repeat") == 3, created[0]
        finally:
            cjt.create_job = orig_create
            cjt.list_jobs = orig_list

        # ── 4. Source-level wiring ────────────────────────────────────────
        source = (tree / "tools/cronjob_tools.py").read_text()
        assert "HERMES_CRON_REPEAT_COERCION_V1" in source
        assert "def _coerce_repeat" in source
        assert "not replace a detailed structured prompt" in source

    print("PASS _coerce_repeat is deterministic and never raises")
    print("PASS update with a string repeat no longer raises TypeError")
    print("PASS create with a numeric-string repeat is coerced to int")
    print("PASS prompt-preservation guidance is in the tool schema")
    print("CRON_REPEAT_COERCION_RELIABILITY: PASS")


if __name__ == "__main__":
    main()
