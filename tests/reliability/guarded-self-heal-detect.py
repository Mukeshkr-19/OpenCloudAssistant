#!/usr/bin/env python3
"""Detector + verified canary/rollback coverage (fake journal; no real systemd)."""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "integrations" / "self-repair"))

from guarded_heal.adapters import (  # noqa: E402
    ActionResult,
    CanaryAdapter,
    DeployAdapter,
    DeployResult,
    FleetAdapter,
    GitHubPromoter,
    PromoteResult,
    probe_materialized_greeting,
)
from guarded_heal.controller import (  # noqa: E402
    OpenCodeRunner,
    SelfHealController,
    classify_failure,
)
from guarded_heal.detector import (  # noqa: E402
    JournalctlAdapter,
    RuntimeDetector,
    parse_journal_line,
)


def require(cond: bool, msg: str) -> None:
    if not cond:
        raise AssertionError(msg)


GREETING_FN_SRC = '''
# HERMES_OPENCLOUD_TOOL_INTENT_V1
# HERMES_OPENCLOUD_GREETING_TOOL_CHOICE_NONE_V1
def _opencloud_is_conversational_greeting(text: str) -> bool:
    import re
    raw = (text or "").strip()
    if not raw or len(raw) > 80:
        return False
    if re.search(r"https?://|/\\S|\\b(search|find|browse|open|run|fix|deploy|cron)\\b", raw, re.I):
        return False
    if re.fullmatch(
        r"(?i)(hi|hello|hey|yo|sup|howdy|hiya|good\\s*(morning|afternoon|evening))"
        r"([,.!]+\\s*[A-Za-z]{0,24}|\\s+(bro|man|dude|buddy|pal|mate|fam|there|hermes|assistant|friend))?[.!]?",
        raw,
    ):
        return True
    if re.fullmatch(r"(?i)(hi|hello|hey)[.!]?\\s+(there|hermes|assistant|friend|bro|man|dude)[.!]?", raw):
        return True
    if re.fullmatch(r"(?i)(bro|dude|yo)[.!]?", raw):
        return True
    return False
'''


def _write_materialized(hermes: Path) -> None:
    agent = hermes / "agent"
    agent.mkdir(parents=True)
    (agent / "conversation_loop.py").write_text(GREETING_FN_SRC, encoding="utf-8")


class FakeJournal:
    def __init__(self, batches: list[tuple[list[str], str]]):
        self.batches = list(batches)
        self.calls = 0

    def read(self, *, cursor, since, units):
        self.calls += 1
        if not self.batches:
            return [], cursor
        lines, new_c = self.batches.pop(0)
        return lines, new_c


def test_parse_and_classify_fixture() -> None:
    require(parse_journal_line("DeprecationWarning: foo") is None, "warning ignored")
    require(parse_journal_line("optional dependency unavailable (optional)") is None, "optional")
    ev = parse_journal_line(
        "ERROR clarify tool: choices must be a list of strings chat_id=99 "
        "user=bob@x.com Bearer SECRETTOKEN12 /Users/bob/x"
    )
    require(ev is not None, "clarify detected")
    require("choices must be a list of strings" in ev.message, "clarify msg")
    require("bob@x.com" not in ev.message, "email sanitized before ingest")
    require("SECRETTOKEN" not in ev.message, "bearer sanitized")
    require("/Users/bob" not in ev.message, "home sanitized")
    c = classify_failure(ev.exc_type, ev.message, module=ev.module)
    require(c is not None and c.tier == 3 and c.severity == "MEDIUM", "clarify Tier3")

    to = parse_journal_line("httpx.ReadTimeout waiting for nvidia")
    require(to is not None, "timeout line")
    c2 = classify_failure(to.exc_type, to.message)
    require(c2 is not None and c2.tier == 2, "ReadTimeout Tier2")


def test_detector_cursor_no_dup_storm() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        state = Path(tmp) / "state"
        repo = Path(tmp) / "repo"
        repo.mkdir()
        ctrl = SelfHealController(
            repo_root=repo,
            state_root=state,
            fleet=FleetAdapter(
                dry_invoke=lambda: ActionResult(
                    status="NO_ACTION_TRANSIENT", detail="ok", verified=True
                )
            ),
            test_mode=True,
        )
        line = "ValueError: clarify tool: choices must be a list of strings"
        journal = FakeJournal(
            [
                ([line, "DeprecationWarning: x"], "cursor-1"),
                ([line], "cursor-2"),  # same failure again after cursor
            ]
        )
        det = RuntimeDetector(
            state_root=state, ingest_fn=ctrl.ingest, journal=journal
        )
        r1 = det.detect(auto_run=False)
        require(r1["matched"] == 1, f"matched {r1}")
        require(r1["ingested"] == 1, "first ingest")
        require(det.load_cursor() == "cursor-1", "cursor persisted")
        incidents = ctrl.store.list_incidents()
        require(len(incidents) == 1, "one incident")
        inc_id = incidents[0]["id"]

        r2 = det.detect(auto_run=False)
        require(r2["matched"] == 1, "second match")
        # Dedup: bump occurrence, no new storm incident.
        require(len(ctrl.store.list_incidents()) == 1, "no duplicate incident")
        bumped = ctrl.store.get(inc_id)
        require(int(bumped["occurrence_count"]) >= 2, "occurrence bumped")
        require(det.load_cursor() == "cursor-2", "cursor advanced")


def test_detector_restart_no_full_replay() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        state = Path(tmp) / "state"
        repo = Path(tmp) / "repo"
        repo.mkdir()
        ctrl = SelfHealController(repo_root=repo, state_root=state, test_mode=True)
        journal = FakeJournal([([], "boot-cursor")])
        det = RuntimeDetector(
            state_root=state, ingest_fn=ctrl.ingest, journal=journal
        )
        det.detect(auto_run=False)
        require(det.load_cursor() == "boot-cursor", "first-start cursor")
        # Simulate process restart: new detector, same cursor file.
        journal2 = FakeJournal(
            [(["DeprecationWarning: noise"], "next-cursor")]
        )
        det2 = RuntimeDetector(
            state_root=state, ingest_fn=ctrl.ingest, journal=journal2
        )
        require(det2.load_cursor() == "boot-cursor", "reload cursor")
        det2.detect(auto_run=False)
        require(len(ctrl.store.list_incidents()) == 0, "warnings not incidents")


def test_journalctl_argv_shell_false() -> None:
    calls = []

    def runner(argv, **kwargs):
        calls.append((argv, kwargs))

        class R:
            returncode = 0
            stdout = "-- cursor: abc\n"
            stderr = ""

        return R()

    ad = JournalctlAdapter(runner=runner)
    ad.read(cursor="prev", since="2 min ago", units=("hermes-gateway.service",))
    argv, kwargs = calls[0]
    require(kwargs.get("shell") is False, "shell=False")
    require(argv[0] == "journalctl", "journalctl")
    require("--after-cursor" in argv, "after cursor")
    require("-u" in argv and "hermes-gateway.service" in argv, "unit")


def test_post_deploy_canary_greeting() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        hermes = Path(tmp) / "hermes-agent"
        _write_materialized(hermes)
        ok, detail = probe_materialized_greeting(hermes)
        require(ok, detail)
        require("POST_DEPLOY_RUNTIME_CANARY" in detail, "kind tagged")

        # Break vocative path → fail
        bad = hermes / "agent" / "conversation_loop.py"
        bad.write_text(
            bad.read_text(encoding="utf-8").replace("bro|man|dude", "xyz"),
            encoding="utf-8",
        )
        ok2, _ = probe_materialized_greeting(hermes)
        require(not ok2, "broken classifier fails")


def test_canary_kinds_and_e2e_paths() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        state = Path(tmp) / "state"
        repo = Path(tmp) / "repo"
        repo.mkdir()
        subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
        subprocess.run(
            ["git", "config", "user.email", "t@example.com"],
            cwd=repo,
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "config", "user.name", "t"],
            cwd=repo,
            check=True,
            capture_output=True,
        )
        (repo / "README").write_text("x\n", encoding="utf-8")
        hermes = repo / "integrations" / "hermes"
        hermes.mkdir(parents=True)
        src_patch = ROOT / "integrations/hermes/hermes-product-reliability-ux.patch"
        (hermes / "hermes-product-reliability-ux.patch").write_text(
            src_patch.read_text(encoding="utf-8"), encoding="utf-8"
        )
        tdir = repo / "tests" / "reliability"
        tdir.mkdir(parents=True)
        (tdir / "product-reliability-ux.py").write_text(
            (ROOT / "tests/reliability/product-reliability-ux.py").read_text(
                encoding="utf-8"
            ),
            encoding="utf-8",
        )
        subprocess.run(["git", "add", "-A"], cwd=repo, check=True, capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", "seed"], cwd=repo, check=True, capture_output=True
        )
        base = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=repo, text=True
        ).strip()
        fake = ROOT / "tests/reliability/fixtures/fake-opencode"
        os.environ["OPEN_CLOUD_SELF_HEAL_FAKE_REVIEW"] = "APPROVE"
        os.environ["OPEN_CLOUD_SELF_HEAL_MODELS"] = "opencode/fake,opencode/fake2"
        os.environ["OPEN_CLOUD_SELF_HEAL_ALLOW_MEDIUM_AUTOMERGE"] = "1"

        def promote_ok(*_a, **_k):
            return PromoteResult(
                status="PROMOTED",
                detail="merged",
                gh_auth_ok=True,
                pushed=True,
                checks_passed=True,
                merged=True,
                merged_head="abc123merged",
                merged_tree="tree123",
                repair_branch="self-heal/x",
                repair_commit="rep01",
                pr_number=42,
            )

        # Success: active+greeting+health → RECOVERED
        ctrl = SelfHealController(
            repo_root=repo,
            state_root=state,
            opencode=OpenCodeRunner(opencode_bin=str(fake), timeout=120),
            validate_cmd=lambda _w: (True, "ok"),
            promoter=GitHubPromoter(dry_invoke=promote_ok),
            deployer=DeployAdapter(
                repo_root=repo,
                dry_invoke=lambda sha, *, quarantined: DeployResult(
                    status="DEPLOYED",
                    detail="ok",
                    previous_head=base,
                    previous_tree="prevtree",
                    deployed_head=sha,
                    deployed_tree="tree123",
                ),
            ),
            canary=CanaryAdapter(dry_invoke=lambda _k: (True, "ok")),
            test_mode=True,
        )
        row = ctrl.ingest(
            "ValueError",
            "clarify tool choices must be a list of strings",
            module="tools.clarify",
            auto_run=True,
        )
        require(row["state"] == "RECOVERED", f"got {row['state']}")
        require(
            "POST_DEPLOY_RUNTIME_CANARY" in str(row["meta"].get("post_canary", "")),
            "post canary kind",
        )

        # Active but greeting FAIL → rollback
        state2 = Path(tmp) / "state2"
        calls = {"n": 0}

        def dry_deploy(sha, *, quarantined):
            calls["n"] += 1
            if calls["n"] == 1:
                return DeployResult(
                    status="DEPLOYED",
                    detail="ok",
                    previous_head=base,
                    previous_tree="prevtree",
                    deployed_head=sha,
                    deployed_tree="tree123",
                )
            return DeployResult(
                status="ROLLED_BACK",
                detail="restored",
                previous_head=sha,
                deployed_head=base,
                deployed_tree="prevtree",
            )

        ctrl2 = SelfHealController(
            repo_root=repo,
            state_root=state2,
            opencode=OpenCodeRunner(opencode_bin=str(fake), timeout=120),
            validate_cmd=lambda _w: (True, "ok"),
            promoter=GitHubPromoter(dry_invoke=promote_ok),
            deployer=DeployAdapter(repo_root=repo, dry_invoke=dry_deploy),
            canary=CanaryAdapter(
                dry_invoke=lambda k: (False, "greeting fail")
                if k == "post"
                else (True, "pre ok")
            ),
            test_mode=True,
        )
        row2 = ctrl2.ingest(
            "ValueError",
            "clarify tool choices must be a list of strings",
            module="tools.clarify",
            auto_run=True,
        )
        require(row2["state"] == "ROLLED_BACK", f"got {row2['state']}")
        require(ctrl2.store.is_quarantined("abc123merged"), "quarantined")


def test_rollback_failure_modes() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        repo = Path(tmp) / "repo"
        repo.mkdir()
        subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
        subprocess.run(
            ["git", "config", "user.email", "t@example.com"],
            cwd=repo,
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "config", "user.name", "t"],
            cwd=repo,
            check=True,
            capture_output=True,
        )
        (repo / "README").write_text("x\n", encoding="utf-8")
        subprocess.run(["git", "add", "README"], cwd=repo, check=True, capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", "seed"], cwd=repo, check=True, capture_output=True
        )
        base = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=repo, text=True
        ).strip()
        base_tree = subprocess.check_output(
            ["git", "rev-parse", f"{base}^{{tree}}"], cwd=repo, text=True
        ).strip()
        (repo / "README").write_text("y\n", encoding="utf-8")
        subprocess.run(["git", "add", "README"], cwd=repo, check=True, capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", "bad"], cwd=repo, check=True, capture_output=True
        )

        mat = Path(tmp) / "mat.sh"
        mat.write_text("#!/bin/bash\nexit 1\n", encoding="utf-8")
        mat.chmod(0o755)

        # Materialize fail → NOT ROLLED_BACK
        deployer = DeployAdapter(repo_root=repo, materialize_script=mat)

        def runner_mat(argv, **kwargs):
            if argv[:1] == ["bash"] and str(mat) in argv:
                class R:
                    returncode = 1
                    stdout = ""
                    stderr = "mat fail"

                return R()
            return subprocess.run(
                argv, capture_output=True, text=True, timeout=kwargs.get("timeout", 60),
                shell=False, cwd=kwargs.get("cwd"), env=kwargs.get("env"),
            )

        deployer._runner = runner_mat
        # Bypass real systemctl by injecting failures after mat — mat fails first.
        rb = deployer.rollback(base, previous_tree=base_tree, post_canary=lambda: (True, "ok"))
        require(rb.status == "ROLLBACK_FAILED", f"mat → {rb.status}")
        require(rb.status != "ROLLED_BACK", "not false success")

        # Restart exit 1
        mat_ok = Path(tmp) / "mat_ok.sh"
        mat_ok.write_text("#!/bin/bash\nexit 0\n", encoding="utf-8")
        mat_ok.chmod(0o755)

        def runner_restart(argv, **kwargs):
            if argv[:2] == ["systemctl", "--user"] and "restart" in argv:
                class R:
                    returncode = 1
                    stdout = ""
                    stderr = "fail"

                return R()
            if argv[:1] == ["bash"]:
                class R:
                    returncode = 0
                    stdout = ""
                    stderr = ""

                return R()
            return subprocess.run(
                argv, capture_output=True, text=True, timeout=kwargs.get("timeout", 60),
                shell=False, cwd=kwargs.get("cwd"), env=kwargs.get("env"),
            )

        # Reset repo to "bad" tip again for next rollback attempt
        subprocess.run(
            ["git", "-C", str(repo), "reset", "--hard", "HEAD"],
            check=True,
            capture_output=True,
        )
        d2 = DeployAdapter(repo_root=repo, materialize_script=mat_ok, runner=runner_restart)
        # Need a distinct bad commit still; after first rollback HEAD may be base.
        # Re-create bad tip:
        if subprocess.check_output(["git", "-C", str(repo), "rev-parse", "HEAD"], text=True).strip() == base:
            (repo / "README").write_text("z\n", encoding="utf-8")
            subprocess.run(["git", "add", "README"], cwd=repo, check=True, capture_output=True)
            subprocess.run(
                ["git", "commit", "-m", "bad2"], cwd=repo, check=True, capture_output=True
            )
        rb2 = d2.rollback(base, previous_tree=base_tree)
        require(rb2.status == "ROLLBACK_FAILED", f"restart → {rb2.status}")

        # Inactive gateway
        def runner_inactive(argv, **kwargs):
            if argv[:2] == ["systemctl", "--user"] and "restart" in argv:
                class R:
                    returncode = 0
                    stdout = ""
                    stderr = ""

                return R()
            if argv[:2] == ["systemctl", "--user"] and "is-active" in argv:
                class R:
                    returncode = 3
                    stdout = "inactive\n"
                    stderr = ""

                return R()
            if argv[:1] == ["bash"]:
                class R:
                    returncode = 0
                    stdout = ""
                    stderr = ""

                return R()
            return subprocess.run(
                argv, capture_output=True, text=True, timeout=kwargs.get("timeout", 60),
                shell=False, cwd=kwargs.get("cwd"), env=kwargs.get("env"),
            )

        if subprocess.check_output(["git", "-C", str(repo), "rev-parse", "HEAD"], text=True).strip() == base:
            (repo / "README").write_text("w\n", encoding="utf-8")
            subprocess.run(["git", "add", "README"], cwd=repo, check=True, capture_output=True)
            subprocess.run(
                ["git", "commit", "-m", "bad3"], cwd=repo, check=True, capture_output=True
            )
        d3 = DeployAdapter(repo_root=repo, materialize_script=mat_ok, runner=runner_inactive)
        rb3 = d3.rollback(base, previous_tree=base_tree)
        require(rb3.status == "ROLLBACK_FAILED", f"inactive → {rb3.status}")

        # Post-rollback canary fail
        def runner_ok(argv, **kwargs):
            if argv[:2] == ["systemctl", "--user"]:
                class R:
                    returncode = 0
                    stdout = "active\n"
                    stderr = ""

                return R()
            if argv[:1] == ["bash"]:
                class R:
                    returncode = 0
                    stdout = ""
                    stderr = ""

                return R()
            return subprocess.run(
                argv, capture_output=True, text=True, timeout=kwargs.get("timeout", 60),
                shell=False, cwd=kwargs.get("cwd"), env=kwargs.get("env"),
            )

        if subprocess.check_output(["git", "-C", str(repo), "rev-parse", "HEAD"], text=True).strip() == base:
            (repo / "README").write_text("v\n", encoding="utf-8")
            subprocess.run(["git", "add", "README"], cwd=repo, check=True, capture_output=True)
            subprocess.run(
                ["git", "commit", "-m", "bad4"], cwd=repo, check=True, capture_output=True
            )
        d4 = DeployAdapter(repo_root=repo, materialize_script=mat_ok, runner=runner_ok)
        rb4 = d4.rollback(
            base,
            previous_tree=base_tree,
            post_canary=lambda: (False, "canary fail"),
        )
        require(rb4.status == "ROLLBACK_FAILED", f"canary → {rb4.status}")

        # TREE mismatch
        if subprocess.check_output(["git", "-C", str(repo), "rev-parse", "HEAD"], text=True).strip() == base:
            (repo / "README").write_text("u\n", encoding="utf-8")
            subprocess.run(["git", "add", "README"], cwd=repo, check=True, capture_output=True)
            subprocess.run(
                ["git", "commit", "-m", "bad5"], cwd=repo, check=True, capture_output=True
            )
        d5 = DeployAdapter(repo_root=repo, materialize_script=mat_ok, runner=runner_ok)
        rb5 = d5.rollback(base, previous_tree="deadbeef" * 5)
        require(rb5.status == "ROLLBACK_FAILED", f"tree → {rb5.status}")


def test_controller_rollback_failed_critical() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        state = Path(tmp) / "state"
        repo = Path(tmp) / "repo"
        repo.mkdir()
        subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
        subprocess.run(
            ["git", "config", "user.email", "t@example.com"],
            cwd=repo,
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "config", "user.name", "t"],
            cwd=repo,
            check=True,
            capture_output=True,
        )
        (repo / "README").write_text("x\n", encoding="utf-8")
        hermes = repo / "integrations" / "hermes"
        hermes.mkdir(parents=True)
        src_patch = ROOT / "integrations/hermes/hermes-product-reliability-ux.patch"
        (hermes / "hermes-product-reliability-ux.patch").write_text(
            src_patch.read_text(encoding="utf-8"), encoding="utf-8"
        )
        tdir = repo / "tests" / "reliability"
        tdir.mkdir(parents=True)
        (tdir / "product-reliability-ux.py").write_text(
            (ROOT / "tests/reliability/product-reliability-ux.py").read_text(
                encoding="utf-8"
            ),
            encoding="utf-8",
        )
        subprocess.run(["git", "add", "-A"], cwd=repo, check=True, capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", "seed"], cwd=repo, check=True, capture_output=True
        )
        base = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=repo, text=True
        ).strip()
        fake = ROOT / "tests/reliability/fixtures/fake-opencode"
        os.environ["OPEN_CLOUD_SELF_HEAL_FAKE_REVIEW"] = "APPROVE"
        os.environ["OPEN_CLOUD_SELF_HEAL_MODELS"] = "opencode/fake,opencode/fake2"
        os.environ["OPEN_CLOUD_SELF_HEAL_ALLOW_MEDIUM_AUTOMERGE"] = "1"

        def promote_ok(*_a, **_k):
            return PromoteResult(
                status="PROMOTED",
                detail="merged",
                gh_auth_ok=True,
                pushed=True,
                checks_passed=True,
                merged=True,
                merged_head="failsha01",
                merged_tree="tree123",
                repair_branch="self-heal/x",
                repair_commit="rep01",
                pr_number=7,
            )

        calls = {"n": 0}

        def dry_deploy(sha, *, quarantined):
            calls["n"] += 1
            if calls["n"] == 1:
                return DeployResult(
                    status="DEPLOYED",
                    detail="ok",
                    previous_head=base,
                    previous_tree="prevtree",
                    deployed_head=sha,
                    deployed_tree="tree123",
                )
            return DeployResult(
                status="ROLLBACK_FAILED",
                detail="materialize failed",
                previous_head=sha,
                deployed_head=base,
            )

        ctrl = SelfHealController(
            repo_root=repo,
            state_root=state,
            opencode=OpenCodeRunner(opencode_bin=str(fake), timeout=120),
            validate_cmd=lambda _w: (True, "ok"),
            promoter=GitHubPromoter(dry_invoke=promote_ok),
            deployer=DeployAdapter(repo_root=repo, dry_invoke=dry_deploy),
            canary=CanaryAdapter(
                dry_invoke=lambda k: (False, "post fail")
                if k == "post"
                else (True, "pre")
            ),
            test_mode=True,
        )
        row = ctrl.ingest(
            "ValueError",
            "clarify tool choices must be a list of strings",
            module="tools.clarify",
            auto_run=True,
        )
        require(row["state"] == "ROLLBACK_FAILED", f"got {row['state']}")
        require(row["state"] != "ROLLED_BACK", "not false success")
        require(ctrl.store.is_quarantined("failsha01"), "quarantined before/during")
        require(row["meta"].get("severity_escalation") == "CRITICAL", "CRITICAL")
        require(row["meta"].get("human_required") is True, "HUMAN_REQUIRED flag")


def test_fixture_auto_detect_no_cli_ingest() -> None:
    """Clarify journal line → APPLICATION_DEFECT path via detector alone."""
    with tempfile.TemporaryDirectory() as tmp:
        state = Path(tmp) / "state"
        repo = Path(tmp) / "repo"
        repo.mkdir()
        ctrl = SelfHealController(repo_root=repo, state_root=state, test_mode=True)
        journal = FakeJournal(
            [
                (
                    [
                        "2026-08-22 ValueError: clarify tool: choices must be a list of strings"
                    ],
                    "c1",
                )
            ]
        )
        det = RuntimeDetector(
            state_root=state, ingest_fn=ctrl.ingest, journal=journal
        )
        # auto_run=False: prove detection alone creates CLASSIFIED/CAPTURED incident
        # without CLI ingest.
        r = det.detect(auto_run=False)
        require(r["ingested"] == 1, "auto-detected")
        row = ctrl.store.list_incidents()[0]
        require(row["tier"] == 3, "Tier 3")
        require(row["severity"] == "MEDIUM", "MEDIUM")
        require("clarify" in row["title"].lower(), "title")


def main() -> None:
    test_parse_and_classify_fixture()
    test_detector_cursor_no_dup_storm()
    test_detector_restart_no_full_replay()
    test_journalctl_argv_shell_false()
    test_post_deploy_canary_greeting()
    test_canary_kinds_and_e2e_paths()
    test_rollback_failure_modes()
    test_controller_rollback_failed_critical()
    test_fixture_auto_detect_no_cli_ingest()
    print("PASS detector parse/sanitize/classify (clarify Tier3, ReadTimeout Tier2)")
    print("PASS detector cursor dedup + restart (no storm / no full replay)")
    print("PASS journalctl argv shell=False")
    print("PASS materialized greeting canary probe")
    print("PASS post-deploy E2E RECOVERED + greeting-fail rollback")
    print("PASS rollback failure modes ≠ ROLLED_BACK")
    print("PASS controller ROLLBACK_FAILED+CRITICAL+QUARANTINED")
    print("PASS fixture clarify auto-detect without CLI ingest")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"FAIL {exc}", file=sys.stderr)
        import traceback

        traceback.print_exc()
        sys.exit(1)
