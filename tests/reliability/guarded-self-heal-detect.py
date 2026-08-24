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
    P8RuntimeAdapter,
    PromoteResult,
    RuntimeServiceAdapter,
    probe_materialized_greeting,
)
from guarded_heal.controller import (  # noqa: E402
    OpenCodeRunner,
    SelfHealController,
    classify_failure,
)
from guarded_heal.detector import (  # noqa: E402
    GatewayLifecycle,
    JournalctlAdapter,
    RuntimeDetector,
    parse_journal_line,
)

def require(cond: bool, msg: str) -> None:
    if not cond:
        raise AssertionError(msg)


# ponytail: raw string so regex \s/\b match the live patch; do not double-escape.
GREETING_FN_SRC = r'''
# HERMES_OPENCLOUD_TOOL_INTENT_V1
# HERMES_OPENCLOUD_GREETING_TOOL_CHOICE_NONE_V1
def _opencloud_is_conversational_greeting(text: str) -> bool:
    import re
    raw = (text or "").strip()
    if not raw or len(raw) > 80:
        return False
    if re.search(r"https?://|/\S|\b(search|find|browse|open|run|fix|deploy|cron)\b", raw, re.I):
        return False
    if re.fullmatch(
        r"(?i)(hi|hello|hey|yo|sup|howdy|hiya|good\s*(morning|afternoon|evening))"
        r"([,.!]+\s*[A-Za-z]{0,24}|\s+(bro|man|dude|buddy|pal|mate|fam|there|hermes|assistant|friend))?[.!]?",
        raw,
    ):
        return True
    if re.fullmatch(r"(?i)(hi|hello|hey)[.!]?\s+(there|hermes|assistant|friend|bro|man|dude)[.!]?", raw):
        return True
    if re.fullmatch(r"(?i)(bro|dude|yo)[.!]?", raw):
        return True
    if re.fullmatch(
        r"(?i)((macha|anna|bro|dude|man|buddy|pal|mate|fam|hey|hi|hello|yo)\s+)?"
        r"(you\s+there|u\s+there|there\s*\?|you\s+around|you\s+up|are\s+you\s+(there|around|up|awake|online))"
        r"(\s+(da|daa|bro|man|dude|aa|aaa))?[.!?\s]*",
        raw,
    ):
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

    goc = classify_failure(
        "OpenCloudUserOutputContractViolation",
        "reason=greeting_tool_text",
        module="agent.conversation_loop",
        context="provider=nvidia model=llama-3.2-11b-vision",
    )
    require(goc is not None and goc.tier == 3, "greeting contract Tier3")
    require(goc.reason == "greeting_output_contract", "greeting contract reason")
    require(goc.severity == "MEDIUM", "greeting contract MEDIUM")

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
        # Detector is always queue-only: QUEUED, never RECOVERING / process.
        r = det.detect(auto_run=False)
        require(r["ingested"] == 1, "auto-detected")
        require(r.get("auto_run") is False, "detector never recovers")
        row = ctrl.store.list_incidents()[0]
        require(row["tier"] == 3, "Tier 3")
        require(row["severity"] == "MEDIUM", "MEDIUM")
        require(row["state"] == "QUEUED", f"queued not recovering: {row['state']}")
        require("clarify" in row["title"].lower(), "title")
        # CRITICAL: detector must not invoke OpenCode (count stays 0 until controller).
        require(row["state"] != "RECOVERING", "detector ≠ RECOVERING")


# Exact intentional systemd restart journal from production OCI pattern.
INTENTIONAL_RESTART_JOURNAL = [
    "Aug 23 02:10:01 Stopping hermes-gateway.service - Hermes Gateway...",
    "Aug 23 02:10:01 hermes-gateway.service: Sending SIGTERM to main process pid=1234.",
    "Aug 23 02:10:01 hermes-gateway.service: Main process exited, code=exited, status=1/FAILURE",
    "Aug 23 02:10:01 hermes-gateway.service: Failed with result 'exit-code'.",
    "Aug 23 02:10:01 Stopped hermes-gateway.service - Hermes Gateway.",
    "Aug 23 02:10:02 Started hermes-gateway.service - Hermes Gateway.",
]

GENUINE_SEGV_JOURNAL = [
    "Aug 23 03:00:01 hermes-gateway.service: Main process exited, code=dumped, status=11/SEGV",
    "Aug 23 03:00:01 hermes-gateway.service: Failed with result 'core-dump'.",
    "Aug 23 03:00:01 segfault at 0 ip ... sp ... error 4 in hermes",
]


def test_intentional_restart_zero_crash() -> None:
    """Fixture 16: exact intentional restart → crash=0, no Tier1, no repair."""
    with tempfile.TemporaryDirectory() as tmp:
        state = Path(tmp) / "state"
        repo = Path(tmp) / "repo"
        repo.mkdir()
        p8_calls = {"n": 0}

        def p8_dry(task, fp):
            p8_calls["n"] += 1
            return ActionResult(status="RECOVERED", detail="should_not_run", verified=True)

        runtime_calls = {"n": 0}

        def runtime_dry(kind, reason):
            runtime_calls["n"] += 1
            return ActionResult(status="RECOVERED", detail="should_not_run", verified=True)

        ctrl = SelfHealController(
            repo_root=repo,
            state_root=state,
            p8=P8RuntimeAdapter(dry_invoke=p8_dry),
            runtime=RuntimeServiceAdapter(dry_invoke=runtime_dry),
            test_mode=True,
        )
        journal = FakeJournal([(INTENTIONAL_RESTART_JOURNAL, "c-restart")])
        det = RuntimeDetector(
            state_root=state, ingest_fn=ctrl.ingest, journal=journal
        )
        r = det.detect()
        require(r["matched"] == 0, f"matched crash={r['matched']}")
        require(r["ingested"] == 0, "no incidents")
        require(len(ctrl.store.list_incidents()) == 0, "no Tier1")
        require(p8_calls["n"] == 0, "no hermes-code-repair / P8")
        require(runtime_calls["n"] == 0, "no runtime restart")
        # Lifecycle file persisted then cleared after Started.
        lc = GatewayLifecycle(state / "gateway-lifecycle.json")
        require(lc.controlled is False, "lifecycle cleared after Started")


def test_genuine_segv_tier1_runtime_no_p8() -> None:
    """Fixture 17: SEGV → Tier1 queued; controller runtime restart → RECOVERED; no P8."""
    with tempfile.TemporaryDirectory() as tmp:
        state = Path(tmp) / "state"
        repo = Path(tmp) / "repo"
        repo.mkdir()
        p8_calls = {"n": 0}
        runtime_calls = {"n": 0}

        def p8_dry(task, fp):
            p8_calls["n"] += 1
            return ActionResult(status="RECOVERED", detail="p8", verified=True)

        def runtime_dry(kind, reason):
            runtime_calls["n"] += 1
            require(kind == "crash", f"kind={kind}")
            return ActionResult(
                status="RECOVERED", detail="runtime_restart_verified", verified=True
            )

        ctrl = SelfHealController(
            repo_root=repo,
            state_root=state,
            p8=P8RuntimeAdapter(dry_invoke=p8_dry),
            runtime=RuntimeServiceAdapter(dry_invoke=runtime_dry),
            test_mode=True,
        )
        journal = FakeJournal([(GENUINE_SEGV_JOURNAL, "c-segv")])
        det = RuntimeDetector(
            state_root=state, ingest_fn=ctrl.ingest, journal=journal
        )
        r = det.detect()
        require(r["matched"] >= 1, f"matched={r}")
        require(r["ingested"] >= 1, "queued")
        row = ctrl.store.list_incidents()[0]
        require(row["tier"] == 1, f"tier {row['tier']}")
        require(row["state"] == "QUEUED", f"state {row['state']}")
        require(p8_calls["n"] == 0, "detector must not call P8")

        processed = ctrl.process_queue(limit=1)
        require(len(processed) == 1, "controller processed")
        final = ctrl.store.get(row["id"])
        require(final["state"] == "RECOVERED", f"got {final['state']}")
        require(final["meta"].get("p8_used") is False, "no P8")
        require(final["meta"].get("hermes_code_repair") is False, "no repair")
        require(runtime_calls["n"] == 1, "runtime adapter once")
        require(p8_calls["n"] == 0, "still no P8 after controller")


def test_unexpected_crash_already_active() -> None:
    """Fixture 18: unexpected crash but service active → NO_ACTION_TRANSIENT, no restart."""
    with tempfile.TemporaryDirectory() as tmp:
        state = Path(tmp) / "state"
        repo = Path(tmp) / "repo"
        repo.mkdir()
        restarts = {"n": 0}

        def runner(argv, **kwargs):
            class R:
                returncode = 0
                stdout = ""
                stderr = ""

            if argv[:2] == ["systemctl", "--user"] and "is-active" in argv:
                R.stdout = "active\n"
                return R()
            if argv[:2] == ["systemctl", "--user"] and "restart" in argv:
                restarts["n"] += 1
                return R()
            return R()

        ctrl = SelfHealController(
            repo_root=repo,
            state_root=state,
            runtime=RuntimeServiceAdapter(runner=runner, wait_seconds=0),
            p8=P8RuntimeAdapter(
                dry_invoke=lambda *_a: ActionResult(
                    status="FAILED", detail="must_not_use_p8", verified=False
                )
            ),
            test_mode=True,
        )
        row = ctrl.ingest(
            "RuntimeError",
            "hermes-gateway crash or abnormal exit",
            module="hermes-gateway",
            auto_run=True,
        )
        require(
            row["state"] == "NO_ACTION_TRANSIENT",
            f"got {row['state']}",
        )
        require(restarts["n"] == 0, "no unnecessary restart")
        require(row["meta"].get("hermes_code_repair") is False, "no repair")


def test_clarify_detector_queues_opencode_zero() -> None:
    """Fixture 19: Tier3 clarify — detector queues only; OpenCode count=0."""
    with tempfile.TemporaryDirectory() as tmp:
        state = Path(tmp) / "state"
        repo = Path(tmp) / "repo"
        repo.mkdir()
        oc_calls = {"n": 0}

        def oc_runner(argv, **kwargs):
            oc_calls["n"] += 1

            class R:
                returncode = 0
                stdout = "ok"
                stderr = ""

            return R()

        ctrl = SelfHealController(
            repo_root=repo,
            state_root=state,
            opencode=OpenCodeRunner(
                opencode_bin="/bin/echo", runner=oc_runner
            ),
            test_mode=True,
        )
        journal = FakeJournal(
            [
                (
                    [
                        "ValueError: clarify tool: choices must be a list of strings"
                    ],
                    "c-clarify",
                )
            ]
        )
        det = RuntimeDetector(
            state_root=state, ingest_fn=ctrl.ingest, journal=journal
        )
        det.detect()
        require(oc_calls["n"] == 0, "OpenCode count=0 on detect")
        row = ctrl.store.list_incidents()[0]
        require(row["state"] == "QUEUED", "queued")
        require(row["tier"] == 3, "tier3")
        # Controller later may OpenCode — that is not this test's claim.


def test_stale_recovering_lease_and_legacy() -> None:
    """Stale lease + legacy RECOVERING without lease → not RECOVERED."""
    with tempfile.TemporaryDirectory() as tmp:
        state = Path(tmp) / "state"
        repo = Path(tmp) / "repo"
        repo.mkdir()
        ctrl = SelfHealController(
            repo_root=repo, state_root=state, test_mode=True, worker_id="w1"
        )
        # Legacy production-style RECOVERING (no lease meta).
        legacy = ctrl.store.create(
            signature="sig-legacy",
            title="stuck",
            sanitized_task="x",
            severity="HIGH",
            tier=1,
            meta={"reason": "gateway_crash"},
        )
        ctrl.store.transition(legacy["id"], "RECOVERING", detail="legacy no lease")
        # Expired lease
        leased = ctrl.store.create(
            signature="sig-lease",
            title="stuck2",
            sanitized_task="x",
            severity="HIGH",
            tier=1,
            meta={"reason": "gateway_crash"},
        )
        ctrl.store.transition(
            leased["id"],
            "RECOVERING",
            detail="expired",
            meta_update={
                "worker_id": "old",
                "worker_started_at": 1.0,
                "lease_expires_at": 2.0,  # long expired
            },
        )
        reaped = ctrl.reap_stale_leases()
        require(len(reaped) >= 2, f"reaped {len(reaped)}")
        for iid in (legacy["id"], leased["id"]):
            row = ctrl.store.get(iid)
            require(row["state"] != "RECOVERED", f"{iid} not false recovered")
            require(
                row["state"] in ("QUEUED", "RETRY_PENDING", "HUMAN_REQUIRED", "INTERRUPTED"),
                f"{iid} → {row['state']}",
            )


def test_failed_dedup_no_storm() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        state = Path(tmp) / "state"
        repo = Path(tmp) / "repo"
        repo.mkdir()
        ctrl = SelfHealController(
            repo_root=repo,
            state_root=state,
            runtime=RuntimeServiceAdapter(
                dry_invoke=lambda *_a: ActionResult(
                    status="FAILED", detail="boom", verified=False
                )
            ),
            test_mode=True,
        )
        r1 = ctrl.ingest(
            "RuntimeError",
            "hermes-gateway crash or abnormal exit",
            module="hermes-gateway",
            auto_run=True,
        )
        require(r1["state"] == "FAILED", r1["state"])
        r2 = ctrl.ingest(
            "RuntimeError",
            "hermes-gateway crash or abnormal exit",
            module="hermes-gateway",
            auto_run=False,
        )
        require(len(ctrl.store.list_incidents()) == 1, "no storm")
        require(int(r2["occurrence_count"]) >= 2, "occurrence bumped")
        require(r2["state"] == "QUEUED", f"reopened {r2['state']}")


def test_detector_timeout_preserves_cursor() -> None:
    """Timeout → no recovery; cursor unchanged when journal returns None cursor."""
    with tempfile.TemporaryDirectory() as tmp:
        state = Path(tmp) / "state"
        repo = Path(tmp) / "repo"
        repo.mkdir()
        ctrl = SelfHealController(repo_root=repo, state_root=state, test_mode=True)
        det = RuntimeDetector(
            state_root=state, ingest_fn=ctrl.ingest, journal=FakeJournal([])
        )
        det.save_cursor("keep-me")
        # Empty batches with no new cursor simulation:
        class TimeoutJournal:
            def read(self, *, cursor, since, units):
                return [], None  # timeout / fail → do not advance

        det.journal = TimeoutJournal()
        r = det.detect()
        require(r["cursor_advanced"] is False, "no advance")
        require(det.load_cursor() == "keep-me", "cursor preserved")
        require(len(ctrl.store.list_incidents()) == 0, "no recovery invent")


def test_systemd_bounds_present() -> None:
    detect = (ROOT / "services/systemd/opencloud-self-heal-detect.service").read_text(
        encoding="utf-8"
    )
    ctrl = (ROOT / "services/systemd/opencloud-self-heal.service").read_text(
        encoding="utf-8"
    )
    require("RuntimeMaxSec=25" in detect, "detect RuntimeMaxSec")
    require("TimeoutStartSec=25" in detect, "detect TimeoutStartSec")
    require("RuntimeMaxSec=25min" in ctrl, "controller RuntimeMaxSec")
    require("TimeoutStartSec=25min" in ctrl, "controller TimeoutStartSec")


def test_generic_typeerror_tier1_fail_closed_no_p8() -> None:
    """Generic TypeError Tier-1: detector queues → controller HUMAN_REQUIRED; P8=0."""
    with tempfile.TemporaryDirectory() as tmp:
        state = Path(tmp) / "state"
        repo = Path(tmp) / "repo"
        repo.mkdir()
        p8_calls = {"n": 0}
        runtime_calls = {"n": 0}

        def p8_dry(task, fp):
            p8_calls["n"] += 1
            return ActionResult(status="RECOVERED", detail="must_not_run", verified=True)

        def runtime_dry(kind, reason):
            runtime_calls["n"] += 1
            return ActionResult(status="RECOVERED", detail="must_not_run", verified=True)

        ctrl = SelfHealController(
            repo_root=repo,
            state_root=state,
            p8=P8RuntimeAdapter(dry_invoke=p8_dry),
            runtime=RuntimeServiceAdapter(dry_invoke=runtime_dry),
            test_mode=True,
        )
        journal = FakeJournal(
            [(["TypeError: foo() missing 1 required positional argument: 'bar'"], "c-type")]
        )
        det = RuntimeDetector(
            state_root=state, ingest_fn=ctrl.ingest, journal=journal
        )
        r = det.detect()
        require(r["ingested"] >= 1, f"queued {r}")
        row = ctrl.store.list_incidents()[0]
        require(row["tier"] == 1, f"tier {row['tier']}")
        require(row["state"] == "QUEUED", f"state {row['state']}")
        require((row.get("meta") or {}).get("reason") == "internal_code", "internal_code")
        require(p8_calls["n"] == 0, "detector must not call P8")
        require(runtime_calls["n"] == 0, "detector must not call runtime")

        processed = ctrl.process_queue(limit=1)
        require(len(processed) == 1, "controller processed")
        final = ctrl.store.get(row["id"])
        require(final["state"] == "HUMAN_REQUIRED", f"got {final['state']}")
        ev_detail = " ".join(
            (e.get("detail") or "") for e in ctrl.store.events(row["id"])
        )
        require(
            "unsupported_tier1_runtime_reason=internal_code" in ev_detail,
            f"events {ev_detail!r}",
        )
        require(final["meta"].get("p8_used") is False, "p8_used false")
        require(final["meta"].get("hermes_code_repair") is False, "no repair")
        require(p8_calls["n"] == 0, "hermes-code-repair / P8 count=0")
        require(runtime_calls["n"] == 0, "runtime count=0 for unsupported tier1")


def test_gateway_crash_never_hermes_code_repair() -> None:
    """Deterministic: gateway crash → hermes-code-repair invocation count = 0."""
    with tempfile.TemporaryDirectory() as tmp:
        state = Path(tmp) / "state"
        repo = Path(tmp) / "repo"
        repo.mkdir()
        repair_calls = {"n": 0}

        def fake_p8(task, fp):
            repair_calls["n"] += 1
            return ActionResult(status="RECOVERED", detail="bad", verified=True)

        ctrl = SelfHealController(
            repo_root=repo,
            state_root=state,
            p8=P8RuntimeAdapter(dry_invoke=fake_p8),
            runtime=RuntimeServiceAdapter(
                dry_invoke=lambda *_a: ActionResult(
                    status="RECOVERED", detail="ok", verified=True
                )
            ),
            test_mode=True,
        )
        row = ctrl.ingest(
            "RuntimeError",
            "hermes-gateway crash or abnormal exit",
            auto_run=True,
        )
        require(row["state"] == "RECOVERED", row["state"])
        require(repair_calls["n"] == 0, "hermes-code-repair must not run")
        require(row["meta"].get("p8_used") is False, "p8_used false")


def main() -> None:
    import gc

    def run_isolated(test_fn):
        try:
            test_fn()
        finally:
            # Reliability tests intentionally create many temporary SQLite,
            # subprocess, and filesystem resources. Force deterministic
            # collection between tests so macOS low soft FD limits do not
            # accumulate descriptors across the suite.
            gc.collect()

    for test_fn in (
        test_parse_and_classify_fixture,
        test_detector_cursor_no_dup_storm,
        test_detector_restart_no_full_replay,
        test_journalctl_argv_shell_false,
        test_post_deploy_canary_greeting,
        test_canary_kinds_and_e2e_paths,
        test_rollback_failure_modes,
        test_controller_rollback_failed_critical,
        test_fixture_auto_detect_no_cli_ingest,
        test_intentional_restart_zero_crash,
        test_genuine_segv_tier1_runtime_no_p8,
        test_unexpected_crash_already_active,
        test_clarify_detector_queues_opencode_zero,
        test_stale_recovering_lease_and_legacy,
        test_failed_dedup_no_storm,
        test_detector_timeout_preserves_cursor,
        test_systemd_bounds_present,
        test_generic_typeerror_tier1_fail_closed_no_p8,
        test_gateway_crash_never_hermes_code_repair,
    ):
        run_isolated(test_fn)
    print("PASS detector parse/sanitize/classify (clarify Tier3, ReadTimeout Tier2)")
    print("PASS detector cursor dedup + restart (no storm / no full replay)")
    print("PASS journalctl argv shell=False")
    print("PASS materialized greeting canary probe")
    print("PASS post-deploy E2E RECOVERED + greeting-fail rollback")
    print("PASS rollback failure modes ≠ ROLLED_BACK")
    print("PASS controller ROLLBACK_FAILED+CRITICAL+QUARANTINED")
    print("PASS fixture clarify auto-detect QUEUED (no CLI ingest)")
    print("PASS intentional restart → 0 crash / no Tier1 / no repair")
    print("PASS genuine SEGV → Tier1 runtime RECOVERED / no P8")
    print("PASS already-active crash → NO_ACTION_TRANSIENT")
    print("PASS clarify detect OpenCode=0 (queue only)")
    print("PASS stale/legacy RECOVERING lease reap")
    print("PASS FAILED dedup reopen (no storm)")
    print("PASS detect timeout preserves cursor")
    print("PASS systemd RuntimeMaxSec/TimeoutStartSec bounds")
    print("PASS generic TypeError Tier1 HUMAN_REQUIRED / P8=0")
    print("PASS gateway crash never hermes-code-repair")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"FAIL {exc}", file=sys.stderr)
        import traceback

        traceback.print_exc()
        sys.exit(1)
