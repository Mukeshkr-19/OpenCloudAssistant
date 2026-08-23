"""Guarded self-heal engine: classify → recover → verify → promote → deploy → canary.

Extends existing self-repair; does not replace ``hermes-code-repair`` or P8.
OpenCode edits only isolated OpenCloudAssistant worktrees — never
``~/.hermes/hermes-agent`` and never the canonical live checkout.

Invariant: unknown ≠ success; eligible ≠ promoted; validated ≠ deployed;
preflight failure ≠ rollback; timeout ≠ success; missing operation ≠ success.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Optional

from .adapters import (
    CanaryAdapter,
    DeployAdapter,
    FleetAdapter,
    GitHubPromoter,
    P8RuntimeAdapter,
    RuntimeServiceAdapter,
    write_inbox_event,
)
from .store import IncidentStore, REOPENABLE
DEFAULT_STATE_ROOT = Path(
    os.environ.get(
        "OPEN_CLOUD_SELF_HEAL_STATE",
        str(Path.home() / ".opencloud" / "self-repair"),
    )
).expanduser()

MAX_CHANGED_FILES = int(os.environ.get("OPEN_CLOUD_SELF_HEAL_MAX_FILES", "12"))
MAX_TOTAL_CHANGED_LINES = int(
    os.environ.get("OPEN_CLOUD_SELF_HEAL_MAX_LINES", "1500")
)
MAX_ATTEMPTS = int(os.environ.get("OPEN_CLOUD_SELF_HEAL_MAX_ATTEMPTS", "3"))
MAX_INCIDENTS_PER_HOUR = int(
    os.environ.get("OPEN_CLOUD_SELF_HEAL_MAX_INCIDENTS_HOUR", "4")
)
MAX_DEPLOYS_PER_HOUR = int(
    os.environ.get("OPEN_CLOUD_SELF_HEAL_MAX_DEPLOYS_HOUR", "2")
)
MAX_REPAIRS_PER_HOUR = int(
    os.environ.get("OPEN_CLOUD_SELF_HEAL_MAX_REPAIRS_HOUR", "6")
)
MAX_PROMOTIONS_PER_HOUR = int(
    os.environ.get("OPEN_CLOUD_SELF_HEAL_MAX_PROMOTIONS_HOUR", "3")
)
MAX_ROLLBACKS_PER_HOUR = int(
    os.environ.get("OPEN_CLOUD_SELF_HEAL_MAX_ROLLBACKS_HOUR", "3")
)
MAX_MODELS = int(os.environ.get("OPEN_CLOUD_SELF_HEAL_MAX_MODELS", "3"))
REPAIR_TIMEOUT = int(os.environ.get("OPEN_CLOUD_SELF_HEAL_TIMEOUT", "900"))
REVIEW_TIMEOUT = int(os.environ.get("OPEN_CLOUD_SELF_HEAL_REVIEW_TIMEOUT", "300"))
VALIDATE_TIMEOUT = int(os.environ.get("OPEN_CLOUD_SELF_HEAL_VALIDATE_TIMEOUT", "600"))
REOPEN_WINDOW = int(os.environ.get("OPEN_CLOUD_SELF_HEAL_REOPEN_SECONDS", "86400"))
MAX_OCCURRENCES_BEFORE_HUMAN = int(
    os.environ.get("OPEN_CLOUD_SELF_HEAL_MAX_OCCURRENCES", "3")
)
# Controller tick: bound incidents processed per run (detector does not wait).
MAX_QUEUE_PER_TICK = int(os.environ.get("OPEN_CLOUD_SELF_HEAL_MAX_QUEUE_TICK", "2"))
# RECOVERING lease TTL — timeout / killed worker → INTERRUPTED, not RECOVERED.
LEASE_SECONDS = int(os.environ.get("OPEN_CLOUD_SELF_HEAL_LEASE_SECONDS", "1500"))
# Reasons that must use runtime restart adapter — never P8 / hermes-code-repair.
RUNTIME_TIER1_REASONS = frozenset(
    {"gateway_crash", "stuck_turn", "gateway_lifecycle"}
)
# Explicit policy: MEDIUM never auto-merges unless env is set at runtime.
# (checked in _source_repair — not cached at import)

# Test-only: allow fake OpenCode / fake models.
TEST_MODE = os.environ.get("OPEN_CLOUD_SELF_HEAL_TEST_MODE", "") == "1"

# Paths OpenCode must never touch in a source repair (fail → HUMAN_REQUIRED).
DENY_PATH_GLOBS = (
    re.compile(r"(^|/)(\.env|\.env\..+)$"),
    re.compile(r"(^|/)(credentials|secrets?|auth\.json|id_rsa|id_ed25519)(/|$)"),
    re.compile(r"(^|/).*(\.pem|\.key|\.p12|\.pfx)$"),
    re.compile(r"(^|/)(terraform|\.terraform|tfstate|\.tfstate)(/|$)"),
    re.compile(r"(^|/)(\.git/config|\.ssh|known_hosts)(/|$)"),
    re.compile(r"(^|/)private(/|$)"),
    re.compile(r"(^|/)model-benchmarks(/|$)"),
    re.compile(r"(^|/)(\.aws|\.config/gh|\.config/gcloud)(/|$)"),
    re.compile(r"(^|/)(oci_api_key|oci_api_key\.pem|config/oci)(/|$)"),
    re.compile(r"(^|/)(\.netrc|\.npmrc|\.pypirc)$"),
    re.compile(r"(^|/)(kubeconfig|\.kube)(/|$)"),
    re.compile(r"(^|/)(docker/config\.json|\.docker/config\.json)$"),
)

HERMES_LIVE_DENY = re.compile(r"(^|/)\.hermes(/|$)")

OPTIONAL_WARNING_MARKERS = (
    "deprecationwarning",
    "userwarning",
    "resourcewarning",
    "debug:",
    "trace:",
)

EXTERNAL_PROVIDER_MARKERS = (
    "rate limit",
    "429",
    "quota",
    "insufficient credits",
    "unauthorized",
    "401",
    "403",
    "invalid api key",
    "dns",
    "timed out",
    "timeout",
    "connection",
    "network",
    "503",
    "overloaded",
    "billing",
)

SECRET_RE = re.compile(
    r"(?i)(api[_-]?key|token|password|secret|authorization)\s*[:=]\s*\S+"
)
BEARER_RE = re.compile(r"(?i)bearer\s+[A-Za-z0-9._\-+=/]{8,}")
COOKIE_RE = re.compile(r"(?i)(cookie|set-cookie)\s*[:=]\s*\S+")
HOME_PATH_RE = re.compile(r"(?i)(/Users/[^/\s]+|/home/[^/\s]+|~[/\\][^\s]+)")
QUERY_TOKEN_RE = re.compile(
    r"(?i)([?&](access_token|token|key|auth|code|session)=)[^&\s]+"
)
PHONE_RE = re.compile(r"\b\+?\d[\d\s\-().]{8,}\b")
EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
CHAT_ID_RE = re.compile(r"(?i)(chat[_-]?id|thread[_-]?id|user[_-]?id)[\"'=:\s]+\S+")

# Diff secret scan (extend public-audit shapes).
DIFF_SECRET_PATTERNS = (
    re.compile(r"-----BEGIN (?:RSA |OPENSSH |EC )?PRIVATE KEY-----"),
    re.compile(r"(?i)bearer\s+[A-Za-z0-9._~+/=-]{20,}"),
    re.compile(r"(?:sk-|nvapi-|AIza|github_pat_|gh[pousr]_)[A-Za-z0-9_-]{20,}"),
    re.compile(
        r"eyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}"
    ),
    re.compile(r"(?i)auth_code=[A-Za-z0-9._~%+\-/]{8,}"),
)


@dataclass
class Classification:
    signature: str
    severity: str  # LOW | MEDIUM | HIGH | CRITICAL
    tier: int  # 0..3
    title: str
    task: str
    reason: str


def sanitize_for_opencode(text: str) -> str:
    """Strip PII/secrets before any model prompt; keep technical evidence."""
    out = text or ""
    out = BEARER_RE.sub("Bearer [REDACTED]", out)
    out = COOKIE_RE.sub(r"\1=[REDACTED]", out)
    out = QUERY_TOKEN_RE.sub(r"\1[REDACTED]", out)
    out = SECRET_RE.sub(r"\1=[REDACTED]", out)
    out = HOME_PATH_RE.sub("[HOME]", out)
    out = EMAIL_RE.sub("[EMAIL]", out)
    out = PHONE_RE.sub("[PHONE]", out)
    out = CHAT_ID_RE.sub(r"\1=[REDACTED]", out)
    return out[:4000]


def incident_signature(exc_type: str, message: str, module: str = "") -> str:
    """Stable dedup key — no PII, no raw message bodies."""
    low = (message or "").lower()
    norm = re.sub(r"[0-9]+", "N", low)
    norm = re.sub(r"/[^\s]+", "/PATH", norm)
    norm = re.sub(r"'[^']*'", "'X'", norm)
    norm = re.sub(r'"[^"]*"', '"X"', norm)
    norm = re.sub(r"\s+", " ", norm).strip()[:240]
    raw = f"{(exc_type or '').lower()}|{module or ''}|{norm}"
    return hashlib.sha256(raw.encode()).hexdigest()[:24]


def is_optional_warning(exc_type: str, message: str) -> bool:
    blob = f"{exc_type} {message}".lower()
    return any(m in blob for m in OPTIONAL_WARNING_MARKERS)


def classify_failure(
    exc_type: str,
    message: str,
    *,
    module: str = "",
    context: str = "",
) -> Optional[Classification]:
    """Return a Classification for meaningful failures only."""
    if is_optional_warning(exc_type, message):
        return None

    exc = (exc_type or "").strip()
    msg = message or ""
    low = msg.lower()
    mod = (module or "").lower()
    ctx = (context or "").lower()
    sig = incident_signature(exc, msg, module)

    if "choices must be a list of strings" in low or (
        "clarify" in low and "choices" in low and "list" in low
    ):
        return Classification(
            signature=sig,
            severity="MEDIUM",
            tier=3,
            title="clarify tool schema / greeting tool path regression",
            task=(
                "OpenCloud greeting turns are leaking into the clarify tool with "
                "invalid schema (choices must be a list of strings). Fix the "
                "conversational greeting classifier and/or tool_choice=none path "
                "so real-world short greetings (including colloquial vocatives) "
                "do not invoke clarify. Prefer editing "
                "integrations/hermes/hermes-product-reliability-ux.patch and "
                "related greeting patches only. Do not hard-code a single phrase."
            ),
            reason="clarify_schema_greeting",
        )

    if (exc or "").strip().lower() == "openclouduseroutputcontractviolation" or (
        "greeting_tool_text" in low
        and "agent.conversation_loop" in mod
    ):
        return Classification(
            signature=sig,
            severity="MEDIUM",
            tier=3,
            title="greeting output contract violation (tool JSON as text)",
            task=(
                "OpenCloud conversational greeting turns returned serialized tool "
                "or clarify JSON as plain text to the user. Fix "
                "integrations/hermes/hermes-greeting-output-contract.patch "
                "and related greeting patches: preserve tool_choice=none, enforce "
                "the greeting output contract, bounded repair, and local fallback. "
                "Do not reintroduce clarify for greetings."
            ),
            reason="greeting_output_contract",
        )

    if "same_tool_failure_halt" in low:
        return Classification(
            signature=sig,
            severity="HIGH",
            tier=3,
            title="same_tool_failure_halt",
            task=sanitize_for_opencode(
                "Fix repeated tool failure halt in Hermes/OpenCloud path: "
                + msg[:300]
            ),
            reason="same_tool_failure_halt",
        )

    if "gateway crash" in low or "main process exited" in low or "abnormal exit" in low:
        return Classification(
            signature=sig,
            severity="CRITICAL",
            tier=1,
            title="hermes-gateway crash",
            task=sanitize_for_opencode(f"Investigate gateway crash: {msg[:300]}"),
            reason="gateway_crash",
        )

    if "stuck turn" in low:
        return Classification(
            signature=sig,
            severity="HIGH",
            tier=1,
            title="stuck turn",
            task=sanitize_for_opencode(f"Recover stuck turn: {msg[:300]}"),
            reason="stuck_turn",
        )

    if exc.lower() in ("typeerror", "attributeerror") or "_opencloud_" in low:
        return Classification(
            signature=sig,
            severity="HIGH",
            tier=3 if "opencloud" in (low + mod + ctx) else 1,
            title=f"internal {exc or 'error'}",
            task=sanitize_for_opencode(
                f"Fix internal regression {exc}: {msg[:300]} (module={module})"
            ),
            reason="internal_code",
        )

    if any(m in low for m in EXTERNAL_PROVIDER_MARKERS):
        return Classification(
            signature=sig,
            severity="LOW",
            tier=2,
            title="provider/operational failure",
            task="Provider/Fleet recovery only; do not edit source for timeouts or quotas.",
            reason="external_provider",
        )

    if exc.lower() in ("valueerror", "runtimeerror", "assertionerror"):
        if "opencloud" in (low + mod + ctx) or "hermes" in (low + mod):
            return Classification(
                signature=sig,
                severity="MEDIUM",
                tier=3,
                title=f"{exc} in OpenCloud/Hermes path",
                task=sanitize_for_opencode(
                    f"Fix {exc} in OpenCloud/Hermes integration: {msg[:300]}"
                ),
                reason="integration_valueerror",
            )

    return None


def path_denied(rel: str) -> bool:
    rel = rel.replace("\\", "/")
    while rel.startswith("./"):
        rel = rel[2:]
    rel = rel.lstrip("/")
    if HERMES_LIVE_DENY.search(rel):
        return True
    return any(p.search(rel) for p in DENY_PATH_GLOBS)


def scan_diff_for_secrets(diff_text: str) -> Optional[str]:
    for pat in DIFF_SECRET_PATTERNS:
        if pat.search(diff_text or ""):
            return f"secret_pattern:{pat.pattern[:60]}"
    return None


def discover_free_models(
    opencode_bin: str = "opencode",
    runner: Optional[Callable[..., subprocess.CompletedProcess]] = None,
) -> list[str]:
    """Live ``opencode models opencode`` discovery; empty on failure."""
    run = runner or subprocess.run
    try:
        proc = run(
            [opencode_bin, "models", "opencode"],
            capture_output=True,
            text=True,
            timeout=60,
            shell=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    if proc.returncode != 0:
        return []
    models: list[str] = []
    for line in (proc.stdout or "").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if " " in line:
            line = line.split()[0]
        if "/" in line or line.startswith("opencode/"):
            models.append(line if "/" in line else f"opencode/{line}")
        elif line:
            models.append(f"opencode/{line}")
    free = [m for m in models if "free" in m.lower()]
    return (free or models)[: max(MAX_MODELS * 2, 6)]


def pick_models(available: list[str], preferred: Optional[list[str]] = None) -> list[str]:
    preferred = preferred or [
        m.strip()
        for m in os.environ.get("OPEN_CLOUD_SELF_HEAL_MODELS", "").split(",")
        if m.strip()
    ]
    out: list[str] = []
    for m in preferred + available:
        if m not in out:
            out.append(m)
        if len(out) >= MAX_MODELS:
            break
    return out


def git_rev(path: Path, ref: str = "HEAD") -> tuple[str, str]:
    """Return (head, tree) or ('', '') on failure — never invent success."""
    try:
        h = subprocess.run(
            ["git", "-C", str(path), "rev-parse", ref],
            capture_output=True,
            text=True,
            timeout=30,
            shell=False,
        )
        if h.returncode != 0:
            return "", ""
        head = (h.stdout or "").strip()
        t = subprocess.run(
            ["git", "-C", str(path), "rev-parse", f"{head}^{{tree}}"],
            capture_output=True,
            text=True,
            timeout=30,
            shell=False,
        )
        if t.returncode != 0:
            return head, ""
        return head, (t.stdout or "").strip()
    except (OSError, subprocess.TimeoutExpired):
        return "", ""


def assert_safe_workdir(
    workdir: Path,
    *,
    repo_root: Path,
    worktrees_root: Path,
) -> Optional[str]:
    """Canonical worktree guard. Fail → caller must not run OpenCode.

    Invariants:
    - workdir ≠ repo_root
    - workdir ≠ live Hermes and not inside it
    - workdir is beneath self-heal worktrees_root
    - worktrees_root is outside canonical repo_root
    - no symlink escape out of worktrees_root
    """
    try:
        wt = workdir.resolve(strict=False)
        repo = repo_root.resolve(strict=False)
        wroot = worktrees_root.resolve(strict=False)
        hermes = (Path.home() / ".hermes" / "hermes-agent").resolve(strict=False)
    except OSError as exc:
        return f"path resolve failed: {exc}"

    if wroot == repo or repo in wroot.parents:
        return "worktrees_root must be outside canonical repo_root"
    if wt == repo:
        return "workdir must not be canonical repo_root"
    if wt == hermes or hermes in wt.parents:
        return "workdir must not be live Hermes tree"
    try:
        wt.relative_to(wroot)
    except ValueError:
        return "workdir must be beneath self-heal worktrees root"

    # Walk parents: any symlink whose resolve leaves wroot is an escape.
    cur = workdir
    for _ in range(64):
        if cur.is_symlink():
            try:
                resolved = cur.resolve(strict=False)
                resolved.relative_to(wroot)
            except (OSError, ValueError):
                return f"symlink escape at {cur}"
        if cur.resolve(strict=False) == wroot:
            break
        if cur.parent == cur:
            break
        cur = cur.parent
    return None


class OpenCodeRunner:
    """Fixed-argv OpenCode invocation. Never shell=True. Nonzero ≠ success."""

    def __init__(
        self,
        *,
        opencode_bin: Optional[str] = None,
        timeout: int = REPAIR_TIMEOUT,
        runner: Optional[Callable[..., subprocess.CompletedProcess]] = None,
    ):
        self.opencode_bin = opencode_bin or os.environ.get(
            "OPEN_CLOUD_SELF_HEAL_OPENCODE", "opencode"
        )
        self.timeout = timeout
        self._runner = runner or subprocess.run

    def run(
        self,
        *,
        workdir: Path,
        prompt: str,
        model: str,
        agent: Optional[str] = None,
    ) -> tuple[bool, str]:
        argv = [self.opencode_bin, "run", "--dir", str(workdir)]
        if model:
            argv.extend(["--model", model])
        if agent:
            argv.extend(["--agent", agent])
        argv.append(prompt)
        assert isinstance(argv, list)
        try:
            proc = self._runner(
                argv,
                capture_output=True,
                text=True,
                timeout=self.timeout,
                shell=False,
                cwd=str(workdir),
            )
        except subprocess.TimeoutExpired as exc:
            return False, f"timeout: {exc}"
        except OSError as exc:
            return False, f"exec_failed: {exc}"
        out = ((proc.stdout or "") + "\n" + (proc.stderr or "")).strip()
        if proc.returncode != 0:
            return False, f"nonzero={proc.returncode}: {out[:1000]}"
        return True, out[:2000]


class SelfHealController:
    def __init__(
        self,
        *,
        repo_root: Path,
        state_root: Optional[Path] = None,
        store: Optional[IncidentStore] = None,
        opencode: Optional[OpenCodeRunner] = None,
        validate_cmd: Optional[Callable[[Path], tuple[bool, str]]] = None,
        canary: Optional[CanaryAdapter] = None,
        promoter: Optional[GitHubPromoter] = None,
        deployer: Optional[DeployAdapter] = None,
        p8: Optional[P8RuntimeAdapter] = None,
        runtime: Optional[RuntimeServiceAdapter] = None,
        fleet: Optional[FleetAdapter] = None,
        notify_fn: Optional[Callable[[str], None]] = None,
        now: Optional[Callable[[], float]] = None,
        test_mode: Optional[bool] = None,
        worker_id: Optional[str] = None,
    ):
        self.repo_root = Path(repo_root).resolve()
        self.state_root = Path(state_root or DEFAULT_STATE_ROOT)
        self.state_root.mkdir(parents=True, exist_ok=True)
        self.store = store or IncidentStore(self.state_root / "incidents.sqlite")
        self.opencode = opencode or OpenCodeRunner()
        self.validate_cmd = validate_cmd or self._default_validate
        self.canary = canary or CanaryAdapter()
        self.promoter = promoter or GitHubPromoter()
        self.deployer = deployer or DeployAdapter(repo_root=self.repo_root)
        self.p8 = p8  # legacy/manual only — automatic queue never invokes P8
        self.runtime = runtime or RuntimeServiceAdapter()
        self.fleet = fleet or FleetAdapter()
        self.notify_fn = notify_fn or (lambda _msg: None)
        self._now = now or time.time
        self.test_mode = TEST_MODE if test_mode is None else test_mode
        self.worker_id = worker_id or f"worker-{os.getpid()}"
        self.worktrees = self.state_root / "worktrees"
        self.worktrees.mkdir(parents=True, exist_ok=True)
        self.artifacts = self.state_root / "artifacts"
        self.artifacts.mkdir(parents=True, exist_ok=True)
        self.inbox = self.state_root / "inbox"
        self.inbox.mkdir(parents=True, exist_ok=True)
    # ── public API ───────────────────────────────────────────────────────

    def status(self) -> dict[str, Any]:
        return {
            "enabled": self.store.enabled(),
            "state_root": str(self.state_root),
            "open_incidents": sum(
                1
                for i in self.store.list_incidents(100)
                if i["state"]
                not in (
                    "RECOVERED",
                    "ROLLED_BACK",
                    "ROLLBACK_FAILED",
                    "FAILED",
                    "NO_ACTION_TRANSIENT",
                    "DISABLED",
                    "QUARANTINED",
                )
            ),
            "incidents_last_hour": self.store.circuit_count("incidents", 3600),
            "repair_attempts_last_hour": self.store.circuit_count(
                "repair_attempts", 3600
            ),
            "promotions_last_hour": self.store.circuit_count("promotions", 3600),
            "deploys_last_hour": self.store.circuit_count("deploys", 3600),
            "rollbacks_last_hour": self.store.circuit_count("rollbacks", 3600),
        }

    def ingest(
        self,
        exc_type: str,
        message: str,
        *,
        module: str = "",
        context: str = "",
        auto_run: bool = True,
    ) -> Optional[dict[str, Any]]:
        if not self.store.enabled():
            return None
        classified = classify_failure(
            exc_type, message, module=module, context=context
        )
        if classified is None:
            return None
        if classified.tier == 0:
            return None

        existing = self.store.find_open_by_signature(classified.signature)
        if existing:
            bumped = self.store.bump_occurrence(existing["id"])
            # If already QUEUED/RETRY_PENDING, stay queued — no storm.
            return bumped

        recent = self.store.find_recent_by_signature(
            classified.signature, REOPEN_WINDOW
        )
        # Recent FAILED same signature → bump/reopen, not a storm of siblings.
        if recent and recent["state"] == "FAILED":
            occ = int(recent.get("occurrence_count") or 1) + 1
            if occ >= MAX_OCCURRENCES_BEFORE_HUMAN:
                return self.store.transition(
                    recent["id"],
                    "HUMAN_REQUIRED",
                    detail=f"reoccurrence_count={occ}",
                    meta_update={"occurrence_escalation": occ},
                )
            # Reopen same row into QUEUED (dedup).
            with self.store._connect() as conn:  # noqa: SLF001
                conn.execute(
                    "UPDATE incidents SET occurrence_count=?, updated_at=? WHERE id=?",
                    (occ, self._now(), recent["id"]),
                )
                conn.commit()
            self.store.transition(
                recent["id"],
                "QUEUED",
                detail=f"reopened_failed occ={occ}",
                meta_update={"reopened_from_failed": True},
            )
            row = self.store.get(recent["id"])
            if auto_run and row:
                return self.process(row["id"])
            return row

        if recent and recent["state"] in REOPENABLE:
            # Reopen: new incident, escalate if repeats.
            occ = int(recent.get("occurrence_count") or 1) + 1
            if occ >= MAX_OCCURRENCES_BEFORE_HUMAN:
                incident = self.store.create(
                    signature=classified.signature,
                    title=classified.title,
                    sanitized_task=sanitize_for_opencode(classified.task),
                    severity=classified.severity,
                    tier=classified.tier,
                    meta={
                        "reason": classified.reason,
                        "module": module,
                        "reopened_from": recent["id"],
                    },
                )
                self.store.transition(
                    incident["id"],
                    "HUMAN_REQUIRED",
                    detail=f"reoccurrence_count={occ}",
                    meta_update={"occurrence_escalation": occ},
                )
                # Sync occurrence on the new row.
                with self.store._connect() as conn:  # noqa: SLF001
                    conn.execute(
                        "UPDATE incidents SET occurrence_count=? WHERE id=?",
                        (occ, incident["id"]),
                    )
                    conn.commit()
                return self.store.get(incident["id"])

        failed_sha = (recent or {}).get("meta", {}).get("merged_head") or (
            recent or {}
        ).get("meta", {}).get("repair_commit")
        if failed_sha and self.store.is_quarantined(failed_sha):
            # Do not retry a quarantined SHA — escalate.
            incident = self.store.create(
                signature=classified.signature,
                title=classified.title,
                sanitized_task=sanitize_for_opencode(classified.task),
                severity=classified.severity,
                tier=classified.tier,
                meta={
                    "reason": classified.reason,
                    "module": module,
                    "blocked_quarantined_sha": failed_sha,
                },
            )
            return self.store.transition(
                incident["id"],
                "HUMAN_REQUIRED",
                detail=f"quarantined sha {failed_sha[:12]} not retried",
            )

        if self.store.circuit_count("incidents", 3600) >= MAX_INCIDENTS_PER_HOUR:
            self.notify_fn("self-heal circuit open: max incidents/hour")
            return None

        self.store.circuit_bump("incidents", 3600)
        incident = self.store.create(
            signature=classified.signature,
            title=classified.title,
            sanitized_task=sanitize_for_opencode(classified.task),
            severity=classified.severity,
            tier=classified.tier,
            meta={"reason": classified.reason, "module": module},
        )
        self.store.transition(incident["id"], "CAPTURED", detail="sanitized")
        self.store.transition(
            incident["id"],
            "CLASSIFIED",
            tier=classified.tier,
            detail=classified.reason,
        )
        # Detector / enqueue path: QUEUED ≠ RECOVERING.
        self.store.transition(
            incident["id"],
            "QUEUED",
            detail="awaiting controller worker",
        )
        self.notify_fn(
            f"incident {incident['id']}: {classified.title} tier={classified.tier}"
        )
        if auto_run:
            return self.process(incident["id"])
        return self.store.get(incident["id"])

    def scan_inbox(self, *, auto_run: bool = False) -> list[dict[str, Any]]:
        """Auto-ingest production-style error events from inbox/*.json → QUEUED."""
        results: list[dict[str, Any]] = []
        if not self.inbox.is_dir():
            return results
        for path in sorted(self.inbox.glob("*.json")):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                path.rename(path.with_suffix(".bad"))
                continue
            row = self.ingest(
                str(payload.get("exc_type") or payload.get("type") or "Error"),
                str(payload.get("message") or payload.get("error") or ""),
                module=str(payload.get("module") or ""),
                context=str(payload.get("context") or ""),
                auto_run=auto_run,
            )
            try:
                path.unlink()
            except OSError:
                pass
            if row:
                results.append(row)
        return results

    def _lease_meta(self) -> dict[str, Any]:
        started = self._now()
        return {
            "worker_id": self.worker_id,
            "worker_started_at": started,
            "lease_expires_at": started + LEASE_SECONDS,
        }

    def _lease_expired(self, incident: dict[str, Any]) -> bool:
        meta = incident.get("meta") or {}
        expires = meta.get("lease_expires_at")
        # Legacy RECOVERING without lease (production stuck rows) → stale.
        if expires is None and not meta.get("worker_id"):
            return True
        try:
            return float(expires) <= self._now()
        except (TypeError, ValueError):
            return True

    def reap_stale_leases(self) -> list[dict[str, Any]]:
        """Expired / legacy RECOVERING → INTERRUPTED / RETRY_PENDING / HUMAN_REQUIRED."""
        out: list[dict[str, Any]] = []
        for row in self.store.list_by_states(("RECOVERING",), limit=50):
            if not self._lease_expired(row):
                continue
            attempts = int(row.get("attempts") or 0)
            clear_lease = {
                "worker_id": None,
                "worker_started_at": None,
                "lease_expires_at": None,
                "lease_interrupted": True,
            }
            if attempts >= MAX_ATTEMPTS:
                out.append(
                    self.store.transition(
                        row["id"],
                        "HUMAN_REQUIRED",
                        detail="stale RECOVERING lease; max attempts",
                        error="lease_expired",
                        meta_update=clear_lease,
                    )
                )
            else:
                out.append(
                    self.store.transition(
                        row["id"],
                        "RETRY_PENDING",
                        detail="stale RECOVERING lease → retry",
                        error="lease_expired",
                        meta_update=clear_lease,
                    )
                )
                self.store.transition(
                    row["id"],
                    "QUEUED",
                    detail="requeued after interrupted lease",
                )
                out[-1] = self.store.get(row["id"])  # type: ignore[assignment]
        return out

    def process_queue(self, *, limit: Optional[int] = None) -> list[dict[str, Any]]:
        """Controller worker: reap leases, then process up to N QUEUED/RETRY incidents."""
        self.reap_stale_leases()
        if not self.store.enabled():
            return []
        n = limit if limit is not None else MAX_QUEUE_PER_TICK
        n = max(1, min(3, int(n)))
        queued = self.store.list_by_states(
            ("QUEUED", "RETRY_PENDING", "INTERRUPTED"), limit=n
        )
        results: list[dict[str, Any]] = []
        for row in queued:
            # Respect circuit before each recovery attempt.
            if self.store.circuit_count("repair_attempts", 3600) >= MAX_REPAIRS_PER_HOUR:
                self.notify_fn("self-heal repair circuit open; stopping queue tick")
                break
            results.append(self.process(row["id"]))
        return results

    def process(self, incident_id: str) -> dict[str, Any]:
        incident = self.store.get(incident_id)
        if not incident:
            raise KeyError(incident_id)
        if not self.store.enabled():
            return self.store.transition(incident_id, "DISABLED")

        tier = int(incident["tier"])
        if incident["attempts"] >= MAX_ATTEMPTS:
            return self.store.transition(
                incident_id,
                "HUMAN_REQUIRED",
                error="max attempts exceeded",
            )

        if tier == 0:
            return self.store.transition(
                incident_id, "NO_ACTION_TRANSIENT", detail="tier0"
            )

        if tier == 1:
            return self._tier1_runtime(incident_id)

        if tier == 2:
            return self._tier2_fleet(incident_id)

        return self._source_repair(incident_id)

    def retry(self, incident_id: str) -> dict[str, Any]:
        incident = self.store.get(incident_id)
        if not incident:
            raise KeyError(incident_id)
        sha = incident.get("meta", {}).get("merged_head") or incident.get(
            "meta", {}
        ).get("repair_commit")
        if sha and self.store.is_quarantined(sha):
            return self.store.transition(
                incident_id,
                "HUMAN_REQUIRED",
                detail="refusing retry of quarantined sha",
            )
        self.store.transition(
            incident_id, "QUEUED", detail="manual retry", bump_attempt=False
        )
        return self.process(incident_id)

    # ── tiers 1 / 2 ──────────────────────────────────────────────────────

    def _begin_recovering(self, incident_id: str, detail: str) -> dict[str, Any]:
        return self.store.transition(
            incident_id,
            "RECOVERING",
            detail=detail,
            bump_attempt=True,
            meta_update=self._lease_meta(),
        )

    def _tier1_runtime(self, incident_id: str) -> dict[str, Any]:
        """Gateway crash / stuck turn / lifecycle → RuntimeServiceAdapter only.

        Any other Tier-1 reason fails closed — never P8 / hermes-code-repair.
        """
        incident = self.store.get(incident_id)
        assert incident
        reason = str((incident.get("meta") or {}).get("reason") or "")
        title_l = (incident.get("title") or "").lower()

        if reason in RUNTIME_TIER1_REASONS or "crash" in title_l or "stuck" in title_l:
            self._begin_recovering(incident_id, "tier1_runtime")
            self.store.circuit_bump("repair_attempts", 3600)
            if reason == "stuck_turn" or "stuck" in title_l:
                result = self.runtime.recover_stuck_turn()
            else:
                result = self.runtime.recover_crash(reason=reason or "gateway_crash")
            meta = {
                "runtime_status": result.status,
                "runtime_detail": result.detail,
                "runtime_verified": result.verified,
                "p8_used": False,
                "hermes_code_repair": False,
            }
            if result.verified and result.status == "RECOVERED":
                return self.store.transition(
                    incident_id, "RECOVERED", detail=result.detail, meta_update=meta
                )
            if result.status == "NO_ACTION_TRANSIENT":
                return self.store.transition(
                    incident_id,
                    "NO_ACTION_TRANSIENT",
                    detail=result.detail,
                    meta_update=meta,
                )
            if result.status == "FAILED":
                return self.store.transition(
                    incident_id, "FAILED", error=result.detail, meta_update=meta
                )
            return self.store.transition(
                incident_id,
                "HUMAN_REQUIRED",
                detail=result.detail,
                meta_update=meta,
            )

        return self.store.transition(
            incident_id,
            "HUMAN_REQUIRED",
            detail=f"unsupported_tier1_runtime_reason={reason or 'unknown'}",
            meta_update={"p8_used": False, "hermes_code_repair": False},
        )

    def _tier1_p8(self, incident_id: str) -> dict[str, Any]:
        incident = self.store.get(incident_id)
        assert incident
        self._begin_recovering(incident_id, "tier1_p8")
        self.store.circuit_bump("repair_attempts", 3600)
        result = self.p8.attempt(
            task=incident["sanitized_task"],
            fingerprint=incident["signature"],
        )
        meta = {
            "p8_status": result.status,
            "p8_detail": result.detail,
            "p8_verified": result.verified,
        }
        if result.verified and result.status == "RECOVERED":
            return self.store.transition(
                incident_id, "RECOVERED", detail=result.detail, meta_update=meta
            )
        if result.status == "FAILED":
            return self.store.transition(
                incident_id, "FAILED", error=result.detail, meta_update=meta
            )
        return self.store.transition(
            incident_id,
            "HUMAN_REQUIRED",
            detail=result.detail,
            meta_update=meta,
        )

    def _tier2_fleet(self, incident_id: str) -> dict[str, Any]:
        self._begin_recovering(incident_id, "tier2_fleet")
        result = self.fleet.recover_transient()
        meta = {
            "fleet_status": result.status,
            "fleet_detail": result.detail,
            "fleet_verified": result.verified,
            "openrouter_free_preserved": True,
            "gemini_blocked": True,
        }
        # Transient: never claim source recovery. Verified probe → NO_ACTION.
        if result.status == "HUMAN_REQUIRED":
            return self.store.transition(
                incident_id,
                "HUMAN_REQUIRED",
                detail=result.detail,
                meta_update=meta,
            )
        return self.store.transition(
            incident_id,
            "NO_ACTION_TRANSIENT",
            detail=result.detail,
            meta_update=meta,
        )
    # ── tier-3 source repair ─────────────────────────────────────────────

    def _source_repair(self, incident_id: str) -> dict[str, Any]:
        incident = self.store.get(incident_id)
        assert incident
        severity = incident.get("severity") or "MEDIUM"
        if severity in ("HIGH", "CRITICAL"):
            return self.store.transition(
                incident_id,
                "HUMAN_REQUIRED",
                detail=f"severity={severity} never auto-promotes",
            )

        self.store.transition(
            incident_id, "RECOVERING", detail="source repair", bump_attempt=True,
            meta_update=self._lease_meta(),
        )
        if self.store.circuit_count("repair_attempts", 3600) >= MAX_REPAIRS_PER_HOUR:
            return self.store.transition(
                incident_id, "HUMAN_REQUIRED", error="repair circuit open"
            )
        self.store.circuit_bump("repair_attempts", 3600)

        base_head, base_tree = git_rev(self.repo_root, "HEAD")
        if not base_head or not base_tree:
            return self.store.transition(
                incident_id,
                "FAILED",
                error="unable to record immutable BASE_HEAD/BASE_TREE",
            )
        self.store.transition(
            incident_id,
            "RECOVERING",
            detail="base recorded",
            meta_update={"base_head": base_head, "base_tree": base_tree},
            bump_attempt=False,
        )

        wt = self._prepare_worktree(incident_id)
        if wt is None:
            return self.store.transition(
                incident_id,
                "FAILED",
                error="worktree/clone prepare failed (fail closed; no rsync)",
            )

        guard = assert_safe_workdir(
            wt, repo_root=self.repo_root, worktrees_root=self.worktrees
        )
        if guard:
            self._cleanup_worktree(wt)
            return self.store.transition(
                incident_id, "HUMAN_REQUIRED", error=f"workdir guard: {guard}"
            )

        models = pick_models(discover_free_models(self.opencode.opencode_bin))
        is_fake_bin = str(self.opencode.opencode_bin).endswith("fake-opencode")
        if not models:
            if self.test_mode or is_fake_bin:
                models = ["opencode/fake"]
            else:
                self._cleanup_worktree(wt)
                return self.store.transition(
                    incident_id,
                    "REPAIR_ENGINE_UNAVAILABLE",
                    error="no free models discovered; refusing fake model in production",
                )

        repair_ok = False
        last_err = ""
        used_model = ""
        for model in models:
            used_model = model
            if model == "opencode/fake" and not (self.test_mode or is_fake_bin):
                continue
            ok, out = self.opencode.run(
                workdir=wt,
                prompt=incident["sanitized_task"],
                model=model,
                agent=os.environ.get("OPEN_CLOUD_SELF_HEAL_AGENT") or None,
            )
            if ok:
                repair_ok = True
                break
            last_err = out
        if not repair_ok:
            self._cleanup_worktree(wt)
            return self.store.transition(
                incident_id,
                "FAILED",
                error=f"opencode failed: {last_err[:500]}",
                meta_update={"model": used_model},
            )

        boundary = self._check_boundaries(wt)
        if boundary:
            self._cleanup_worktree(wt)
            return self.store.transition(
                incident_id, "HUMAN_REQUIRED", error=boundary
            )

        diff_text = self._git_diff(wt)
        secret_hit = scan_diff_for_secrets(diff_text)
        if secret_hit:
            self._cleanup_worktree(wt)
            return self.store.transition(
                incident_id,
                "HUMAN_REQUIRED_SECURITY",
                error=f"diff secret scan: {secret_hit}",
                meta_update={"secret_scan": secret_hit},
            )

        self.store.transition(incident_id, "VALIDATING")
        v_ok, v_detail = self.validate_cmd(wt)
        if not v_ok:
            self._cleanup_worktree(wt)
            # Distinguish timeout/unknown from plain fail via detail prefix.
            state = "VALIDATION_FAILED"
            if "timeout" in v_detail.lower():
                state = "VALIDATION_FAILED"
            return self.store.transition(
                incident_id, state, error=f"validate: {v_detail[:500]}"
            )

        self.store.transition(incident_id, "REVIEWING")
        review = self._multi_model_review(wt, incident, models, used_model)
        if review == "REJECT":
            self._cleanup_worktree(wt)
            return self.store.transition(
                incident_id, "FAILED", error="multi-model review REJECT"
            )
        if review in ("UNCERTAIN", "UNCERTAIN_SINGLE_REVIEWER"):
            art = self._save_artifact(incident_id, wt, diff_text)
            self._cleanup_worktree(wt)
            return self.store.transition(
                incident_id,
                "HUMAN_REQUIRED",
                detail=review,
                meta_update={"artifact": art, "review": review},
            )

        self.store.transition(
            incident_id,
            "REPAIR_VALIDATED",
            detail="validate+review ok",
            meta_update={"model": used_model, "review": review},
        )

        # Pre-promotion canary — failure is NOT rollback.
        self.store.transition(incident_id, "CANARY")
        c_ok, c_detail = self.canary.pre_promotion(wt)
        if not c_ok:
            self._cleanup_worktree(wt)
            self.notify_fn(f"incident {incident_id}: pre-canary FAIL")
            return self.store.transition(
                incident_id,
                "CANARY_FAILED",
                error=f"pre-promotion canary: {c_detail[:500]}",
                meta_update={"canary": c_detail},
            )

        allow_auto = severity == "LOW" or (
            severity == "MEDIUM"
            and os.environ.get("OPEN_CLOUD_SELF_HEAL_ALLOW_MEDIUM_AUTOMERGE", "")
            == "1"
        )
        # HIGH/CRITICAL already blocked above.

        if self.store.circuit_count("promotions", 3600) >= MAX_PROMOTIONS_PER_HOUR:
            art = self._save_artifact(incident_id, wt, diff_text)
            self._cleanup_worktree(wt)
            return self.store.transition(
                incident_id,
                "READY_FOR_PROMOTION",
                detail="promotion circuit open",
                meta_update={"artifact": art, "canary": c_detail},
            )

        self.store.circuit_bump("promotions", 3600)
        promo = self.promoter.promote(
            wt,
            incident_id=incident_id,
            severity=severity,
            base_head=base_head,
            allow_auto_merge=allow_auto,
        )
        art = self._save_artifact(incident_id, wt, diff_text)
        meta = {
            "artifact": art,
            "canary": c_detail,
            "model": used_model,
            "review": review,
            "base_head": base_head,
            "base_tree": base_tree,
            "repair_branch": promo.repair_branch,
            "repair_commit": promo.repair_commit,
            "pr_number": promo.pr_number,
            "merged_head": promo.merged_head,
            "merged_tree": promo.merged_tree,
            "gh_auth_ok": promo.gh_auth_ok,
            "pushed": promo.pushed,
            "checks_passed": promo.checks_passed,
            "merged": promo.merged,
            "promotion_status": promo.status,
            "promotion_detail": promo.detail,
            "private_sync": "PRIVATE_SYNC_ELIGIBLE"
            if promo.merged
            else "NOT_ELIGIBLE",
        }

        if promo.status == "GITHUB_PROMOTION_UNAVAILABLE":
            self._cleanup_worktree(wt)
            self.notify_fn(
                f"incident {incident_id}: canary PASS; github promotion unavailable"
            )
            return self.store.transition(
                incident_id,
                "READY_FOR_PROMOTION",
                detail="GITHUB_PROMOTION_UNAVAILABLE; local artifact only",
                meta_update=meta,
            )

        if promo.status == "READY_FOR_PROMOTION":
            self._cleanup_worktree(wt)
            return self.store.transition(
                incident_id,
                "READY_FOR_PROMOTION",
                detail=promo.detail,
                meta_update=meta,
            )

        if promo.status == "PR_OPEN":
            self._cleanup_worktree(wt)
            return self.store.transition(
                incident_id, "PR_OPEN", detail=promo.detail, meta_update=meta
            )

        if promo.status == "CI_RUNNING":
            self._cleanup_worktree(wt)
            return self.store.transition(
                incident_id, "CI_RUNNING", detail=promo.detail, meta_update=meta
            )

        if promo.status == "HUMAN_REQUIRED":
            self._cleanup_worktree(wt)
            return self.store.transition(
                incident_id,
                "HUMAN_REQUIRED",
                detail=promo.detail,
                meta_update=meta,
            )

        if promo.status != "PROMOTED" or not promo.merged or not promo.merged_head:
            self._cleanup_worktree(wt)
            return self.store.transition(
                incident_id,
                "FAILED",
                error=f"promotion incomplete: {promo.status} {promo.detail}",
                meta_update=meta,
            )

        # PROMOTED — only after accepted public GitHub change.
        self.store.transition(
            incident_id, "PROMOTED", detail=promo.detail, meta_update=meta
        )
        self._cleanup_worktree(wt)

        return self._deploy_and_canary(incident_id)

    def _deploy_and_canary(self, incident_id: str) -> dict[str, Any]:
        incident = self.store.get(incident_id)
        assert incident
        meta = dict(incident.get("meta") or {})
        merged_sha = meta.get("merged_head") or ""
        if not merged_sha:
            return self.store.transition(
                incident_id,
                "FAILED",
                error="missing merged_head; cannot deploy",
            )
        if self.store.is_quarantined(merged_sha):
            return self.store.transition(
                incident_id,
                "QUARANTINED",
                detail="merged sha already quarantined; refusing deploy",
            )

        if self.store.circuit_count("deploys", 3600) >= MAX_DEPLOYS_PER_HOUR:
            return self.store.transition(
                incident_id,
                "HUMAN_REQUIRED",
                error="deploy circuit open",
            )

        self.store.circuit_bump("deploys", 3600)
        self.store.transition(incident_id, "DEPLOYING", detail=merged_sha)
        dep = self.deployer.deploy(
            merged_sha, quarantined=self.store.is_quarantined(merged_sha)
        )
        meta.update(
            {
                "previous_deployed_head": dep.previous_head,
                "previous_deployed_tree": dep.previous_tree,
                "deployed_head": dep.deployed_head,
                "deployed_tree": dep.deployed_tree,
                "deploy_status": dep.status,
                "deploy_detail": dep.detail,
            }
        )
        if dep.status != "DEPLOYED" or dep.deployed_head != merged_sha:
            return self.store.transition(
                incident_id,
                "ROLLBACK_REQUIRED" if dep.previous_head else "FAILED",
                error=f"deploy: {dep.detail}",
                meta_update=meta,
            )

        self.store.transition(
            incident_id, "DEPLOYED", detail=dep.detail, meta_update=meta
        )
        self.store.transition(incident_id, "POST_DEPLOY_CANARY")
        pc_ok, pc_detail = self.canary.post_deploy(
            materialized_hint=dep.deployed_head
        )
        meta["post_canary"] = pc_detail
        if not pc_ok:
            return self._rollback(
                incident_id,
                previous_sha=dep.previous_head,
                failed_sha=merged_sha,
                reason=pc_detail,
                meta=meta,
            )

        meta["private_sync"] = "PRIVATE_SYNC_ELIGIBLE"
        self.notify_fn(f"incident {incident_id}: RECOVERED")
        return self.store.transition(
            incident_id,
            "RECOVERED",
            detail="post-deploy canary PASS",
            meta_update=meta,
        )

    def _rollback(
        self,
        incident_id: str,
        *,
        previous_sha: str,
        failed_sha: str,
        reason: str,
        meta: dict,
    ) -> dict[str, Any]:
        self.store.transition(
            incident_id, "ROLLBACK_REQUIRED", detail=reason, meta_update=meta
        )
        # Quarantine failed repair SHA before/while rollback — never auto-redeploy.
        self.store.quarantine_sha(failed_sha, reason)
        meta = {**meta, "quarantined_sha": failed_sha}
        if self.store.circuit_count("rollbacks", 3600) >= MAX_ROLLBACKS_PER_HOUR:
            return self.store.transition(
                incident_id,
                "ROLLBACK_FAILED",
                error="rollback circuit open; sha quarantined",
                meta_update={
                    **meta,
                    "severity_escalation": "CRITICAL",
                    "human_required": True,
                },
            )
        self.store.circuit_bump("rollbacks", 3600)
        prev_tree = str(meta.get("previous_deployed_tree") or "")
        try:
            rb = self.deployer.rollback(
                previous_sha,
                previous_tree=prev_tree,
                post_canary=lambda: self.canary.post_deploy(
                    materialized_hint=previous_sha
                ),
            )
        except Exception as exc:  # noqa: BLE001 — never claim ROLLED_BACK
            return self.store.transition(
                incident_id,
                "ROLLBACK_FAILED",
                error=f"rollback exception: {exc}",
                meta_update={
                    **meta,
                    "severity_escalation": "CRITICAL",
                    "human_required": True,
                },
            )
        meta.update(
            {
                "rollback_status": rb.status,
                "rollback_detail": rb.detail,
                "rollback_head": rb.deployed_head,
                "rollback_tree": rb.deployed_tree,
            }
        )
        self.store.transition(
            incident_id, "QUARANTINED", detail=f"quarantined {failed_sha[:12]}"
        )
        if rb.status != "ROLLED_BACK":
            return self.store.transition(
                incident_id,
                "ROLLBACK_FAILED",
                error=f"rollback failed: {rb.detail}",
                meta_update={
                    **meta,
                    "severity_escalation": "CRITICAL",
                    "human_required": True,
                },
            )
        meta["prefer_revert_pr"] = True
        return self.store.transition(
            incident_id,
            "ROLLED_BACK",
            detail=rb.detail,
            meta_update=meta,
        )

    def _prepare_worktree(self, incident_id: str) -> Optional[Path]:
        dest = self.worktrees / incident_id
        if dest.exists():
            shutil.rmtree(dest)
        # git worktree first.
        try:
            proc = subprocess.run(
                [
                    "git",
                    "-C",
                    str(self.repo_root),
                    "worktree",
                    "add",
                    "--detach",
                    str(dest),
                    "HEAD",
                ],
                capture_output=True,
                text=True,
                timeout=120,
                shell=False,
            )
            if proc.returncode == 0:
                return dest
        except (OSError, subprocess.TimeoutExpired):
            pass
        # Genuine clone only (no rsync non-git fallback).
        try:
            if dest.exists():
                shutil.rmtree(dest)
            proc = subprocess.run(
                [
                    "git",
                    "clone",
                    "--local",
                    "--no-hardlinks",
                    str(self.repo_root),
                    str(dest),
                ],
                capture_output=True,
                text=True,
                timeout=180,
                shell=False,
            )
            if proc.returncode == 0:
                return dest
        except (OSError, subprocess.TimeoutExpired):
            pass
        shutil.rmtree(dest, ignore_errors=True)
        return None

    def _cleanup_worktree(self, wt: Path) -> None:
        try:
            subprocess.run(
                [
                    "git",
                    "-C",
                    str(self.repo_root),
                    "worktree",
                    "remove",
                    "--force",
                    str(wt),
                ],
                capture_output=True,
                timeout=60,
                shell=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            pass
        if wt.exists():
            shutil.rmtree(wt, ignore_errors=True)

    def _git_diff(self, wt: Path) -> str:
        try:
            proc = subprocess.run(
                ["git", "-C", str(wt), "diff", "HEAD"],
                capture_output=True,
                text=True,
                timeout=60,
                shell=False,
            )
            return proc.stdout or ""
        except (OSError, subprocess.TimeoutExpired):
            return ""

    def _changed_files(self, wt: Path) -> list[tuple[str, int]]:
        try:
            proc = subprocess.run(
                ["git", "-C", str(wt), "diff", "--numstat", "HEAD"],
                capture_output=True,
                text=True,
                timeout=60,
                shell=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            return []
        out: list[tuple[str, int]] = []
        for line in (proc.stdout or "").splitlines():
            parts = line.split("\t")
            if len(parts) != 3:
                continue
            add_s, del_s, path = parts
            try:
                lines = (0 if add_s == "-" else int(add_s)) + (
                    0 if del_s == "-" else int(del_s)
                )
            except ValueError:
                lines = 0
            out.append((path, lines))
        try:
            proc2 = subprocess.run(
                ["git", "-C", str(wt), "ls-files", "--others", "--exclude-standard"],
                capture_output=True,
                text=True,
                timeout=60,
                shell=False,
            )
            for path in (proc2.stdout or "").splitlines():
                if path.strip():
                    out.append((path.strip(), 0))
        except (OSError, subprocess.TimeoutExpired):
            pass
        return out

    def _check_boundaries(self, wt: Path) -> str:
        changed = self._changed_files(wt)
        if not changed:
            return "no source change detected"
        if len(changed) > MAX_CHANGED_FILES:
            return f"too many changed files: {len(changed)}>{MAX_CHANGED_FILES}"
        total = sum(n for _, n in changed)
        if total > MAX_TOTAL_CHANGED_LINES:
            return f"too many changed lines: {total}>{MAX_TOTAL_CHANGED_LINES}"
        for path, _ in changed:
            if path_denied(path):
                return f"denied path changed: {path}"
            # Symlink escape: reject changed paths that are symlinks leaving wt.
            full = wt / path
            if full.is_symlink():
                try:
                    full.resolve(strict=True).relative_to(wt.resolve())
                except (OSError, ValueError):
                    return f"symlink escape: {path}"
            if path.startswith("integrations/hermes/") and not path.endswith(
                (".patch", ".py", ".md")
            ):
                return f"unexpected hermes path: {path}"
        return ""

    def _default_validate(self, wt: Path) -> tuple[bool, str]:
        """Full unattended gate. No ``|| true``. FAIL/TIMEOUT/UNKNOWN → False."""
        checks: list[list[str]] = [
            ["git", "-C", str(wt), "diff", "--check"],
        ]
        focused = [
            wt / "tests/reliability/product-reliability-ux.py",
            wt / "tests/reliability/guarded-self-heal.py",
        ]
        for script in focused:
            if script.is_file():
                checks.append(["python3", str(script)])
        audit = wt / "scripts/public-audit.sh"
        if audit.is_file():
            checks.append(["bash", str(audit)])
        smoke = wt / "tests/smoke/run.sh"
        # Bound smoke: only services smoke when present (full smoke is heavy).
        services_smoke = wt / "tests/smoke/services.sh"
        if services_smoke.is_file():
            checks.append(["bash", str(services_smoke)])
        elif smoke.is_file():
            checks.append(["bash", str(smoke)])
        # Materialization if Hermes patches changed.
        changed = [p for p, _ in self._changed_files(wt)]
        hermes_patched = any(
            p.startswith("integrations/hermes/") and p.endswith(".patch")
            for p in changed
        )
        mat = wt / "install/30-brain-materialize.sh"
        if hermes_patched and mat.is_file():
            checks.append(["bash", str(mat), "--stage", str(wt / ".tmp-mat-check")])
        units_changed = any(
            p.startswith("services/systemd/") for p in changed
        )
        if units_changed:
            installer = wt / "install/95-services.sh"
            if installer.is_file():
                checks.append(["bash", str(installer), "--check"])

        details: list[str] = []
        for argv in checks:
            try:
                proc = subprocess.run(
                    argv,
                    capture_output=True,
                    text=True,
                    timeout=VALIDATE_TIMEOUT,
                    shell=False,
                    cwd=str(wt),
                )
            except subprocess.TimeoutExpired:
                return False, f"timeout:{' '.join(argv)}"
            except OSError as exc:
                return False, f"missing_or_exec:{argv[0]}:{exc}"
            if proc.returncode != 0:
                return False, (
                    f"fail:{' '.join(argv)}:{(proc.stderr or proc.stdout)[:400]}"
                )
            details.append(f"{argv[0]}=ok")
        if not details:
            return False, "unknown:no validation checks ran"
        return True, ",".join(details)

    def _multi_model_review(
        self,
        wt: Path,
        incident: dict,
        models: list[str],
        repair_model: str,
    ) -> str:
        review_models = [m for m in models if m and m != repair_model]
        # Need a second distinct model; repair model cannot approve itself.
        if len(models) < 2 or not review_models:
            return "UNCERTAIN_SINGLE_REVIEWER"
        reviewer = review_models[0]
        prompt = (
            "Review this repair diff for safety. Reply with exactly one word: "
            "APPROVE, REJECT, or UNCERTAIN. Task was: "
            + incident["sanitized_task"][:500]
        )
        if self.test_mode or str(self.opencode.opencode_bin).endswith(
            "fake-opencode"
        ) or os.environ.get("OPEN_CLOUD_SELF_HEAL_FAKE_REVIEW"):
            # Still require ≥2 models listed; fake review is explicit test path.
            if len(models) < 2 and not os.environ.get(
                "OPEN_CLOUD_SELF_HEAL_FAKE_REVIEW"
            ):
                return "UNCERTAIN_SINGLE_REVIEWER"
            return os.environ.get("OPEN_CLOUD_SELF_HEAL_FAKE_REVIEW", "APPROVE")
        ok, out = self.opencode.run(
            workdir=wt, prompt=prompt, model=reviewer, agent=None
        )
        if not ok:
            return "UNCERTAIN"
        up = out.upper()
        if "REJECT" in up:
            return "REJECT"
        if "UNCERTAIN" in up:
            return "UNCERTAIN"
        if "APPROVE" in up:
            return "APPROVE"
        return "UNCERTAIN"

    def _save_artifact(
        self, incident_id: str, wt: Path, diff_text: Optional[str] = None
    ) -> str:
        dest = self.artifacts / incident_id
        if dest.exists():
            shutil.rmtree(dest)
        dest.mkdir(parents=True)
        diff = diff_text if diff_text is not None else self._git_diff(wt)
        if scan_diff_for_secrets(diff):
            # Do not persist raw secret-bearing diffs.
            (dest / "repair.diff").write_text(
                "# REDACTED: secret scan failed\n", encoding="utf-8"
            )
        else:
            (dest / "repair.diff").write_text(diff, encoding="utf-8")
        meta = {"incident_id": incident_id, "saved_at": self._now()}
        (dest / "meta.json").write_text(
            json.dumps(meta, indent=2), encoding="utf-8"
        )
        return str(dest)


# Re-export for tests / inbox helpers.
__all__ = [
    "SelfHealController",
    "OpenCodeRunner",
    "Classification",
    "classify_failure",
    "sanitize_for_opencode",
    "incident_signature",
    "path_denied",
    "scan_diff_for_secrets",
    "assert_safe_workdir",
    "discover_free_models",
    "pick_models",
    "git_rev",
    "DEFAULT_STATE_ROOT",
    "write_inbox_event",
    "DENY_PATH_GLOBS",
]
