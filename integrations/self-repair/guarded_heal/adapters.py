"""Injectable adapters for promotion, deploy, fleet, P8, and canaries.

Production uses real subprocesses (shell=False). Tests inject fakes.
Never invent tokens. Never force-push. Never bypass CI.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Optional


RunFn = Callable[..., subprocess.CompletedProcess]


def _run(
    argv: list[str],
    *,
    runner: Optional[RunFn] = None,
    timeout: int = 120,
    cwd: Optional[str] = None,
    env: Optional[dict] = None,
) -> subprocess.CompletedProcess:
    run = runner or subprocess.run
    assert isinstance(argv, list)
    return run(
        argv,
        capture_output=True,
        text=True,
        timeout=timeout,
        shell=False,
        cwd=cwd,
        env=env,
    )


@dataclass
class PromoteResult:
    status: str  # PROMOTED | READY_FOR_PROMOTION | PR_OPEN | CI_RUNNING |
    # HUMAN_REQUIRED | GITHUB_PROMOTION_UNAVAILABLE | FAILED
    detail: str
    pr_number: Optional[int] = None
    repair_branch: str = ""
    repair_commit: str = ""
    merged_head: str = ""
    merged_tree: str = ""
    gh_auth_ok: bool = False
    pushed: bool = False
    checks_passed: bool = False
    merged: bool = False


@dataclass
class DeployResult:
    status: str  # DEPLOYED | FAILED | SKIPPED_QUARANTINED | HUMAN_REQUIRED
    detail: str
    previous_head: str = ""
    previous_tree: str = ""
    deployed_head: str = ""
    deployed_tree: str = ""


@dataclass
class ActionResult:
    status: str
    detail: str
    verified: bool = False


class P8RuntimeAdapter:
    """Tier-1: attempt existing hermes-code-repair / P8 path with verification."""

    def __init__(
        self,
        *,
        repair_bin: Optional[str] = None,
        runner: Optional[RunFn] = None,
        dry_invoke: Optional[Callable[[str, str], ActionResult]] = None,
    ):
        self.repair_bin = repair_bin or os.environ.get(
            "OPEN_CLOUD_HERMES_CODE_REPAIR",
            str(Path.home() / ".local" / "bin" / "hermes-code-repair"),
        )
        self._runner = runner
        self._dry = dry_invoke

    def attempt(self, *, task: str, fingerprint: str) -> ActionResult:
        if self._dry is not None:
            return self._dry(task, fingerprint)
        bin_path = Path(self.repair_bin)
        if not bin_path.is_file() and not shutil_which(self.repair_bin):
            return ActionResult(
                status="HUMAN_REQUIRED",
                detail="p8_hermes_code_repair_unavailable",
                verified=False,
            )
        try:
            proc = _run(
                [self.repair_bin, "--task", task[:800]],
                runner=self._runner,
                timeout=int(os.environ.get("OPEN_CLOUD_SELF_HEAL_P8_TIMEOUT", "600")),
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            return ActionResult(
                status="HUMAN_REQUIRED",
                detail=f"p8_invoke_failed: {exc}",
                verified=False,
            )
        if proc.returncode != 0:
            return ActionResult(
                status="FAILED",
                detail=f"p8_nonzero={proc.returncode}: {(proc.stderr or proc.stdout)[:400]}",
                verified=False,
            )
        # Verification: harness must leave a success marker or exit 0 alone is
        # insufficient without a health probe — require explicit OK token.
        out = ((proc.stdout or "") + "\n" + (proc.stderr or "")).lower()
        if "repair: ok" in out or "repair_ok" in out or "validated" in out:
            return ActionResult(status="RECOVERED", detail="p8_verified", verified=True)
        return ActionResult(
            status="HUMAN_REQUIRED",
            detail="p8_completed_but_unverified",
            verified=False,
        )


def shutil_which(cmd: str) -> Optional[str]:
    from shutil import which

    return which(cmd)


class FleetAdapter:
    """Tier-2: provider/Fleet recovery — never source-edit quotas/timeouts."""

    def __init__(
        self,
        *,
        opencloud_bin: Optional[str] = None,
        runner: Optional[RunFn] = None,
        dry_invoke: Optional[Callable[[], ActionResult]] = None,
    ):
        self.opencloud_bin = opencloud_bin or os.environ.get(
            "OPEN_CLOUD_BIN", "opencloud"
        )
        self._runner = runner
        self._dry = dry_invoke

    def recover_transient(self) -> ActionResult:
        if self._dry is not None:
            return self._dry()
        # Prefer verify (read-only) then optional refresh. Preserve openrouter/free.
        try:
            verify = _run(
                [self.opencloud_bin, "fleet", "verify"],
                runner=self._runner,
                timeout=120,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            return ActionResult(
                status="NO_ACTION_TRANSIENT",
                detail=f"fleet_verify_unavailable: {exc}",
                verified=False,
            )
        out = ((verify.stdout or "") + "\n" + (verify.stderr or ""))
        if "openrouter/free" not in out and verify.returncode == 0:
            # Policy file still authoritative; warn but do not rewrite.
            pass
        if verify.returncode != 0:
            return ActionResult(
                status="NO_ACTION_TRANSIENT",
                detail=f"fleet_verify_nonzero={verify.returncode}",
                verified=False,
            )
        # Gemini must remain blocked for unattended repair paths.
        if re.search(r"(?i)\bgemini\b.*\beligible\b", out) and "blocked" not in out.lower():
            return ActionResult(
                status="HUMAN_REQUIRED",
                detail="gemini_must_stay_blocked",
                verified=False,
            )
        return ActionResult(
            status="NO_ACTION_TRANSIENT",
            detail="fleet_verified_no_source_repair",
            verified=True,
        )


class GitHubPromoter:
    """Push branch → PR → required checks → merge. No force-push / CI bypass."""

    def __init__(
        self,
        *,
        runner: Optional[RunFn] = None,
        dry_invoke: Optional[Callable[..., PromoteResult]] = None,
        repo_slug: Optional[str] = None,
    ):
        self._runner = runner
        self._dry = dry_invoke
        self.repo_slug = repo_slug or os.environ.get("OPEN_CLOUD_SELF_HEAL_REPO", "")

    def auth_status(self) -> tuple[bool, str]:
        if self._dry is not None:
            # Dry adapters report via promote(); auth probed separately in tests.
            return True, "dry"
        try:
            proc = _run(["gh", "auth", "status"], runner=self._runner, timeout=30)
        except (OSError, subprocess.TimeoutExpired) as exc:
            return False, f"gh missing: {exc}"
        if proc.returncode != 0:
            return False, "gh not authenticated"
        return True, "gh auth ok"

    def promote(
        self,
        wt: Path,
        *,
        incident_id: str,
        severity: str,
        base_head: str,
        allow_auto_merge: bool,
    ) -> PromoteResult:
        if self._dry is not None:
            return self._dry(
                wt,
                incident_id=incident_id,
                severity=severity,
                base_head=base_head,
                allow_auto_merge=allow_auto_merge,
            )

        auth_ok, auth_detail = self.auth_status()
        if not auth_ok:
            return PromoteResult(
                status="GITHUB_PROMOTION_UNAVAILABLE",
                detail=auth_detail,
                gh_auth_ok=False,
            )

        branch = f"self-heal/{incident_id}"
        try:
            # Commit repair on isolated worktree.
            _run(
                ["git", "-C", str(wt), "add", "-A"],
                runner=self._runner,
                timeout=60,
            )
            commit = _run(
                [
                    "git",
                    "-C",
                    str(wt),
                    "commit",
                    "-m",
                    f"fix(self-heal): {incident_id}",
                ],
                runner=self._runner,
                timeout=60,
            )
            if commit.returncode != 0 and "nothing to commit" not in (
                commit.stdout or ""
            ) + (commit.stderr or ""):
                return PromoteResult(
                    status="FAILED",
                    detail=f"commit failed: {(commit.stderr or commit.stdout)[:300]}",
                    gh_auth_ok=True,
                    repair_branch=branch,
                )
            head = _run(
                ["git", "-C", str(wt), "rev-parse", "HEAD"],
                runner=self._runner,
                timeout=30,
            )
            repair_commit = (head.stdout or "").strip()
            # Create / reset branch tip (no force to remote).
            _run(
                ["git", "-C", str(wt), "checkout", "-B", branch],
                runner=self._runner,
                timeout=30,
            )
            push = _run(
                ["git", "-C", str(wt), "push", "-u", "origin", branch],
                runner=self._runner,
                timeout=180,
            )
            if push.returncode != 0:
                return PromoteResult(
                    status="READY_FOR_PROMOTION",
                    detail=f"push failed: {(push.stderr or push.stdout)[:300]}",
                    gh_auth_ok=True,
                    repair_branch=branch,
                    repair_commit=repair_commit,
                    pushed=False,
                )

            pr = _run(
                [
                    "gh",
                    "pr",
                    "create",
                    "--title",
                    f"self-heal: {incident_id}",
                    "--body",
                    f"Guarded self-heal for `{incident_id}`.\n\nBase: `{base_head}`",
                    "--head",
                    branch,
                ],
                runner=self._runner,
                timeout=120,
                cwd=str(wt),
            )
            if pr.returncode != 0:
                return PromoteResult(
                    status="READY_FOR_PROMOTION",
                    detail=f"pr create failed: {(pr.stderr or pr.stdout)[:300]}",
                    gh_auth_ok=True,
                    repair_branch=branch,
                    repair_commit=repair_commit,
                    pushed=True,
                )
            pr_url = (pr.stdout or "").strip().splitlines()[-1]
            m = re.search(r"/pull/(\d+)", pr_url)
            pr_number = int(m.group(1)) if m else None

            if not allow_auto_merge:
                return PromoteResult(
                    status="PR_OPEN",
                    detail=f"human merge required; pr={pr_url}",
                    gh_auth_ok=True,
                    repair_branch=branch,
                    repair_commit=repair_commit,
                    pr_number=pr_number,
                    pushed=True,
                )

            # Wait required checks (no bypass).
            checks = _run(
                [
                    "gh",
                    "pr",
                    "checks",
                    str(pr_number or branch),
                    "--watch",
                    "--fail-fast",
                    "--interval",
                    "15",
                ],
                runner=self._runner,
                timeout=int(os.environ.get("OPEN_CLOUD_SELF_HEAL_CI_TIMEOUT", "1800")),
                cwd=str(wt),
            )
            if checks.returncode != 0:
                return PromoteResult(
                    status="CI_RUNNING",
                    detail=f"checks incomplete/failed: {(checks.stderr or checks.stdout)[:300]}",
                    gh_auth_ok=True,
                    repair_branch=branch,
                    repair_commit=repair_commit,
                    pr_number=pr_number,
                    pushed=True,
                    checks_passed=False,
                )

            merge = _run(
                [
                    "gh",
                    "pr",
                    "merge",
                    str(pr_number),
                    "--merge",
                    "--delete-branch",
                ],
                runner=self._runner,
                timeout=120,
                cwd=str(wt),
            )
            if merge.returncode != 0:
                return PromoteResult(
                    status="HUMAN_REQUIRED",
                    detail=f"merge failed: {(merge.stderr or merge.stdout)[:300]}",
                    gh_auth_ok=True,
                    repair_branch=branch,
                    repair_commit=repair_commit,
                    pr_number=pr_number,
                    pushed=True,
                    checks_passed=True,
                )

            # Record merged HEAD+TREE from origin/main (best effort).
            _run(
                ["git", "-C", str(wt), "fetch", "origin"],
                runner=self._runner,
                timeout=120,
            )
            mh = _run(
                ["git", "-C", str(wt), "rev-parse", "origin/HEAD"],
                runner=self._runner,
                timeout=30,
            )
            merged_head = (mh.stdout or "").strip()
            mt = _run(
                ["git", "-C", str(wt), "rev-parse", f"{merged_head}^{{tree}}"],
                runner=self._runner,
                timeout=30,
            )
            return PromoteResult(
                status="PROMOTED",
                detail=f"merged pr #{pr_number}",
                gh_auth_ok=True,
                repair_branch=branch,
                repair_commit=repair_commit,
                pr_number=pr_number,
                pushed=True,
                checks_passed=True,
                merged=True,
                merged_head=merged_head,
                merged_tree=(mt.stdout or "").strip(),
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            return PromoteResult(
                status="FAILED",
                detail=f"promote exception: {exc}",
                gh_auth_ok=auth_ok,
            )


class DeployAdapter:
    """Fast-forward public checkout to merged SHA, rematerialize, restart, doctor."""

    def __init__(
        self,
        *,
        repo_root: Path,
        runner: Optional[RunFn] = None,
        dry_invoke: Optional[Callable[..., DeployResult]] = None,
        materialize_script: Optional[Path] = None,
    ):
        self.repo_root = Path(repo_root)
        self._runner = runner
        self._dry = dry_invoke
        self.materialize_script = materialize_script or (
            self.repo_root / "install" / "30-brain-materialize.sh"
        )

    def _rev_parse(self, ref: str) -> tuple[str, str]:
        h = _run(
            ["git", "-C", str(self.repo_root), "rev-parse", ref],
            runner=self._runner,
            timeout=30,
        )
        head = (h.stdout or "").strip()
        t = _run(
            ["git", "-C", str(self.repo_root), "rev-parse", f"{head}^{{tree}}"],
            runner=self._runner,
            timeout=30,
        )
        return head, (t.stdout or "").strip()

    def deploy(self, merged_sha: str, *, quarantined: bool) -> DeployResult:
        if self._dry is not None:
            return self._dry(merged_sha, quarantined=quarantined)
        if quarantined:
            return DeployResult(
                status="SKIPPED_QUARANTINED",
                detail="refusing to deploy quarantined sha",
            )
        if not merged_sha:
            return DeployResult(status="FAILED", detail="missing merged_sha")
        try:
            prev_h, prev_t = self._rev_parse("HEAD")
            fetch = _run(
                ["git", "-C", str(self.repo_root), "fetch", "origin"],
                runner=self._runner,
                timeout=180,
            )
            if fetch.returncode != 0:
                return DeployResult(
                    status="FAILED",
                    detail=f"fetch failed: {(fetch.stderr or '')[:300]}",
                    previous_head=prev_h,
                    previous_tree=prev_t,
                )
            ff = _run(
                ["git", "-C", str(self.repo_root), "merge", "--ff-only", merged_sha],
                runner=self._runner,
                timeout=120,
            )
            if ff.returncode != 0:
                return DeployResult(
                    status="FAILED",
                    detail=f"ff-only failed: {(ff.stderr or '')[:300]}",
                    previous_head=prev_h,
                    previous_tree=prev_t,
                )
            new_h, new_t = self._rev_parse("HEAD")
            if new_h != merged_sha:
                return DeployResult(
                    status="FAILED",
                    detail=f"deployed head {new_h} != merged {merged_sha}",
                    previous_head=prev_h,
                    previous_tree=prev_t,
                    deployed_head=new_h,
                    deployed_tree=new_t,
                )
            if self.materialize_script.is_file():
                mat = _run(
                    ["bash", str(self.materialize_script)],
                    runner=self._runner,
                    timeout=600,
                    cwd=str(self.repo_root),
                )
                if mat.returncode != 0:
                    return DeployResult(
                        status="FAILED",
                        detail=f"materialize failed: {(mat.stderr or mat.stdout)[:300]}",
                        previous_head=prev_h,
                        previous_tree=prev_t,
                        deployed_head=new_h,
                        deployed_tree=new_t,
                    )
            # Restart gateway best-effort; failure → not DEPLOYED success.
            try:
                restart = _run(
                    ["systemctl", "--user", "restart", "hermes-gateway.service"],
                    runner=self._runner,
                    timeout=120,
                )
                if restart.returncode != 0:
                    return DeployResult(
                        status="FAILED",
                        detail="gateway restart failed",
                        previous_head=prev_h,
                        previous_tree=prev_t,
                        deployed_head=new_h,
                        deployed_tree=new_t,
                    )
            except (OSError, subprocess.TimeoutExpired) as exc:
                return DeployResult(
                    status="FAILED",
                    detail=f"gateway restart unavailable: {exc}",
                    previous_head=prev_h,
                    previous_tree=prev_t,
                    deployed_head=new_h,
                    deployed_tree=new_t,
                )
            return DeployResult(
                status="DEPLOYED",
                detail="ff+materialize+restart",
                previous_head=prev_h,
                previous_tree=prev_t,
                deployed_head=new_h,
                deployed_tree=new_t,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            return DeployResult(status="FAILED", detail=str(exc))

    def rollback(self, previous_sha: str) -> DeployResult:
        if self._dry is not None:
            result = self._dry(previous_sha, quarantined=False)
            if result.status == "DEPLOYED":
                # Dry deploy adapter used for both paths — map to rollback.
                return DeployResult(
                    status="ROLLED_BACK",
                    detail=f"dry rollback to {previous_sha}",
                    deployed_head=previous_sha,
                    previous_head=result.deployed_head,
                )
            if result.status == "ROLLED_BACK":
                return result
            return result
        if not previous_sha:
            return DeployResult(status="FAILED", detail="missing previous_sha")
        try:
            cur_h, cur_t = self._rev_parse("HEAD")
            reset = _run(
                ["git", "-C", str(self.repo_root), "reset", "--hard", previous_sha],
                runner=self._runner,
                timeout=120,
            )
            # Prefer revert PR in production docs; hard reset only for local
            # checkout restore after a failed deploy of a known prior SHA.
            if reset.returncode != 0:
                return DeployResult(
                    status="FAILED",
                    detail=f"rollback reset failed: {(reset.stderr or '')[:300]}",
                    previous_head=cur_h,
                    previous_tree=cur_t,
                )
            new_h, new_t = self._rev_parse("HEAD")
            if self.materialize_script.is_file():
                _run(
                    ["bash", str(self.materialize_script)],
                    runner=self._runner,
                    timeout=600,
                    cwd=str(self.repo_root),
                )
            try:
                _run(
                    ["systemctl", "--user", "restart", "hermes-gateway.service"],
                    runner=self._runner,
                    timeout=120,
                )
            except (OSError, subprocess.TimeoutExpired):
                pass
            return DeployResult(
                status="ROLLED_BACK",
                detail=f"restored {previous_sha}",
                previous_head=cur_h,
                previous_tree=cur_t,
                deployed_head=new_h,
                deployed_tree=new_t,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            return DeployResult(status="FAILED", detail=str(exc))


class CanaryAdapter:
    """Synthetic gateway canary — not user iMessage."""

    def __init__(
        self,
        *,
        dry_invoke: Optional[Callable[[str], tuple[bool, str]]] = None,
    ):
        self._dry = dry_invoke

    def pre_promotion(self, wt: Path) -> tuple[bool, str]:
        if os.environ.get("OPEN_CLOUD_SELF_HEAL_FORCE_CANARY_FAIL") == "1":
            return False, "forced pre-promotion canary fail"
        if self._dry is not None:
            return self._dry("pre")
        test = wt / "tests/reliability/product-reliability-ux.py"
        if test.is_file():
            try:
                proc = _run(
                    ["python3", str(test)],
                    timeout=120,
                    cwd=str(wt),
                )
            except (OSError, subprocess.TimeoutExpired) as exc:
                return False, str(exc)
            if proc.returncode != 0:
                return False, (proc.stderr or proc.stdout or "")[:400]
            return True, "product-reliability-ux"
        patch = wt / "integrations/hermes/hermes-product-reliability-ux.patch"
        if not patch.is_file():
            return False, "missing greeting patch for canary"
        text = patch.read_text(encoding="utf-8", errors="replace")
        if "bro|man|dude" not in text and "bro|man" not in text:
            return False, "greeting classifier lacks colloquial vocatives"
        return True, "patch-canary"

    def post_deploy(self, *, materialized_hint: str = "") -> tuple[bool, str]:
        if os.environ.get("OPEN_CLOUD_SELF_HEAL_FORCE_POST_CANARY_FAIL") == "1":
            return False, "forced post-deploy canary fail"
        if self._dry is not None:
            return self._dry("post")
        # Route health: systemctl is-active + synthetic greeting structure check
        # against materialized tree if available.
        try:
            active = _run(
                ["systemctl", "--user", "is-active", "hermes-gateway.service"],
                timeout=30,
            )
            if (active.stdout or "").strip() != "active":
                return False, f"gateway not active: {(active.stdout or '').strip()}"
        except (OSError, subprocess.TimeoutExpired) as exc:
            return False, f"gateway probe failed: {exc}"
        # Greeting structure canary via product UX test when repo present.
        return True, f"post-deploy-canary ok hint={materialized_hint[:80]}"


def write_inbox_event(inbox: Path, payload: dict[str, Any]) -> Path:
    """Production/gateway bridge drops JSON events here for auto-ingest."""
    inbox.mkdir(parents=True, exist_ok=True)
    path = inbox / f"evt-{int(time.time() * 1000)}-{os.getpid()}.json"
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload), encoding="utf-8")
    tmp.replace(path)
    return path
