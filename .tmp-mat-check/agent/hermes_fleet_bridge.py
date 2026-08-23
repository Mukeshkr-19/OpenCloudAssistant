"""
Hermes <-> dynamic Fleet integration.

Concrete NVIDIA / Zen model IDs come only from the runtime registry.
OpenRouter/free is the only stable OpenRouter route.

Main gateway session identifiers are HMAC-SHA256'd before entering
Fleet SQLite. Raw messaging identifiers, prompts, credentials and
memory are never stored here.
"""

from __future__ import annotations

import hashlib
import hmac
import importlib.util
import json
import os
from pathlib import Path
from typing import Any, Optional


ROOT = Path(os.environ.get("OPEN_CLOUD_FLEET_HOME", Path.home() / ".local/share/hermes-fleet")).expanduser().resolve()

DISPATCHER = ROOT / "dispatcher.py"
CONFIG = ROOT / "fleet.json"
PIN_KEY = ROOT / "session-pin.key"

_module_cache = None


class FleetBridgeError(RuntimeError):
    pass


# Routing V1 request bounds. Main requests may sample independent capacity,
# but may not loop indefinitely or hammer one provider. Workers retain the
# stricter one-cross-provider-fallback rule in _chain().
MAX_PROVIDER_ATTEMPTS_PER_REQUEST = 6
MAX_ATTEMPTS_PER_PROVIDER = 2
MAX_RATE_LIMIT_RETRIES = 0
MAX_FAILOVER_TIME_SECONDS = 180

def normalize_tool_history(agent, messages):
    """Serialize completed parallel tool batches for limited fallback models."""

    if not getattr(agent, "_hermes_single_tool_call_history", False):
        return messages

    results = {
        str(message.get("tool_call_id") or ""): message
        for message in messages
        if message.get("role") == "tool" and message.get("tool_call_id")
    }
    consumed = set()
    normalized = []
    guidance = (
        "This model supports only one tool call per assistant response. "
        "Issue tool calls sequentially, one at a time."
    )

    for message in messages:
        if message.get("role") == "system" and isinstance(message.get("content"), str):
            normalized.append({**message, "content": message["content"] + "\n\n" + guidance})
            continue

        calls = message.get("tool_calls") if message.get("role") == "assistant" else None
        if not isinstance(calls, list) or len(calls) <= 1:
            if message.get("role") != "tool" or message.get("tool_call_id") not in consumed:
                normalized.append(message)
            continue

        for index, call in enumerate(calls):
            split = {**message, "tool_calls": [call]}
            if index:
                split["content"] = None
                for field in ("reasoning", "reasoning_content", "reasoning_details"):
                    split.pop(field, None)
            normalized.append(split)

            call_id = str(call.get("id") or call.get("call_id") or "")
            if call_id in results:
                normalized.append(results[call_id])
                consumed.add(call_id)

    return normalized


def _module():

    global _module_cache

    if _module_cache is not None:
        return _module_cache

    spec = importlib.util.spec_from_file_location(
        "hermes_dynamic_fleet",
        DISPATCHER,
    )

    if (
        spec is None
        or spec.loader is None
    ):
        raise FleetBridgeError(
            "Fleet dispatcher cannot be loaded"
        )

    module = importlib.util.module_from_spec(
        spec
    )

    spec.loader.exec_module(
        module
    )

    if not hasattr(
        module,
        "HermesFleet",
    ):
        raise FleetBridgeError(
            "HermesFleet missing from dispatcher"
        )

    _module_cache = module

    return module


def _fleet():
    return _module().HermesFleet()


def enabled():

    disabled = str(
        os.environ.get(
            "HERMES_FLEET_DISABLE",
            "",
        )
    ).strip().lower()

    if disabled in {
        "1",
        "true",
        "yes",
        "on",
    }:
        return False

    try:

        cfg = json.loads(
            CONFIG.read_text(
                encoding="utf-8"
            )
        )

    except Exception:
        return False

    return bool(
        cfg.get(
            "enabled"
        )
    )


def _key(candidate):
    return str(
        candidate.get(
            "candidateKey"
        )
        or ""
    )


def _group(candidate):
    return str(
        candidate.get(
            "providerGroup"
        )
        or ""
    ).strip().lower()


def _provider(candidate):
    return str(
        candidate.get(
            "provider"
        )
        or ""
    ).strip().lower()


def _model(candidate):
    return str(
        candidate.get(
            "model"
        )
        or ""
    ).strip()


def _allowed(candidate):

    # HERMES_FLEET_GEMINI_UNVERIFIED_GUARD_V1
    _fleet_provider = getattr(candidate, 'provider', None)
    if _fleet_provider is None and isinstance(candidate, dict):
        _fleet_provider = candidate.get('provider')
    if str(_fleet_provider or '').strip().lower() in {'gemini', 'google-gemini'}:
        return False
    provider = _provider(
        candidate
    )

    group = _group(
        candidate
    )

    model = _model(
        candidate
    )

    if (
        provider == "openrouter"
        or group == "openrouter"
    ):
        return (
            model
            == "openrouter/free"
        )

    return bool(
        provider
        and model
    )


def _all(
    fleet,
    role,
):

    rows = fleet.candidates(
        role
    )

    return [
        dict(row)
        for row in (
            rows
            or []
        )
        if isinstance(
            row,
            dict,
        )
    ]


def _cooling(
    fleet,
    candidate,
):

    return (
        fleet._provider_cooling(
            _group(
                candidate
            )
        )
        or
        fleet._candidate_cooling(
            _key(
                candidate
            )
        )
    )


def _available(
    fleet,
    role,
):

    return [
        candidate
        for candidate in _all(
            fleet,
            role,
        )
        if (
            _allowed(
                candidate
            )
            and not _cooling(
                fleet,
                candidate,
            )
        )
    ]


def _find(
    fleet,
    role,
    *,
    candidate_key=None,
    provider=None,
    model=None,
):

    provider = str(
        provider
        or ""
    ).strip().lower()

    model = str(
        model
        or ""
    ).strip()

    model_matches = []

    for candidate in _all(
        fleet,
        role,
    ):

        if (
            candidate_key
            and _key(
                candidate
            )
            == candidate_key
        ):
            return candidate

        if (
            model
            and _model(
                candidate
            )
            == model
        ):

            model_matches.append(
                candidate
            )

            if (
                not provider
                or _provider(
                    candidate
                )
                == provider
            ):
                return candidate

    if len(
        model_matches
    ) == 1:
        return model_matches[
            0
        ]

    return None


def _session_digest(
    session_key,
):

    key = PIN_KEY.read_bytes()

    if len(key) < 32:
        raise FleetBridgeError(
            "Fleet session HMAC key invalid"
        )

    return hmac.new(
        key,
        str(
            session_key
        ).encode(
            "utf-8",
            errors="surrogatepass",
        ),
        hashlib.sha256,
    ).hexdigest()


def _ensure_pin_table(
    fleet,
):

    fleet.db.execute(
        """
        CREATE TABLE IF NOT EXISTS fleet_session_pins (
            session_digest TEXT NOT NULL,
            role TEXT NOT NULL,
            candidate_key TEXT NOT NULL,
            routing_profile TEXT NOT NULL DEFAULT '',
            updated_at REAL NOT NULL DEFAULT 0,

            PRIMARY KEY (
                session_digest,
                role
            )
        )
        """
    )

    # HERMES_ROUTING_V1_PIN_MIGRATION
    # CREATE TABLE IF NOT EXISTS does not add new columns to an existing
    # installation, so migrate legacy pin databases in place.
    _pin_columns = {
        str(row[1])
        for row in fleet.db.execute(
            "PRAGMA table_info(fleet_session_pins)"
        ).fetchall()
    }

    if "routing_profile" not in _pin_columns:
        fleet.db.execute(
            "ALTER TABLE fleet_session_pins "
            "ADD COLUMN routing_profile TEXT NOT NULL DEFAULT ''"
        )

    fleet.db.commit()


def _get_pin(
    fleet,
    role,
    session_key,
):

    _ensure_pin_table(
        fleet
    )

    row = fleet.db.execute(
        """
        SELECT candidate_key
        FROM fleet_session_pins
        WHERE session_digest = ?
          AND role = ?
        """,
        (
            _session_digest(
                session_key
            ),
            role,
        ),
    ).fetchone()

    if not row:
        return None

    return str(
        row[
            0
        ]
        or ""
    ) or None


def _get_pin_profile(
    fleet,
    role,
    session_key,
):

    _ensure_pin_table(
        fleet
    )

    row = fleet.db.execute(
        """
        SELECT routing_profile
        FROM fleet_session_pins
        WHERE session_digest = ?
          AND role = ?
        """,
        (
            _session_digest(
                session_key
            ),
            role,
        ),
    ).fetchone()

    if not row:
        return None

    return str(
        row[
            0
        ]
        or ""
    ).strip().lower() or None


def _set_pin(
    fleet,
    role,
    session_key,
    candidate,
    profile=None,
):

    import time

    _ensure_pin_table(
        fleet
    )

    fleet.db.execute(
        """
        INSERT INTO fleet_session_pins (
            session_digest,
            role,
            candidate_key,
            routing_profile,
            updated_at
        )
        VALUES (?, ?, ?, ?, ?)

        ON CONFLICT(
            session_digest,
            role
        )
        DO UPDATE SET
            candidate_key = excluded.candidate_key,
            routing_profile = excluded.routing_profile,
            updated_at = excluded.updated_at
        """,
        (
            _session_digest(
                session_key
            ),
            role,
            _key(
                candidate
            ),
            str(
                profile
                or ""
            ).strip().lower(),
            time.time(),
        ),
    )

    fleet.db.commit()


def _clear_pin(
    fleet,
    role,
    session_key,
):

    _ensure_pin_table(
        fleet
    )

    fleet.db.execute(
        """
        DELETE FROM fleet_session_pins
        WHERE session_digest = ?
          AND role = ?
        """,
        (
            _session_digest(
                session_key
            ),
            role,
        ),
    )

    fleet.db.commit()


def session_is_pinned(
    session_key,
    role="main",
):

    if not session_key:
        return False

    fleet = _fleet()

    try:

        return bool(
            _get_pin(
                fleet,
                role,
                session_key,
            )
        )

    finally:
        fleet.close()


def clear_session_pin(
    session_key,
    role="main",
):

    if not session_key:
        return

    fleet = _fleet()

    try:

        _clear_pin(
            fleet,
            role,
            session_key,
        )

    finally:
        fleet.close()


def _configured_default():

    try:

        from hermes_cli.config import (
            load_config_readonly,
        )

        cfg = (
            load_config_readonly()
            or {}
        )

    except Exception:

        return (
            "",
            "",
        )

    raw = cfg.get(
        "model"
    )

    if isinstance(
        raw,
        str,
    ):
        return (
            "",
            raw.strip(),
        )

    if not isinstance(
        raw,
        dict,
    ):
        return (
            "",
            "",
        )

    provider = str(
        raw.get(
            "provider"
        )
        or ""
    ).strip().lower()

    if provider == "auto":
        provider = ""

    model = str(
        raw.get(
            "default"
        )
        or raw.get(
            "model"
        )
        or ""
    ).strip()

    return (
        provider,
        model,
    )


def should_manage_main(
    *,
    model,
    provider,
    gateway_session_key,
):

    if not enabled():
        return False

    if not gateway_session_key:
        return False

    if session_is_pinned(
        gateway_session_key,
        "main",
    ):
        return True

    configured_provider, configured_model = (
        _configured_default()
    )

    requested_provider = str(
        provider
        or ""
    ).strip().lower()

    requested_model = str(
        model
        or ""
    ).strip()

    if (
        not configured_model
        or requested_model
        != configured_model
    ):
        return False

    if (
        configured_provider
        and requested_provider
        and configured_provider
        != requested_provider
    ):
        return False

    return True


def _runtime(
    candidate,
):

    from hermes_cli.runtime_provider import (
        resolve_runtime_provider,
    )

    runtime = resolve_runtime_provider(
        requested=_provider(
            candidate
        ),
        target_model=_model(
            candidate
        ),
    )

    if not isinstance(
        runtime,
        dict,
    ):
        raise FleetBridgeError(
            "runtime resolver returned invalid result"
        )

    # HERMES_FLEET_CONTEXT_ELIGIBILITY_V1
    # Fleet's synthetic tool probe alone is insufficient: Hermes refuses to
    # initialize models whose effective context window is below its runtime
    # floor.  Apply the same metadata resolver before pinning a candidate so a
    # startup-incompatible model is cooled and selection can continue.
    from agent.model_metadata import (
        MINIMUM_CONTEXT_LENGTH,
        get_model_context_length,
    )

    context_length = get_model_context_length(
        _model(candidate),
        base_url=runtime.get("base_url", "") or "",
        api_key=runtime.get("api_key", "") or "",
        provider=runtime.get("provider") or _provider(candidate),
    )

    if context_length and context_length < MINIMUM_CONTEXT_LENGTH:
        raise FleetBridgeError(
            f"model context {context_length} is below Hermes minimum "
            f"{MINIMUM_CONTEXT_LENGTH}"
        )

    return runtime


def _chain(
    fleet,
    role,
    primary,
    *,
    profile=None,
):

    result = []

    seen = {
        _key(
            primary
        )
    }

    available = list(
        _available(
            fleet,
            role,
        )
    )

    # HERMES_ROUTING_V1_FALLBACK_ORDER
    # Use the same FAST/BALANCED/DEEP ranking as primary selection.
    # Unknown/new discovered models remain generic fallbacks and
    # openrouter/free remains the final escape.
    routing_profile = (
        fleet.routing_profile(
            role,
            requested=profile,
        )
    )

    if routing_profile:
        available.sort(
            key=lambda candidate:
                fleet._routing_sort_key(
                    candidate,
                    routing_profile,
                )
        )

    final_escape = None
    provider_attempts = {_group(primary): 1}

    for candidate in available:

        key = _key(
            candidate
        )

        if (
            not key
            or key in seen
        ):
            continue

        entry = {
            "provider":
                _provider(
                    candidate
                ),

            "model":
                _model(
                    candidate
                ),

            "_hermes_fleet_candidate_key":
                key,

            "_hermes_fleet_provider_group":
                _group(
                    candidate
                ),
        }

        if fleet._routing_is_final_escape(candidate):
            final_escape = entry
            seen.add(key)
            continue

        group = _group(candidate)
        if provider_attempts.get(group, 0) >= MAX_ATTEMPTS_PER_PROVIDER:
            continue

        # Reserve one slot for the exact final escape when it is available.
        if len(result) >= MAX_PROVIDER_ATTEMPTS_PER_REQUEST - 2:
            continue

        seen.add(key)
        result.append(entry)
        provider_attempts[group] = provider_attempts.get(group, 0) + 1

    if final_escape is not None and len(result) < MAX_PROVIDER_ATTEMPTS_PER_REQUEST - 1:
        result.append(final_escape)

    if role == "worker":
        cross_provider = [
            candidate
            for candidate in result
            if candidate["_hermes_fleet_provider_group"] != _group(primary)
        ]
        return (cross_provider or result)[:1]

    return result


def resolve_role(
    role,
    *,
    session_key=None,
    profile=None,
):

    if not enabled():
        raise FleetBridgeError(
            "Fleet disabled"
        )

    fleet = _fleet()

    try:

        routing_profile = (
            fleet.routing_profile(
                role,
                requested=profile,
            )
        )

        candidates = _all(
            fleet,
            role,
        )

        if not candidates:
            raise FleetBridgeError(
                f"No candidates for role={role}"
            )

        #
        # Main gateway sessions stay on the same healthy runtime.
        #
        if (
            role == "main"
            and session_key
        ):

            pin = _get_pin(
                fleet,
                role,
                session_key,
            )

            if pin:

                pin_profile = (
                    _get_pin_profile(
                        fleet,
                        role,
                        session_key,
                    )
                )

                if (
                    routing_profile
                    and pin_profile
                    != routing_profile
                ):
                    _clear_pin(
                        fleet,
                        role,
                        session_key,
                    )
                    pin = None

            if pin:

                candidate = _find(
                    fleet,
                    role,
                    candidate_key=pin,
                )

                if (
                    candidate
                    and _allowed(
                        candidate
                    )
                    and not _cooling(
                        fleet,
                        candidate,
                    )
                ):

                    try:

                        runtime = _runtime(
                            candidate
                        )

                        return {
                            "candidate":
                                candidate,

                            "runtime":
                                runtime,

                            "fallback_chain":
                                _chain(
                                    fleet,
                                    role,
                                    candidate,
                                    profile=profile,
                                ),

                            "pinned":
                                True,
                        }

                    except Exception:

                        fleet.failure(
                            candidate,
                            "model_unavailable",
                        )

                _clear_pin(
                    fleet,
                    role,
                    session_key,
                )

        tried = set()

        for _ in range(
            len(
                candidates
            )
            + 2
        ):

            candidate = fleet.select(
                role,
                touch=True,
                profile=profile,
            )

            if not candidate:
                break

            candidate = dict(
                candidate
            )

            key = _key(
                candidate
            )

            if (
                not key
                or key in tried
            ):
                break

            tried.add(
                key
            )

            if not _allowed(
                candidate
            ):
                fleet.failure(
                    candidate,
                    "model_unavailable",
                )
                continue

            try:

                runtime = _runtime(
                    candidate
                )

            except Exception:

                fleet.failure(
                    candidate,
                    "model_unavailable",
                )

                continue

            if (
                role == "main"
                and session_key
            ):

                _set_pin(
                    fleet,
                    role,
                    session_key,
                    candidate,
                    profile=routing_profile,
                )

            return {
                "candidate":
                    candidate,

                "runtime":
                    runtime,

                "fallback_chain":
                    _chain(
                        fleet,
                        role,
                        candidate,
                        profile=profile,
                    ),

                "pinned":
                    False,
            }

        raise FleetBridgeError(
            f"No healthy candidate for role={role}"
        )

    finally:
        fleet.close()


def _failure_kind(
    reason,
):

    if reason is None:
        return None

    text = " ".join([
        str(
            reason
        ),
        str(
            getattr(
                reason,
                "name",
                "",
            )
            or ""
        ),
        str(
            getattr(
                reason,
                "value",
                "",
            )
            or ""
        ),
    ]).lower()

    # Explicit account-wide quota evidence must cool the provider/account,
    # not merely this candidate. OpenRouter supplies both a stable limit_source
    # and a human-readable daily-free-tier error for this condition.
    if (
        "openrouter_free_tier_daily" in text
        or "free-models-per-day" in text
        or "free models per day" in text
        or "daily free model" in text
        or "account quota" in text
    ):
        return "account_quota"

    # HERMES_FLEET_MODEL_QUOTA_CLASSIFICATION_V1
    # Provider gateways can return HTTP 429 for model-specific free-tier
    # exhaustion. Detect quota/credits first so one exhausted model does not
    # masquerade as a generic provider-wide rate limit.
    if (
        "freeusagelimit" in text
        or "free usage limit" in text
        or "billing" in text
        or "quota" in text
        or "credit" in text
        or "payment" in text
    ):
        return "quota"

    if (
        "rate_limit" in text
        or "ratelimit" in text
        or "rate limit" in text
        or "429" in text
    ):
        return "rate_limit"

    if "only supports single tool-call" in text or "only supports single tool call" in text:
        return "model_unavailable"

    if (
        "auth" in text
        or "401" in text
        or "403" in text
        or "unauthorized" in text
        or "forbidden" in text
    ):
        return "auth"

    # HERMES_FLEET_TIMEOUT_CLASSIFICATION_V1
    # A slow/upstream-stalled model must not cool every sibling model on the
    # provider. Keep request timeouts candidate-scoped.
    if (
        "timeout" in text
        or "timed out" in text
    ):
        return "timeout"

    # Genuine connectivity failures indicate provider/runtime reachability and
    # remain provider-scoped.
    if (
        "connection" in text
        or "network" in text
        or "dns" in text
    ):
        return "network"

    if (
        "server" in text
        or "overload" in text
        or "500" in text
        or "502" in text
        or "503" in text
        or "504" in text
    ):
        return "server"

    if (
        "model" in text
        and (
            "unavailable" in text
            or "not found" in text
            or "not_found" in text
            or "unsupported" in text
        )
    ):
        return "model_unavailable"

    return None


def note_agent_failure(
    agent,
    reason,
):

    role = str(
        getattr(
            agent,
            "_hermes_fleet_role",
            "",
        )
        or ""
    )

    if role not in {
        "main",
        "worker",
        "reviewer",
    }:
        return

    kind = _failure_kind(
        reason
    )

    if not kind:
        return

    provider = str(
        getattr(
            agent,
            "provider",
            "",
        )
        or ""
    ).strip().lower()

    model = str(
        getattr(
            agent,
            "model",
            "",
        )
        or ""
    ).strip()

    route = (
        provider,
        model,
    )

    if (
        getattr(
            agent,
            "_hermes_fleet_last_failure_route",
            None,
        )
        == route
    ):
        return

    fleet = _fleet()

    try:

        candidate = _find(
            fleet,
            role,
            provider=provider,
            model=model,
        )

        if not candidate:
            return

        fleet.failure(
            candidate,
            kind,
        )

        agent._hermes_fleet_last_failure_route = route
        agent._hermes_fleet_last_failure = (provider, model, kind)

        if role == "main":

            session_key = getattr(
                agent,
                "_hermes_fleet_session_key",
                None,
            )

            if session_key:

                _clear_pin(
                    fleet,
                    role,
                    session_key,
                )

    finally:
        fleet.close()


def note_api_failure(agent, api_error, classified_reason=None) -> bool:
    """Record the real API failure and say whether this candidate may retry."""

    if getattr(agent, "_hermes_fleet_role", None) not in {"main", "worker", "reviewer"}:
        return True

    error_kind = _failure_kind(api_error)
    kind = error_kind or _failure_kind(classified_reason)
    note_agent_failure(agent, api_error if error_kind else classified_reason)
    return kind not in {"rate_limit", "quota", "account_quota"}


def note_agent_success(agent) -> None:
    """Record recovered capacity and reset failure-episode state."""

    role = str(getattr(agent, "_hermes_fleet_role", "") or "")
    if role not in {"main", "worker", "reviewer"}:
        return

    provider = str(getattr(agent, "provider", "") or "").strip().lower()
    model = str(getattr(agent, "model", "") or "").strip()
    route = (provider, model)
    recovered = getattr(agent, "_hermes_fleet_last_failure_route", None) == route

    if recovered or getattr(agent, "_hermes_fleet_last_success_route", None) != route:
        fleet = _fleet()
        try:
            candidate = _find(fleet, role, provider=provider, model=model)
            if candidate:
                fleet.success(candidate)
                agent._hermes_fleet_last_success_route = route
        finally:
            fleet.close()

    agent._hermes_fleet_last_failure_route = None
    agent._hermes_fleet_failover_started_at = None


def should_skip_fallback(
    agent,
    provider,
    model,
):

    role = str(
        getattr(
            agent,
            "_hermes_fleet_role",
            "",
        )
        or ""
    )

    if role not in {
        "main",
        "worker",
        "reviewer",
    }:
        return False

    fleet = _fleet()

    try:

        candidate = _find(
            fleet,
            role,
            provider=provider,
            model=model,
        )

        if not candidate:
            return False

        if not _allowed(
            candidate
        ):
            return True

        return _cooling(
            fleet,
            candidate,
        )

    finally:
        fleet.close()


def self_check():

    fleet = _fleet()

    try:

        roles = {}

        for role in (
            "main",
            "worker",
            "reviewer",
        ):

            rows = _all(
                fleet,
                role,
            )

            roles[
                role
            ] = len(
                rows
            )

            if rows:
                _cooling(
                    fleet,
                    rows[
                        0
                    ],
                )

        return {
            "enabled":
                enabled(),

            "roles":
                roles,
        }

    finally:
        fleet.close()
