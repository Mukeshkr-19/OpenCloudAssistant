"""Deterministic model-control + channel-capability fast path.

# HERMES_OPENCLOUD_MODEL_CONTROL_FAST_PATH_V1

Handles high-confidence messaging intents *before* the conversational LLM /
tools run:

  * "what model are you using?" → real runtime routing state
  * "switch to <model> in <provider>" → Fleet-resolved session pin/override
  * "can I use voice chat on iMessage?" → channel capability metadata

Pure helpers stay importable for reliability tests. Gateway owns session
override writes, Fleet pin, and ack delivery.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence, Tuple

# HERMES_OPENCLOUD_MODEL_CONTROL_FAST_PATH_V1

_STATUS_RE = re.compile(
    r"(?i)^\s*(?:(?:hey|hi|hello|hermes)[,!]?\s+)?"
    r"(?:what(?:'s|\s+is)?\s+(?:the\s+)?(?:ai\s+)?model|"
    r"which\s+(?:ai\s+)?model|"
    r"what\s+model|"
    r"current\s+model|"
    r"model\s+status)"
    r"(?:\s+(?:are\s+(?:you|u)|is\s+this))?"
    r"(?:\s+(?:using|on|running))?"
    r"(?:\s+(?:now|currently|right\s+now))?"
    r"[?.!]?\s*$"
)

_SWITCH_RE = re.compile(
    r"(?i)^\s*(?:(?:hey|hi|hello|hermes)[,!]?\s+)?"
    r"(?:please\s+)?"
    r"(?:switch|change|use|set)\s+(?:to\s+|over\s+to\s+)?"
    r"(?P<body>.+?)"
    r"\s*$"
)

_PROVIDER_HINT_RE = re.compile(
    r"(?i)\b(?:in|via|on|with|using)\s+"
    r"(?P<prov>opencode(?:-zen)?|zen|openrouter|nvidia|openai|anthropic|"
    r"gemini|google)\b"
)

_VOICE_CAP_RE = re.compile(
    r"(?i)(?:can\s+i|is\s+it\s+possible\s+to|do\s+you\s+support|are\s+you\s+able\s+to)\s+"
    r"(?:use\s+)?(?:voice\s+chat|voice\s+calls?|live\s+voice|"
    r"talk\s+(?:to\s+you\s+)?(?:by|via|with)\s+voice|"
    r"voice\s+(?:through|over|on|via)\s+(?:imessage|i\s*message|photon|this\s+channel))"
)

_SLASH_RE = re.compile(r"^\s*/")

# Friendly provider aliases → Hermes/Fleet provider ids.
_PROVIDER_ALIASES = {
    "opencode": "opencode-zen",
    "opencode-zen": "opencode-zen",
    "zen": "opencode-zen",
    "openrouter": "openrouter",
    "nvidia": "nvidia",
    "openai": "openai",
    "anthropic": "anthropic",
    "gemini": "gemini",
    "google": "gemini",
}


@dataclass(frozen=True)
class ModelControlIntent:
    kind: str  # status | switch | capability
    model_query: str = ""
    provider_hint: str = ""
    capability: str = ""  # voice_chat


@dataclass(frozen=True)
class ResolvedModel:
    provider: str
    model: str
    group: str = ""
    candidate: Any = None


def detect_model_control_intent(text: Optional[str]) -> Optional[ModelControlIntent]:
    """Return a high-confidence model-control intent, or None to fall through."""
    if not text or not isinstance(text, str):
        return None
    raw = text.strip()
    if not raw or _SLASH_RE.match(raw):
        return None
    if len(raw) > 240:
        return None

    if _VOICE_CAP_RE.search(raw):
        return ModelControlIntent(kind="capability", capability="voice_chat")

    if _STATUS_RE.match(raw):
        return ModelControlIntent(kind="status")

    m = _SWITCH_RE.match(raw)
    if not m:
        return None
    body = (m.group("body") or "").strip().rstrip(".!?")
    if not body:
        return None
    # Reject vague / taskful bodies ("switch to agent mode", "switch topics").
    if re.search(
        r"(?i)\b(topic|subject|mode|gear|tabs?|apps?|windows?|channel)\b",
        body,
    ) and not re.search(r"(?i)\b(model|muse|kimi|glm|gpt|claude|llama|zen|opencode)\b", body):
        return None

    provider_hint = ""
    pm = _PROVIDER_HINT_RE.search(body)
    if pm:
        provider_hint = _PROVIDER_ALIASES.get(
            pm.group("prov").strip().lower(),
            pm.group("prov").strip().lower(),
        )
        body = (_PROVIDER_HINT_RE.sub("", body)).strip(" ,.-")

    body = re.sub(r"(?i)\b(?:the\s+)?model\b", " ", body)
    body = re.sub(r"\s+", " ", body).strip(" ,.-")
    if not body or len(body) < 2:
        return None
    # Must look like a model token, not free prose.
    if len(body.split()) > 8:
        return None
    return ModelControlIntent(
        kind="switch",
        model_query=body,
        provider_hint=provider_hint,
    )


def normalize_model_query(query: str) -> str:
    q = (query or "").strip().lower()
    q = q.replace("_", "-")
    q = re.sub(r"\s+", " ", q)
    return q


def _candidate_fields(candidate: Any) -> Tuple[str, str, str]:
    try:
        from agent.hermes_fleet_bridge import _group, _model, _provider

        return (
            str(_provider(candidate) or "").strip().lower(),
            str(_model(candidate) or "").strip(),
            str(_group(candidate) or "").strip().lower(),
        )
    except Exception:
        if isinstance(candidate, dict):
            return (
                str(candidate.get("provider") or "").strip().lower(),
                str(candidate.get("model") or candidate.get("id") or "").strip(),
                str(candidate.get("providerGroup") or candidate.get("group") or "")
                .strip()
                .lower(),
            )
        return ("", "", "")


def resolve_model_alias(
    query: str,
    *,
    provider_hint: str = "",
    candidates: Optional[Sequence[Any]] = None,
) -> Tuple[Optional[ResolvedModel], List[ResolvedModel]]:
    """Resolve a friendly model name against Fleet candidates.

    Returns ``(exact_or_unique, ambiguities)``. Ambiguous matches leave the
    route unchanged — caller should ask one clarification.
    """
    q = normalize_model_query(query)
    if not q:
        return None, []
    hint = (provider_hint or "").strip().lower()
    if hint in _PROVIDER_ALIASES:
        hint = _PROVIDER_ALIASES[hint]

    pool: List[ResolvedModel] = []
    for cand in candidates or []:
        provider, model, group = _candidate_fields(cand)
        if not model:
            continue
        if hint and provider != hint and group != hint:
            # Allow "opencode" hint to match zen group.
            if not (hint == "opencode-zen" and (provider == "opencode-zen" or group == "zen")):
                continue
        pool.append(
            ResolvedModel(provider=provider, model=model, group=group, candidate=cand)
        )

    if not pool:
        return None, []

    # Exact id / suffix match first.
    exact = [
        r
        for r in pool
        if r.model.lower() == q
        or r.model.lower().endswith("/" + q)
        or r.model.lower().endswith("-" + q)
    ]
    if len(exact) == 1:
        return exact[0], []
    if len(exact) > 1:
        return None, exact

    # Token / substring match (muse 1.2 → muse + 1.2 must all appear).
    tokens = [t for t in re.split(r"[\s/]+", q) if t]
    fuzzy: List[ResolvedModel] = []
    for r in pool:
        mid = r.model.lower()
        if all(tok in mid for tok in tokens):
            fuzzy.append(r)
    if len(fuzzy) == 1:
        return fuzzy[0], []
    if len(fuzzy) > 1:
        return None, fuzzy
    return None, []


def format_model_status(
    *,
    provider: str,
    model: str,
    source: str,
    fleet_pin: bool,
    routing_mode: str = "",
    last_successful: str = "",
) -> str:
    """Human-readable status from runtime state — never LLM self-report."""
    bits = [
        f"Active route: {provider}/{model}" if provider else f"Active model: {model}",
        f"source={source}",
        f"fleet_pin={'yes' if fleet_pin else 'no'}",
    ]
    if routing_mode:
        bits.append(f"routing={routing_mode}")
    if last_successful and last_successful != f"{provider}/{model}":
        bits.append(f"last_successful={last_successful}")
    bits.append("This answer comes from runtime routing state, not model self-report.")
    return "\n".join(bits)


def format_capability_answer(
    *,
    platform: str,
    capability: str,
) -> str:
    """Answer capability questions from channel metadata, not tools."""
    plat = (platform or "").strip().lower()
    if capability == "voice_chat":
        if plat in {"imessage", "photon", "bluebubbles"}:
            return (
                "No — live voice chat is not supported on iMessage/Photon.\n"
                "You can send voice notes (audio messages) as attachments; "
                "Hermes replies with text (and optional TTS audio when configured). "
                "There is no interactive two-way voice call over this channel."
            )
        if plat in {"discord", "telegram"}:
            return (
                "This channel can exchange voice notes / audio messages. "
                "Live interactive voice-call chat depends on platform voice features "
                "and is not the default Hermes messaging path."
            )
        return (
            "Live voice chat is not available on this messaging channel. "
            "Use text (or voice notes where the platform supports audio attachments)."
        )
    return "That capability is not advertised for this channel."


def format_switch_ack(resolved: ResolvedModel, *, previous: str = "") -> str:
    prev = f" (was {previous})" if previous else ""
    return (
        f"✅ Session model set to {resolved.provider}/{resolved.model}{prev}.\n"
        "Override is session-scoped (cron/Fleet automatic routing unchanged for other sessions)."
    )


def format_switch_ambiguous(matches: Sequence[ResolvedModel]) -> str:
    lines = [
        "Several Fleet models match — reply with one exact id (or /model <id> --provider <p>):"
    ]
    for i, m in enumerate(matches[:8], 1):
        lines.append(f"  {i}. {m.provider} / {m.model}")
    return "\n".join(lines)


def format_switch_not_found(query: str, provider_hint: str = "") -> str:
    hint = f" (provider={provider_hint})" if provider_hint else ""
    return (
        f"Could not switch to '{query}'{hint}: no unambiguous verified Fleet route.\n"
        "Route unchanged. Try /model --catalog or /model <id> --provider <provider>."
    )


def list_fleet_main_candidates() -> List[Any]:
    """Best-effort Fleet main candidates (verified + available)."""
    try:
        from agent.hermes_fleet_bridge import _available, _fleet, enabled

        if not enabled():
            return []
        fleet = _fleet()
        try:
            return list(_available(fleet, "main") or [])
        finally:
            fleet.close()
    except Exception:
        return []


def tool_result_is_success(payload: Any) -> bool:
    """Truthfulness helper: nonzero / error / cancel / unknown ≠ success."""
    if payload is None:
        return False
    data = payload
    if isinstance(payload, str):
        text = payload.strip()
        if not text:
            return False
        try:
            import json

            data = json.loads(text)
        except Exception:
            # Non-JSON tool text: only treat explicit success markers as ok.
            low = text.lower()
            if "exit_code" in low and re.search(r"exit_code\"?\s*[:=]\s*[1-9]", text):
                return False
            if re.search(r"\b(error|failed|cancelled|canceled|timeout)\b", low):
                return False
            return "success" in low and "fail" not in low
    if not isinstance(data, dict):
        return False
    if data.get("success") is False or data.get("ok") is False:
        return False
    if data.get("error"):
        return False
    if str(data.get("status") or "").lower() in {
        "failed",
        "error",
        "cancelled",
        "canceled",
        "timeout",
        "unknown",
    }:
        return False
    try:
        code = data.get("exit_code", data.get("returncode"))
        if code is not None and int(code) != 0:
            return False
    except Exception:
        return False
    if data.get("success") is True or data.get("ok") is True:
        return True
    # Unknown without explicit success → not success.
    return False
