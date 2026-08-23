#!/usr/bin/env python3
"""Deterministic coverage for guarded self-heal control plane."""

from __future__ import annotations

import json
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
    write_inbox_event,
)
from guarded_heal.controller import (  # noqa: E402
    OpenCodeRunner,
    SelfHealController,
    assert_safe_workdir,
    classify_failure,
    incident_signature,
    path_denied,
    sanitize_for_opencode,
    scan_diff_for_secrets,
)
from guarded_heal.store import IncidentStore  # noqa: E402


def require(cond: bool, msg: str) -> None:
    if not cond:
        raise AssertionError(msg)


def test_classify_and_sanitize() -> None:
    require(classify_failure("UserWarning", "deprecated") is None, "warnings ignored")
    c = classify_failure(
        "ValueError",
        "clarify tool: choices must be a list of strings",
        module="tools.clarify",
    )
    require(c is not None and c.tier == 3, "clarify schema → tier 3")
    require(c.severity == "MEDIUM", "clarify is MEDIUM")
    require("clarify" in c.title, "title")

    ext = classify_failure("APITimeoutError", "nvidia ReadTimeout")
    require(ext is not None and ext.tier == 2, "timeout → tier 2 fleet only")

    dirty = sanitize_for_opencode(
        "user=bob@example.com api_key=sk-secret phone=+1 555-123-4567 "
        "chat_id=999 Authorization: Bearer SHORT "
        "Cookie: session=xyz /Users/bob/secret?access_token=tok123"
    )
    require("sk-secret" not in dirty, "api key redacted")
    require("@example.com" not in dirty, "email redacted")
    require("555" not in dirty, "phone redacted")
    # SHORT is below public-audit length; ensure sanitizer still rewrites Bearer.
    require("Bearer [REDACTED]" in dirty or "Bearer SHORT" not in dirty, "bearer")
    require("/Users/bob" not in dirty, "home path redacted")
    require("tok123" not in dirty, "query token redacted")

    a = incident_signature("ValueError", "choices must be a list of strings")
    b = incident_signature("ValueError", "choices must be a list of strings")
    require(a == b, "signature stable")
    require("@" not in a and " " not in a, "signature compact")

    require(path_denied(".env") is True, "deny .env")
    require(path_denied("terraform/main.tf") is True, "deny terraform")
    require(path_denied(".aws/credentials") is True, "deny aws")
    require(path_denied("oci_api_key.pem") is True, "deny oci key")
    require(path_denied(".ssh/id_ed25519") is True, "deny ssh")
    require(path_denied("integrations/hermes/x.patch") is False, "allow patch")

    # Diff-scan pattern uses longer bearer; construct at runtime so public-audit
    # does not flag this test file.
    long_bearer = "Bearer " + ("a" * 24)
    require(scan_diff_for_secrets("+" + long_bearer) is not None, "diff scan")
    require(scan_diff_for_secrets("+print('hello')") is None, "clean diff")


def test_workdir_guard() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        repo = base / "repo"
        repo.mkdir()
        wroot = base / "state" / "worktrees"
        wroot.mkdir(parents=True)
        good = wroot / "inc-1"
        good.mkdir()
        require(
            assert_safe_workdir(good, repo_root=repo, worktrees_root=wroot) is None,
            "good workdir",
        )
        require(
            assert_safe_workdir(repo, repo_root=repo, worktrees_root=wroot) is not None,
            "reject canonical",
        )
        outside = base / "elsewhere"
        outside.mkdir()
        require(
            assert_safe_workdir(outside, repo_root=repo, worktrees_root=wroot)
            is not None,
            "reject outside worktrees",
        )
        # Symlink escape
        escape = wroot / "escape"
        escape.symlink_to(base / "elsewhere")
        require(
            assert_safe_workdir(escape, repo_root=repo, worktrees_root=wroot)
            is not None,
            "reject symlink escape",
        )


def test_opencode_argv_no_shell() -> None:
    calls = []

    def runner(argv, **kwargs):
        calls.append((argv, kwargs))

        class R:
            returncode = 0
            stdout = "ok"
            stderr = ""

        return R()

    oc = OpenCodeRunner(opencode_bin="/bin/echo", runner=runner, timeout=5)
    hostile = "hello; touch /tmp/SHOULD_NOT_EXIST"
    ok, _ = oc.run(workdir=ROOT, prompt=hostile, model="opencode/fake")
    require(ok, "runner ok")
    argv, kwargs = calls[0]
    require(kwargs.get("shell") is False, "shell must be False")
    require(isinstance(argv, list), "argv list")
    require(hostile in argv, "prompt is one argv element")
    require(not Path("/tmp/SHOULD_NOT_EXIST").exists(), "sentinel absent")


def test_store_dedup_circuit_quarantine() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        store = IncidentStore(Path(tmp) / "incidents.sqlite")
        a = store.create(
            signature="sig1",
            title="t",
            sanitized_task="task",
            severity="MEDIUM",
            tier=3,
        )
        require(store.find_open_by_signature("sig1")["id"] == a["id"], "dedup open")
        store.transition(a["id"], "RECOVERED")
        require(store.find_open_by_signature("sig1") is None, "recovered not open")
        store.set_enabled(False)
        require(store.enabled() is False, "disabled")
        store.set_enabled(True)
        n = store.circuit_bump("deploys", 3600)
        require(n == 1, "circuit start")
        n = store.circuit_bump("repair_attempts", 3600)
        require(n == 1, "repair circuit independent")
        store.quarantine_sha("deadbeef", "canary")
        require(store.is_quarantined("deadbeef"), "quarantine")


def test_failure_semantics_no_fake_promotion() -> None:
    """READY / gh-auth ≠ PROMOTED; pre-canary fail ≠ ROLLED_BACK."""
    with tempfile.TemporaryDirectory() as tmp:
        state = Path(tmp) / "state"
        # Minimal fake repo with git
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
        subprocess.run(
            ["git", "add", "README"], cwd=repo, check=True, capture_output=True
        )
        subprocess.run(
            ["git", "commit", "-m", "seed"],
            cwd=repo,
            check=True,
            capture_output=True,
        )

        def promote_unavailable(*_a, **_k):
            return PromoteResult(
                status="GITHUB_PROMOTION_UNAVAILABLE",
                detail="gh not authenticated",
                gh_auth_ok=False,
            )

        def always_validate(_wt):
            return True, "ok"

        fake = ROOT / "tests/reliability/fixtures/fake-opencode"
        # Ensure patch/test exist so fake-opencode can run — copy minimal tree
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
            ["git", "commit", "-m", "add patch"],
            cwd=repo,
            check=True,
            capture_output=True,
        )

        os.environ["OPEN_CLOUD_SELF_HEAL_FAKE_REVIEW"] = "APPROVE"
        os.environ["OPEN_CLOUD_SELF_HEAL_MODELS"] = "opencode/fake,opencode/fake2"
        ctrl = SelfHealController(
            repo_root=repo,
            state_root=state,
            opencode=OpenCodeRunner(opencode_bin=str(fake), timeout=120),
            validate_cmd=always_validate,
            promoter=GitHubPromoter(dry_invoke=promote_unavailable),
            canary=CanaryAdapter(dry_invoke=lambda _k: (True, "ok")),
            test_mode=True,
        )
        row = ctrl.ingest(
            "ValueError",
            "clarify tool choices must be a list of strings",
            module="tools.clarify",
            auto_run=True,
        )
        require(row is not None, "incident")
        require(row["state"] == "READY_FOR_PROMOTION", f"got {row['state']}")
        require(row["state"] != "RECOVERED", "not recovered without merge")
        require(row["meta"].get("gh_auth_ok") is False, "auth tracked")
        require(row["meta"].get("merged") in (False, None), "not merged")

        # Pre-promotion canary fail → CANARY_FAILED, not ROLLED_BACK
        state2 = Path(tmp) / "state2"
        ctrl2 = SelfHealController(
            repo_root=repo,
            state_root=state2,
            opencode=OpenCodeRunner(opencode_bin=str(fake), timeout=120),
            validate_cmd=always_validate,
            canary=CanaryAdapter(dry_invoke=lambda k: (False, "pre fail") if k == "pre" else (True, "ok")),
            promoter=GitHubPromoter(dry_invoke=promote_unavailable),
            test_mode=True,
        )
        row2 = ctrl2.ingest(
            "ValueError",
            "clarify tool choices must be a list of strings",
            module="tools.clarify",
            auto_run=True,
        )
        require(row2["state"] == "CANARY_FAILED", f"got {row2['state']}")
        require(row2["state"] != "ROLLED_BACK", "preflight ≠ rollback")


def test_tier1_tier2_truthful() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        state = Path(tmp) / "state"
        repo = Path(tmp) / "repo"
        repo.mkdir()

        ctrl = SelfHealController(
            repo_root=repo,
            state_root=state,
            p8=P8RuntimeAdapter(
                dry_invoke=lambda _t, _f: ActionResult(
                    status="HUMAN_REQUIRED",
                    detail="p8_unavailable",
                    verified=False,
                )
            ),
            fleet=FleetAdapter(
                dry_invoke=lambda: ActionResult(
                    status="NO_ACTION_TRANSIENT",
                    detail="fleet_verified",
                    verified=True,
                )
            ),
            test_mode=True,
        )
        # Tier 1 via TypeError without opencloud → tier 1
        r1 = ctrl.ingest("TypeError", "foo() missing arg", module="x", auto_run=True)
        require(r1["state"] == "HUMAN_REQUIRED", f"tier1 {r1['state']}")
        require(r1["meta"].get("p8_verified") is False, "not fake success")

        r2 = ctrl.ingest(
            "APITimeoutError", "ReadTimeout provider", module="fleet", auto_run=True
        )
        require(r2["state"] == "NO_ACTION_TRANSIENT", f"tier2 {r2['state']}")
        require(r2["meta"].get("fleet_verified") is True, "fleet verified")


def test_auto_ingest_inbox() -> None:
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
        write_inbox_event(
            ctrl.inbox,
            {
                "exc_type": "APITimeoutError",
                "message": "quota exceeded 429",
                "module": "provider",
            },
        )
        rows = ctrl.scan_inbox(auto_run=True)
        require(len(rows) == 1, "ingested")
        require(rows[0]["state"] == "NO_ACTION_TRANSIENT", rows[0]["state"])
        require(not list(ctrl.inbox.glob("*.json")), "inbox drained")


def test_full_recovery_and_rollback_adapters() -> None:
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
            ["git", "commit", "-m", "seed"],
            cwd=repo,
            check=True,
            capture_output=True,
        )
        base = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=repo, text=True
        ).strip()

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

        def deploy_ok(sha, *, quarantined):
            require(not quarantined, "not quarantined")
            return DeployResult(
                status="DEPLOYED",
                detail="ok",
                previous_head=base,
                previous_tree="prevtree",
                deployed_head=sha,
                deployed_tree="tree123",
            )

        def rollback_ok(sha, *, quarantined):
            return DeployResult(
                status="ROLLED_BACK",
                detail="restored",
                previous_head="abc123merged",
                deployed_head=sha,
            )

        fake = ROOT / "tests/reliability/fixtures/fake-opencode"
        os.environ["OPEN_CLOUD_SELF_HEAL_FAKE_REVIEW"] = "APPROVE"
        os.environ["OPEN_CLOUD_SELF_HEAL_MODELS"] = "opencode/fake,opencode/fake2"
        os.environ["OPEN_CLOUD_SELF_HEAL_ALLOW_MEDIUM_AUTOMERGE"] = "1"

        # Success → RECOVERED
        ctrl = SelfHealController(
            repo_root=repo,
            state_root=state,
            opencode=OpenCodeRunner(opencode_bin=str(fake), timeout=120),
            validate_cmd=lambda _w: (True, "ok"),
            promoter=GitHubPromoter(dry_invoke=promote_ok),
            deployer=DeployAdapter(repo_root=repo, dry_invoke=deploy_ok),
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
        require(row["meta"].get("merged_head") == "abc123merged", "merged recorded")
        require(row["meta"].get("base_head") == base, "base_head immutable")

        # Post-deploy canary fail → ROLLED_BACK + QUARANTINED
        state2 = Path(tmp) / "state2"

        def deploy_then_fail_canary(sha, *, quarantined):
            return DeployResult(
                status="DEPLOYED",
                detail="ok",
                previous_head=base,
                previous_tree="prevtree",
                deployed_head=sha,
                deployed_tree="tree123",
            )

        ctrl2 = SelfHealController(
            repo_root=repo,
            state_root=state2,
            opencode=OpenCodeRunner(opencode_bin=str(fake), timeout=120),
            validate_cmd=lambda _w: (True, "ok"),
            promoter=GitHubPromoter(dry_invoke=promote_ok),
            deployer=DeployAdapter(
                repo_root=repo,
                dry_invoke=lambda sha, *, quarantined: (
                    deploy_then_fail_canary(sha, quarantined=quarantined)
                    if not hasattr(deploy_then_fail_canary, "_rb")
                    else rollback_ok(sha, quarantined=quarantined)
                ),
            ),
            canary=CanaryAdapter(
                dry_invoke=lambda k: (False, "post fail")
                if k == "post"
                else (True, "pre ok")
            ),
            test_mode=True,
        )
        # Fix deployer: need separate deploy vs rollback. Simpler: custom class.
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
            )

        ctrl2.deployer = DeployAdapter(repo_root=repo, dry_invoke=dry_deploy)
        row2 = ctrl2.ingest(
            "ValueError",
            "clarify tool choices must be a list of strings",
            module="tools.clarify",
            auto_run=True,
        )
        require(row2["state"] == "ROLLED_BACK", f"got {row2['state']}")
        require(
            ctrl2.store.is_quarantined("abc123merged"),
            "failed sha quarantined",
        )


def test_cli_help() -> None:
    proc = subprocess.run(
        ["python3", str(ROOT / "integrations/self-repair/guarded_heal/cli.py"), "--help"],
        capture_output=True,
        text=True,
        cwd=str(ROOT),
    )
    require(proc.returncode == 0, "cli help")
    proc2 = subprocess.run(
        [str(ROOT / "bin/opencloud"), "help"],
        capture_output=True,
        text=True,
        cwd=str(ROOT),
    )
    require("self-heal" in proc2.stdout, "bin wires self-heal")


def main() -> None:
    test_classify_and_sanitize()
    test_workdir_guard()
    test_opencode_argv_no_shell()
    test_store_dedup_circuit_quarantine()
    test_failure_semantics_no_fake_promotion()
    test_tier1_tier2_truthful()
    test_auto_ingest_inbox()
    test_full_recovery_and_rollback_adapters()
    test_cli_help()
    print("PASS guarded self-heal classify/sanitize/signature/deny/secret-scan")
    print("PASS workdir guard + symlink escape")
    print("PASS OpenCode argv shell=False safety")
    print("PASS incident store dedup + circuits + quarantine")
    print("PASS failure semantics (READY≠PROMOTED, pre-canary≠rollback)")
    print("PASS tier1/tier2 truthful adapters")
    print("PASS auto-ingest inbox")
    print("PASS E2E-adapter RECOVERED + ROLLED_BACK/QUARANTINED")
    print("PASS CLI wiring")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"FAIL {exc}", file=sys.stderr)
        import traceback

        traceback.print_exc()
        sys.exit(1)
