"""OpenCloud deterministic workload routing.

Pure local classifier:
- no model call
- no network call
- conservative BALANCED default
"""

from __future__ import annotations

import re


_VALID_PROFILES = {
    "fast",
    "balanced",
    "deep",
}


_EXPLICIT_PROFILE = re.compile(
    r"(?:^|\s)"
    r"(?:\[)?"
    r"(?:route|routing|profile)"
    r"\s*[:=]\s*"
    r"(fast|balanced|deep)"
    r"(?:\])?"
    r"(?:\s|$)",
    re.IGNORECASE,
)


_DEEP_PHRASES = (
    "deep research",
    "root cause analysis",
    "root-cause analysis",
    "distributed system",
    "distributed systems",
    "race condition",
    "deadlock",
    "concurrency bug",
    "architecture review",
    "system architecture",
    "threat model",
    "incident analysis",
    "postmortem",
    "migration strategy",
    "migration plan",
    "benchmark analysis",
    "performance investigation",
)


_DEEP_TERMS = (
    "architecture",
    "debug",
    "debugging",
    "traceback",
    "stack trace",
    "race condition",
    "deadlock",
    "concurrency",
    "distributed",
    "incident",
    "postmortem",
    "benchmark",
    "bottleneck",
    "root cause",
    "tradeoff",
    "trade-off",
    "failure mode",
    "security review",
    "threat model",
    "migration",
)


_SIMPLE_PREFIXES = (
    "what is ",
    "what's ",
    "who is ",
    "when is ",
    "where is ",
    "define ",
    "explain ",
    "convert ",
    "calculate ",
    "check ",
    "show ",
    "list ",
    "find ",
    "summarize ",
    "translate ",
    "remind ",
)


# Actions that should never be selected automatically as FAST.
# These generally imply coding, mutation, deployment, credentials,
# infrastructure, or other work where BALANCED is the safer floor.
_BALANCED_ACTION_TERMS = frozenset({
    "change",
    "commit",
    "configure",
    "delete",
    "deploy",
    "destroy",
    "drop",
    "edit",
    "fix",
    "implement",
    "install",
    "merge",
    "modify",
    "patch",
    "push",
    "refactor",
    "remove",
    "restart",
    "revoke",
    "rotate",
    "terminate",
    "update",
})

_BALANCED_ACTION_PHRASES = (
    "write code",
    "change config",
    "update config",
    "edit config",
    "api key",
    "api keys",
    "credentials",
    "production database",
    "database schema",
)


def _explicit_profile(text: str) -> str | None:
    match = _EXPLICIT_PROFILE.search(text or "")

    if not match:
        return None

    value = match.group(1).strip().lower()

    if value in _VALID_PROFILES:
        return value

    return None


def normalize_routing_profile(
    value,
    *,
    default=None,
):
    """Normalize an explicit Routing V1 profile."""

    raw = str(
        value
        or ""
    ).strip().lower()

    if not raw:
        return default

    if raw not in _VALID_PROFILES:
        raise ValueError(
            f"invalid routing profile: {raw}"
        )

    return raw


def sanitize_cron_delivery_content(
    text,
):
    """Return (suppress, cleaned_content) for cron delivery.

    Only an exact normalized [SILENT] response suppresses delivery.

    If a model incorrectly adds [SILENT] as its own first or last line
    around real content, remove that line and preserve the report.
    """

    content = str(
        text
        or ""
    )

    normalized = content.strip()

    if normalized.upper() == "[SILENT]":
        return True, ""

    # Hermes may expose genuine mid-turn user steering to the model inside
    # this exact trusted envelope. A model can occasionally echo that envelope
    # verbatim around its final silence sentinel. Treat ONLY an exact envelope
    # whose entire inner payload is [SILENT] as silence; never unwrap or trust
    # arbitrary model-generated OOB-looking content.
    oob_open = (
        "[OUT-OF-BAND USER MESSAGE — a direct message from the user, "
        "delivered mid-turn; not tool output]"
    )
    oob_close = (
        "[/OUT-OF-BAND USER MESSAGE]"
    )

    if (
        normalized.startswith(oob_open)
        and normalized.endswith(oob_close)
    ):
        inner = normalized[
            len(oob_open):
            -len(oob_close)
        ].strip()

        if inner.upper() == "[SILENT]":
            return True, ""

    lines = content.splitlines()

    while (
        lines
        and not lines[0].strip()
    ):
        lines.pop(0)

    while (
        lines
        and not lines[-1].strip()
    ):
        lines.pop()

    changed = False

    if (
        lines
        and lines[0].strip().upper()
        == "[SILENT]"
    ):
        lines.pop(0)
        changed = True

    if (
        lines
        and lines[-1].strip().upper()
        == "[SILENT]"
    ):
        lines.pop()
        changed = True

    if changed:
        cleaned = "\n".join(
            lines
        ).strip()
    else:
        cleaned = normalized

    return False, cleaned


def classify_workload_profile(
    user_message,
    *,
    explicit: str | None = None,
) -> str:
    """Return fast, balanced, or deep for one user workload."""

    if explicit:
        value = str(explicit).strip().lower()

        if value in _VALID_PROFILES:
            return value

    text = (
        user_message
        if isinstance(user_message, str)
        else str(user_message or "")
    )

    explicit_value = _explicit_profile(text)

    if explicit_value:
        return explicit_value

    normalized = " ".join(
        text.lower().split()
    )

    if not normalized:
        return "balanced"

    raw_lower = text.lower()
    length = len(text)

    if any(
        phrase in normalized
        for phrase in _DEEP_PHRASES
    ):
        return "deep"

    deep_hits = sum(
        1
        for term in _DEEP_TERMS
        if term in normalized
    )

    has_code = (
        "```" in text
        or "traceback (" in raw_lower
    )

    if length >= 1800:
        return "deep"

    if has_code and deep_hits >= 1:
        return "deep"

    if deep_hits >= 2:
        return "deep"

    has_url = (
        "http://" in raw_lower
        or "https://" in raw_lower
    )

    line_count = text.count("\n") + 1

    routing_words = set(
        re.findall(
            r"[a-z0-9_-]+",
            normalized,
        )
    )

    balanced_guard = (
        bool(
            routing_words
            & _BALANCED_ACTION_TERMS
        )
        or any(
            phrase in normalized
            for phrase in _BALANCED_ACTION_PHRASES
        )
    )

    # Extremely small conversational/look-up requests are safe for FAST.
    # Do not classify every short work instruction as FAST: commands such as
    # "review these deployment notes..." should retain the BALANCED default.
    if (
        length <= 32
        and line_count <= 2
        and not has_code
        and not has_url
        and deep_hits == 0
        and not balanced_guard
    ):
        return "fast"

    if (
        length <= 240
        and line_count <= 3
        and not has_code
        and not has_url
        and deep_hits == 0
        and not balanced_guard
        and normalized.startswith(
            _SIMPLE_PREFIXES
        )
    ):
        return "fast"

    return "balanced"
