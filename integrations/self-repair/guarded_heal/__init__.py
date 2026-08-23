"""Guarded autonomous self-healing control plane.

Extends existing P8 ``opencloud_self_repair`` + ``hermes-code-repair`` with a
source-controlled DETECT → CAPTURE → CLASSIFY → RECOVER → VERIFY → PROMOTE →
DEPLOY → CANARY loop for Open Cloud Assistant itself (isolated worktrees,
never live Hermes edits via OpenCode).
"""

from .controller import (
    DEFAULT_STATE_ROOT,
    SelfHealController,
    assert_safe_workdir,
    classify_failure,
    path_denied,
    sanitize_for_opencode,
)
from .detector import GatewayLifecycle, RuntimeDetector, parse_journal_line
from .store import IncidentStore

__all__ = [
    "SelfHealController",
    "IncidentStore",
    "RuntimeDetector",
    "GatewayLifecycle",
    "DEFAULT_STATE_ROOT",
    "classify_failure",
    "sanitize_for_opencode",
    "path_denied",
    "assert_safe_workdir",
    "parse_journal_line",
]
