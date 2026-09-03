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
    """Legacy P8 / hermes-code-repair path. Not used for gateway crash / stuck turn."""

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


class RuntimeServiceAdapter:
    """Tier-1 RUNTIME recovery: inspect/restart/verify. Never source-repair.

    ponytail: systemctl + is-active only — no hermes-code-repair, no live Hermes
    scan, no py_compile of venv. Ceiling: user-session unit only; escalate to
    HUMAN_REQUIRED when restart/verify cannot prove health.
    """

    UNIT = "hermes-gateway.service"

    def __init__(
        self,
        *,
        unit: str = UNIT,
        runner: Optional[RunFn] = None,
        dry_invoke: Optional[Callable[..., ActionResult]] = None,
        health_probe: Optional[Callable[[], tuple[bool, str]]] = None,
        wait_seconds: float = 2.0,
    ):
        self.unit = unit
        self._runner = runner
        self._dry = dry_invoke
        self._health = health_probe
        self.wait_seconds = wait_seconds

    def _systemctl(self, *args: str, timeout: int = 60) -> subprocess.CompletedProcess:
        return _run(
            ["systemctl", "--user", *args],
            runner=self._runner,
            timeout=timeout,
        )

    def is_active(self) -> tuple[Optional[bool], str]:
        try:
            proc = self._systemctl("is-active", self.unit, timeout=30)
        except subprocess.TimeoutExpired:
            return None, "is-active TIMEOUT"
        except OSError as exc:
            return None, f"is-active UNKNOWN: {exc}"
        state = (proc.stdout or "").strip()
        if state == "active":
            return True, state
        if state in ("inactive", "failed", "deactivating", "activating"):
            return False, state
        return None, state or f"rc={proc.returncode}"

    def _probe_health(self) -> tuple[bool, str]:
        if self._health is not None:
            return self._health()
        # Default: is-active alone is the health gate for crash recovery.
        active, detail = self.is_active()
        if active is True:
            return True, f"active:{detail}"
        if active is False:
            return False, f"not_active:{detail}"
        return False, f"unknown:{detail}"

    def recover_crash(self, *, reason: str = "gateway_crash") -> ActionResult:
        """Inspect → if active verify → else restart → wait → is-active → health."""
        if self._dry is not None:
            return self._dry("crash", reason)
        active, a_detail = self.is_active()
        if active is True:
            ok, h = self._probe_health()
            if ok:
                return ActionResult(
                    status="NO_ACTION_TRANSIENT",
                    detail=f"already_active_verified:{h}",
                    verified=True,
                )
            return ActionResult(
                status="HUMAN_REQUIRED",
                detail=f"active_but_unhealthy:{h}",
                verified=False,
            )
        if active is None:
            return ActionResult(
                status="HUMAN_REQUIRED",
                detail=f"service_state_unknown:{a_detail}",
                verified=False,
            )
        try:
            restart = self._systemctl("restart", self.unit, timeout=120)
        except subprocess.TimeoutExpired:
            return ActionResult(
                status="FAILED", detail="restart TIMEOUT", verified=False
            )
        except OSError as exc:
            return ActionResult(
                status="HUMAN_REQUIRED",
                detail=f"restart unavailable: {exc}",
                verified=False,
            )
        if restart.returncode != 0:
            return ActionResult(
                status="FAILED",
                detail=f"restart rc={restart.returncode}",
                verified=False,
            )
        if self.wait_seconds > 0:
            time.sleep(self.wait_seconds)
        active2, a2 = self.is_active()
        if active2 is not True:
            return ActionResult(
                status="FAILED",
                detail=f"post_restart_not_active:{a2}",
                verified=False,
            )
        ok, h = self._probe_health()
        if not ok:
            return ActionResult(
                status="FAILED",
                detail=f"post_restart_health_fail:{h}",
                verified=False,
            )
        return ActionResult(
            status="RECOVERED",
            detail=f"runtime_restart_verified:{h}",
            verified=True,
        )

    def recover_stuck_turn(self) -> ActionResult:
        """Supported runtime/session cleanup only; else HUMAN_REQUIRED. No source edit."""
        if self._dry is not None:
            return self._dry("stuck_turn", "stuck_turn")
        # ponytail: no generic session cleaner shipped yet → fail closed.
        return ActionResult(
            status="HUMAN_REQUIRED",
            detail="stuck_turn_no_supported_runtime_cleanup",
            verified=False,
        )

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
            self.repo_root / "install" / "35-hermes-live.sh"
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
                    ["bash", str(self.materialize_script), "--install"],
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

    def rollback(
        self,
        previous_sha: str,
        *,
        previous_tree: str = "",
        post_canary: Optional[Callable[[], tuple[bool, str]]] = None,
    ) -> DeployResult:
        """Restore previous SHA only; ROLLED_BACK iff every gate is proven.

        Gates: HEAD==previous, TREE==previous (when known), materialize rc=0,
        restart rc=0, gateway active, post-rollback canary PASS.
        Any miss → ROLLBACK_FAILED (never swallow as success).
        """
        if self._dry is not None:
            # Dry adapters may return ROLLED_BACK / ROLLBACK_FAILED / FAILED directly.
            result = self._dry(previous_sha, quarantined=False)
            if result.status in ("ROLLED_BACK", "ROLLBACK_FAILED", "FAILED"):
                return result
            if result.status == "DEPLOYED":
                return DeployResult(
                    status="ROLLED_BACK",
                    detail=f"dry rollback to {previous_sha}",
                    deployed_head=previous_sha,
                    deployed_tree=previous_tree or result.deployed_tree,
                    previous_head=result.deployed_head,
                )
            return DeployResult(
                status="ROLLBACK_FAILED",
                detail=f"dry rollback incomplete: {result.status} {result.detail}",
                previous_head=result.previous_head,
                previous_tree=result.previous_tree,
                deployed_head=result.deployed_head,
                deployed_tree=result.deployed_tree,
            )
        if not previous_sha:
            return DeployResult(status="ROLLBACK_FAILED", detail="missing previous_sha")
        try:
            cur_h, cur_t = self._rev_parse("HEAD")
            reset = _run(
                ["git", "-C", str(self.repo_root), "reset", "--hard", previous_sha],
                runner=self._runner,
                timeout=120,
            )
            if reset.returncode != 0:
                return DeployResult(
                    status="ROLLBACK_FAILED",
                    detail=f"rollback reset failed: {(reset.stderr or '')[:300]}",
                    previous_head=cur_h,
                    previous_tree=cur_t,
                )
            new_h, new_t = self._rev_parse("HEAD")
            if new_h != previous_sha:
                return DeployResult(
                    status="ROLLBACK_FAILED",
                    detail=f"HEAD {new_h} != previous {previous_sha}",
                    previous_head=cur_h,
                    previous_tree=cur_t,
                    deployed_head=new_h,
                    deployed_tree=new_t,
                )
            if previous_tree and new_t and new_t != previous_tree:
                return DeployResult(
                    status="ROLLBACK_FAILED",
                    detail=f"TREE {new_t} != previous {previous_tree}",
                    previous_head=cur_h,
                    previous_tree=cur_t,
                    deployed_head=new_h,
                    deployed_tree=new_t,
                )
            if self.materialize_script.is_file():
                mat = _run(
                    ["bash", str(self.materialize_script), "--install"],
                    runner=self._runner,
                    timeout=600,
                    cwd=str(self.repo_root),
                )
                if mat.returncode != 0:
                    return DeployResult(
                        status="ROLLBACK_FAILED",
                        detail=f"materialize failed rc={mat.returncode}: "
                        f"{(mat.stderr or mat.stdout)[:300]}",
                        previous_head=cur_h,
                        previous_tree=cur_t,
                        deployed_head=new_h,
                        deployed_tree=new_t,
                    )
            try:
                restart = _run(
                    ["systemctl", "--user", "restart", "hermes-gateway.service"],
                    runner=self._runner,
                    timeout=120,
                )
            except subprocess.TimeoutExpired:
                return DeployResult(
                    status="ROLLBACK_FAILED",
                    detail="gateway restart timeout",
                    previous_head=cur_h,
                    previous_tree=cur_t,
                    deployed_head=new_h,
                    deployed_tree=new_t,
                )
            except OSError as exc:
                return DeployResult(
                    status="ROLLBACK_FAILED",
                    detail=f"gateway restart unavailable: {exc}",
                    previous_head=cur_h,
                    previous_tree=cur_t,
                    deployed_head=new_h,
                    deployed_tree=new_t,
                )
            if restart.returncode != 0:
                return DeployResult(
                    status="ROLLBACK_FAILED",
                    detail=f"gateway restart rc={restart.returncode}",
                    previous_head=cur_h,
                    previous_tree=cur_t,
                    deployed_head=new_h,
                    deployed_tree=new_t,
                )
            try:
                active = _run(
                    ["systemctl", "--user", "is-active", "hermes-gateway.service"],
                    runner=self._runner,
                    timeout=30,
                )
            except (OSError, subprocess.TimeoutExpired) as exc:
                return DeployResult(
                    status="ROLLBACK_FAILED",
                    detail=f"gateway active probe failed: {exc}",
                    previous_head=cur_h,
                    previous_tree=cur_t,
                    deployed_head=new_h,
                    deployed_tree=new_t,
                )
            if (active.stdout or "").strip() != "active":
                return DeployResult(
                    status="ROLLBACK_FAILED",
                    detail=f"gateway not active after rollback: "
                    f"{(active.stdout or '').strip()}",
                    previous_head=cur_h,
                    previous_tree=cur_t,
                    deployed_head=new_h,
                    deployed_tree=new_t,
                )
            if post_canary is not None:
                try:
                    ok, detail = post_canary()
                except Exception as exc:  # noqa: BLE001 — fail closed
                    return DeployResult(
                        status="ROLLBACK_FAILED",
                        detail=f"post-rollback canary exception: {exc}",
                        previous_head=cur_h,
                        previous_tree=cur_t,
                        deployed_head=new_h,
                        deployed_tree=new_t,
                    )
                if not ok:
                    return DeployResult(
                        status="ROLLBACK_FAILED",
                        detail=f"post-rollback canary FAIL: {detail[:400]}",
                        previous_head=cur_h,
                        previous_tree=cur_t,
                        deployed_head=new_h,
                        deployed_tree=new_t,
                    )
            return DeployResult(
                status="ROLLED_BACK",
                detail=f"restored {previous_sha}",
                previous_head=cur_h,
                previous_tree=cur_t,
                deployed_head=new_h,
                deployed_tree=new_t,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            return DeployResult(status="ROLLBACK_FAILED", detail=str(exc))
        except Exception as exc:  # noqa: BLE001 — never claim success
            return DeployResult(status="ROLLBACK_FAILED", detail=f"exception: {exc}")


# ── Canary helpers (materialized greeting probe) ─────────────────────────────

_GREETING_PASS = ("Hi bro", "Hi man", "Bro", "you there?", "macha you there daa?")
_GREETING_TASK = (
    "Hi bro deploy the fleet",
    "Bro switch to Muse",
    "Hi what model are you using",
    "Hi is it gonna rain",
)


def _extract_greeting_fn(source: str) -> Optional[Callable[[str], bool]]:
    """Exec ``_opencloud_is_conversational_greeting`` from materialized source."""
    marker = "def _opencloud_is_conversational_greeting"
    start = source.find(marker)
    if start < 0:
        return None
    # Capture through the function's final ``return False`` at def indent.
    rest = source[start:]
    lines = rest.splitlines(keepends=True)
    body: list[str] = [lines[0]]
    for line in lines[1:]:
        if line.startswith("def ") or line.startswith("class "):
            break
        body.append(line)
        if line.rstrip() == "    return False":
            # Keep going in case more returns follow; stop at next top-level.
            continue
    code = "".join(body)
    ns: dict[str, Any] = {"re": re}
    try:
        exec(code, ns)  # noqa: S102 — bounded snippet from our materialized tree
    except Exception:
        return None
    fn = ns.get("_opencloud_is_conversational_greeting")
    return fn if callable(fn) else None


def probe_materialized_greeting(
    hermes_root: Path,
) -> tuple[bool, str]:
    """Structural/runtime probe against ~/.hermes/hermes-agent (not repo patch alone)."""
    root = Path(hermes_root)
    loop = root / "agent" / "conversation_loop.py"
    if not loop.is_file():
        return False, "POST_DEPLOY_RUNTIME_CANARY: missing conversation_loop.py"
    try:
        text = loop.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return False, f"POST_DEPLOY_RUNTIME_CANARY: read failed: {exc}"
    if "HERMES_OPENCLOUD_TOOL_INTENT_V1" not in text:
        return False, "POST_DEPLOY_RUNTIME_CANARY: missing TOOL_INTENT marker"
    if "bro|man|dude" not in text and "bro|man" not in text:
        return False, "POST_DEPLOY_RUNTIME_CANARY: vocatives missing in materialized"
    if "HERMES_OPENCLOUD_GREETING_TOOL_CHOICE_NONE_V1" not in text:
        return False, "POST_DEPLOY_RUNTIME_CANARY: missing GREETING_TOOL_CHOICE_NONE marker"
    fn = _extract_greeting_fn(text)
    if fn is None:
        return False, "POST_DEPLOY_RUNTIME_CANARY: could not load greeting classifier"
    for sample in _GREETING_PASS:
        try:
            if fn(sample) is not True:
                return False, f"POST_DEPLOY_RUNTIME_CANARY: expected greeting {sample!r}"
        except Exception as exc:  # noqa: BLE001
            return False, f"POST_DEPLOY_RUNTIME_CANARY: classifier error: {exc}"
    for sample in _GREETING_TASK:
        try:
            if fn(sample) is not False:
                return False, f"POST_DEPLOY_RUNTIME_CANARY: false positive {sample!r}"
        except Exception as exc:  # noqa: BLE001
            return False, f"POST_DEPLOY_RUNTIME_CANARY: classifier error: {exc}"
    return True, "POST_DEPLOY_RUNTIME_CANARY: greeting+markers ok"


class CanaryAdapter:
    """Synthetic gateway canary — not user iMessage.

    Kinds: PRE_PROMOTION_CANARY (worktree/patch) vs POST_DEPLOY_RUNTIME_CANARY
    (materialized ~/.hermes/hermes-agent + gateway + fleet).
    UNKNOWN/TIMEOUT → FAIL.
    """

    def __init__(
        self,
        *,
        dry_invoke: Optional[Callable[[str], tuple[bool, str]]] = None,
        hermes_root: Optional[Path] = None,
        opencloud_bin: Optional[str] = None,
        runner: Optional[RunFn] = None,
    ):
        self._dry = dry_invoke
        self.hermes_root = Path(
            hermes_root
            or os.environ.get(
                "OPEN_CLOUD_HERMES_ROOT",
                str(Path.home() / ".hermes" / "hermes-agent"),
            )
        ).expanduser()
        self.opencloud_bin = opencloud_bin or os.environ.get(
            "OPEN_CLOUD_BIN", "opencloud"
        )
        self._runner = runner

    def pre_promotion(self, wt: Path) -> tuple[bool, str]:
        if os.environ.get("OPEN_CLOUD_SELF_HEAL_FORCE_CANARY_FAIL") == "1":
            return False, "PRE_PROMOTION_CANARY: forced fail"
        if self._dry is not None:
            ok, detail = self._dry("pre")
            return ok, f"PRE_PROMOTION_CANARY: {detail}"
        test = wt / "tests/reliability/product-reliability-ux.py"
        if test.is_file():
            try:
                proc = _run(
                    ["python3", str(test)],
                    runner=self._runner,
                    timeout=120,
                    cwd=str(wt),
                )
            except subprocess.TimeoutExpired:
                return False, "PRE_PROMOTION_CANARY: TIMEOUT"
            except OSError as exc:
                return False, f"PRE_PROMOTION_CANARY: UNKNOWN {exc}"
            if proc.returncode != 0:
                return False, (
                    "PRE_PROMOTION_CANARY: FAIL "
                    + (proc.stderr or proc.stdout or "")[:400]
                )
            return True, "PRE_PROMOTION_CANARY: product-reliability-ux"
        patch = wt / "integrations/hermes/hermes-product-reliability-ux.patch"
        if not patch.is_file():
            return False, "PRE_PROMOTION_CANARY: missing greeting patch"
        text = patch.read_text(encoding="utf-8", errors="replace")
        if "bro|man|dude" not in text and "bro|man" not in text:
            return False, "PRE_PROMOTION_CANARY: greeting classifier lacks vocatives"
        return True, "PRE_PROMOTION_CANARY: patch-structure"

    def post_deploy(self, *, materialized_hint: str = "") -> tuple[bool, str]:
        if os.environ.get("OPEN_CLOUD_SELF_HEAL_FORCE_POST_CANARY_FAIL") == "1":
            return False, "POST_DEPLOY_RUNTIME_CANARY: forced fail"
        if self._dry is not None:
            ok, detail = self._dry("post")
            return ok, f"POST_DEPLOY_RUNTIME_CANARY: {detail}"
        # 1) Gateway active
        try:
            active = _run(
                ["systemctl", "--user", "is-active", "hermes-gateway.service"],
                runner=self._runner,
                timeout=30,
            )
        except subprocess.TimeoutExpired:
            return False, "POST_DEPLOY_RUNTIME_CANARY: gateway is-active TIMEOUT"
        except OSError as exc:
            return False, f"POST_DEPLOY_RUNTIME_CANARY: gateway UNKNOWN {exc}"
        if (active.stdout or "").strip() != "active":
            return False, (
                "POST_DEPLOY_RUNTIME_CANARY: gateway not active: "
                + (active.stdout or "").strip()
            )
        # 2) Materialized greeting canary (not repo patch alone)
        g_ok, g_detail = probe_materialized_greeting(self.hermes_root)
        if not g_ok:
            return False, g_detail
        # 3) Fleet doctor / probe — TIMEOUT/UNKNOWN = FAIL
        try:
            fleet = _run(
                [self.opencloud_bin, "fleet", "verify"],
                runner=self._runner,
                timeout=120,
            )
        except subprocess.TimeoutExpired:
            return False, "POST_DEPLOY_RUNTIME_CANARY: fleet verify TIMEOUT"
        except OSError as exc:
            return False, f"POST_DEPLOY_RUNTIME_CANARY: fleet UNKNOWN {exc}"
        if fleet.returncode != 0:
            return False, (
                "POST_DEPLOY_RUNTIME_CANARY: fleet verify FAIL "
                + (fleet.stderr or fleet.stdout or "")[:300]
            )
        hint = materialized_hint[:80]
        return True, f"POST_DEPLOY_RUNTIME_CANARY: pass hint={hint} ({g_detail})"


def write_inbox_event(inbox: Path, payload: dict[str, Any]) -> Path:
    """Production/gateway bridge drops JSON events here for auto-ingest."""
    inbox.mkdir(parents=True, exist_ok=True)
    path = inbox / f"evt-{int(time.time() * 1000)}-{os.getpid()}.json"
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload), encoding="utf-8")
    tmp.replace(path)
    return path
