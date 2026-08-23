#!/usr/bin/env python3
"""CLI: opencloud self-heal status|incidents|show|retry|disable|enable|ingest|detect|run."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Allow running as `python3 .../cli.py` without install.
_ROOT = Path(__file__).resolve().parents[3]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT / "integrations" / "self-repair"))

from guarded_heal.controller import SelfHealController  # noqa: E402
from guarded_heal.detector import RuntimeDetector  # noqa: E402


def _controller(repo: Path | None = None) -> SelfHealController:
    root = repo or Path(__file__).resolve().parents[3]
    return SelfHealController(repo_root=root)


def cmd_status(_args: argparse.Namespace) -> int:
    print(json.dumps(_controller().status(), indent=2))
    return 0


def cmd_incidents(args: argparse.Namespace) -> int:
    store = _controller().store
    rows = store.list_incidents(limit=args.limit)
    for row in rows:
        print(
            f"{row['id']}\t{row['state']}\t{row['tier']}\t{row['severity']}\t{row['title']}"
        )
    return 0


def cmd_show(args: argparse.Namespace) -> int:
    ctrl = _controller()
    row = ctrl.store.get(args.id)
    if not row:
        print(f"not found: {args.id}", file=sys.stderr)
        return 1
    print(json.dumps(row, indent=2, default=str))
    print("--- events ---")
    for ev in ctrl.store.events(args.id):
        print(f"{ev['ts']}\t{ev['kind']}\t{ev['detail'][:200]}")
    return 0


def cmd_retry(args: argparse.Namespace) -> int:
    row = _controller().retry(args.id)
    print(json.dumps(row, indent=2, default=str))
    return 0 if row and row.get("state") not in ("FAILED",) else 1


def cmd_enable(_args: argparse.Namespace) -> int:
    _controller().store.set_enabled(True)
    print("self-heal: ENABLED")
    return 0


def cmd_disable(_args: argparse.Namespace) -> int:
    _controller().store.set_enabled(False)
    print("self-heal: DISABLED")
    return 0


def cmd_ingest(args: argparse.Namespace) -> int:
    row = _controller().ingest(
        args.exc_type,
        args.message,
        module=args.module or "",
        context=args.context or "",
        auto_run=not args.no_run,
    )
    if row is None:
        print("no incident (filtered or disabled)")
        return 0
    print(json.dumps(row, indent=2, default=str))
    return 0


def cmd_detect(args: argparse.Namespace) -> int:
    """Lightweight journal → queue. Never auto-processes / recovers."""
    ctrl = _controller()
    det = RuntimeDetector(state_root=ctrl.state_root, ingest_fn=ctrl.ingest)
    # Detector is always queue-only; --no-run kept for CLI compat.
    result = det.detect(auto_run=False)
    print(json.dumps(result, indent=2))
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    """Controller tick: drain inbox → process queued incidents (bounded)."""
    ctrl = _controller()
    no_run = bool(getattr(args, "no_run", False))
    ingested = ctrl.scan_inbox(auto_run=False)
    processed: list = []
    if not no_run:
        processed = ctrl.process_queue()
    st = ctrl.status()
    print(
        json.dumps(
            {
                "event": "self-heal-tick",
                "ingested": len(ingested),
                "ingested_ids": [r.get("id") for r in ingested],
                "processed": len(processed),
                "processed_ids": [r.get("id") for r in processed],
                **st,
            },
            indent=2,
        )
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="opencloud self-heal")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("status").set_defaults(func=cmd_status)

    p_inc = sub.add_parser("incidents")
    p_inc.add_argument("--limit", type=int, default=50)
    p_inc.set_defaults(func=cmd_incidents)

    p_show = sub.add_parser("show")
    p_show.add_argument("id")
    p_show.set_defaults(func=cmd_show)

    p_retry = sub.add_parser("retry")
    p_retry.add_argument("id")
    p_retry.set_defaults(func=cmd_retry)

    sub.add_parser("enable").set_defaults(func=cmd_enable)
    sub.add_parser("disable").set_defaults(func=cmd_disable)

    p_ing = sub.add_parser("ingest")
    p_ing.add_argument("--exc-type", required=True)
    p_ing.add_argument("--message", required=True)
    p_ing.add_argument("--module", default="")
    p_ing.add_argument("--context", default="")
    p_ing.add_argument("--no-run", action="store_true")
    p_ing.set_defaults(func=cmd_ingest)

    p_det = sub.add_parser("detect")
    p_det.add_argument(
        "--no-run",
        action="store_true",
        help="compat: detect is always queue-only (never recovers)",
    )
    p_det.set_defaults(func=cmd_detect)

    p_run = sub.add_parser("run")
    p_run.add_argument(
        "--no-run",
        action="store_true",
        help="ingest inbox to QUEUED only; do not process queue",
    )
    p_run.set_defaults(func=cmd_run)
    args = parser.parse_args(argv)
    return int(args.func(args) or 0)


if __name__ == "__main__":
    raise SystemExit(main())
