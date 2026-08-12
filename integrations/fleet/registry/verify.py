#!/usr/bin/env python3

from __future__ import annotations

import inspect
import json
import os
import re
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

from agent.credential_pool import load_pool
from hermes_cli.runtime_provider import resolve_runtime_provider

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fleet_runtime import fleet_root, registry_lock, verification_ttl_ms


HOME = Path.home()

REGISTRY = fleet_root() / "registry" / "models.json"


TARGET = {
    "zen": 3,
    "nvidia": 2,
}


MAX_ATTEMPTS = {
    "zen": 10,
    "nvidia": 8,
}

VERIFICATION_TTL_MS = verification_ttl_ms()


def verification_is_fresh(row, now_ms):
    if row.get("verification") != "verified":
        return False
    verified_at = row.get("verifiedAtMs") or row.get("lastProbeMs")
    return bool(verified_at and now_ms - int(verified_at) < VERIFICATION_TTL_MS)


###############################################################################
# MODEL CLASSIFICATION
###############################################################################

SPECIALIST = re.compile(
    r"""
    (^|[/_.:-])
    (
        bge
        | e5
        | embed
        | embedding
        | rerank
        | retriev
        | reward
        | guard
        | safety
        | moderation
        | detector
        | flux
        | diffusion
        | clip
        | siglip
        | vision[-_]?encoder
        | image[-_]?gen
        | stable[-_]?diffusion
        | whisper
        | speech
        | audio
        | tts
        | asr
        | ocr
        | segmentation
    )
    ([/_.:-]|$)
    """,
    re.I | re.X,
)


AGENT_FAMILY = re.compile(
    r"""
    deepseek
    | glm
    | nemotron
    | kimi
    | minimax
    | qwen
    | mimo
    | llama
    | gemma
    | mistral
    | instruct
    | chat
    | coder
    | code
    """,
    re.I | re.X,
)


###############################################################################
# ATOMIC JSON
###############################################################################

def atomic_json(
    path: Path,
    data: dict,
):

    fd, tmp = tempfile.mkstemp(
        prefix=path.name + ".",
        suffix=".tmp",
        dir=str(path.parent),
    )

    try:

        with os.fdopen(
            fd,
            "w",
            encoding="utf-8",
        ) as f:

            json.dump(
                data,
                f,
                indent=2,
                sort_keys=True,
            )

            f.write("\n")

            f.flush()
            os.fsync(
                f.fileno()
            )

        os.replace(
            tmp,
            path,
        )

    finally:

        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass


###############################################################################
# GENERIC VALUE SEARCH
###############################################################################

def walk_find(
    value,
    names,
):

    if isinstance(
        value,
        dict,
    ):

        for name in names:

            raw = value.get(
                name
            )

            if (
                isinstance(raw, str)
                and raw.strip()
            ):

                return raw.strip()


        for child in value.values():

            found = walk_find(
                child,
                names,
            )

            if found:
                return found


    elif isinstance(
        value,
        (
            list,
            tuple,
        ),
    ):

        for child in value:

            found = walk_find(
                child,
                names,
            )

            if found:
                return found


    else:

        for name in names:

            try:

                raw = getattr(
                    value,
                    name,
                )

            except Exception:

                continue


            if (
                isinstance(raw, str)
                and raw.strip()
            ):

                return raw.strip()


    return None


###############################################################################
# HERMES-NATIVE AUTH
###############################################################################

def runtime_for(
    provider,
    model,
):

    return resolve_runtime_provider(
        requested=provider,
        target_model=model,
    )


def native_auth(
    provider,
    model,
):

    runtime = runtime_for(
        provider,
        model,
    )


    key = walk_find(
        runtime,
        (
            "api_key",
            "apikey",
            "key",
            "token",
            "access_token",
            "runtime_api_key",
        ),
    )


    base = walk_find(
        runtime,
        (
            "base_url",
            "runtime_base_url",
            "inference_base_url",
        ),
    )


    #
    # Hermes-native credential pool fallback.
    #

    if not key or not base:

        pool = load_pool(
            provider
        )

        entry = None

        selector = getattr(
            pool,
            "select",
            None,
        )

        if callable(selector):

            try:
                entry = selector()

            except Exception:
                entry = None


        if entry is not None:

            key = (
                key
                or walk_find(
                    entry,
                    (
                        "runtime_api_key",
                        "api_key",
                        "token",
                    ),
                )
            )

            base = (
                base
                or walk_find(
                    entry,
                    (
                        "runtime_base_url",
                        "base_url",
                        "inference_base_url",
                    ),
                )
            )


    if not key:

        raise RuntimeError(
            "native credential unavailable"
        )


    if not base:

        raise RuntimeError(
            "native base URL unavailable"
        )


    return (
        str(base).rstrip("/"),
        key,
        runtime,
    )


###############################################################################
# ENDPOINT NORMALIZATION
###############################################################################

def chat_endpoint(
    provider,
    base,
):

    value = str(
        base or ""
    ).strip().rstrip("/")


    if not value:

        raise RuntimeError(
            "empty provider base URL"
        )


    if value.endswith(
        "/chat/completions"
    ):

        return value


    #
    # OpenCode Zen historically appears in
    # configuration/resolver paths as either:
    #
    #   .../zen
    #   .../zen/v1
    #
    # Its OpenAI-compatible chat endpoint is
    # always under /zen/v1/chat/completions.
    #

    if provider == "opencode-zen":

        if value.endswith(
            "/zen"
        ):

            value += "/v1"


        elif (
            "/zen/" in value
            and not value.endswith(
                "/v1"
            )
        ):

            value = value.rstrip("/")


    #
    # NVIDIA normally resolves to:
    #
    # https://integrate.api.nvidia.com/v1
    #

    return (
        value
        + "/chat/completions"
    )


###############################################################################
# ERROR CLASSIFICATION
###############################################################################

def classify(
    status,
    body,
):

    text = str(
        body or ""
    ).lower()


    if status == 401:

        return (
            "provider_auth",
            True,
        )


    if status == 429:

        return (
            "provider_rate_limit",
            True,
        )


    if status in (
        500,
        502,
        503,
        504,
    ):

        return (
            "provider_server",
            True,
        )


    if status in (
        403,
        404,
    ):

        return (
            f"model_or_route_unavailable_http_{status}",
            False,
        )


    if status == 400:

        return (
            "request_incompatible_http_400",
            False,
        )


    if (
        "timeout" in text
        or "timed out" in text
    ):

        return (
            "network_timeout",
            True,
        )


    return (
        "probe_failed",
        False,
    )


###############################################################################
# SYNTHETIC TOOL PROBE
###############################################################################

def probe(
    provider,
    model,
):

    try:

        base, key, runtime = native_auth(
            provider,
            model,
        )

        endpoint = chat_endpoint(
            provider,
            base,
        )

    except Exception:

        return (
            False,
            "native_runtime_resolution_failed",
            True,
            None,
        )


    payload = {
        "model":
            model,

        "messages": [
            {
                "role":
                    "user",

                "content":
                    (
                        "Synthetic compatibility test. "
                        "Call hermes_fleet_probe with "
                        '{"ok":true}. '
                        "Do not provide a normal answer."
                    ),
            }
        ],

        "tools": [
            {
                "type":
                    "function",

                "function": {
                    "name":
                        "hermes_fleet_probe",

                    "description":
                        "Synthetic Hermes Fleet compatibility check",

                    "parameters": {
                        "type":
                            "object",

                        "properties": {
                            "ok": {
                                "type":
                                    "boolean"
                            }
                        },

                        "required": [
                            "ok"
                        ],
                    },
                },
            }
        ],

        "tool_choice":
            "auto",

        "temperature":
            0,

        "max_tokens":
            96,
    }


    request = urllib.request.Request(
        endpoint,

        data=json.dumps(
            payload
        ).encode(
            "utf-8"
        ),

        headers={
            "Authorization":
                f"Bearer {key}",

            "Content-Type":
                "application/json",

            "Accept":
                "application/json",
        },

        method="POST",
    )


    try:

        with urllib.request.urlopen(
            request,
            timeout=45,
        ) as response:

            raw = response.read()


    except urllib.error.HTTPError as exc:

        try:

            body = exc.read().decode(
                "utf-8",
                errors="replace",
            )

        except Exception:

            body = ""


        reason, stop = classify(
            exc.code,
            body,
        )


        return (
            False,
            reason,
            stop,
            endpoint,
        )


    except Exception as exc:

        reason, stop = classify(
            None,
            str(exc),
        )


        return (
            False,
            reason,
            stop,
            endpoint,
        )


    try:

        data = json.loads(
            raw
        )

    except Exception:

        return (
            False,
            "invalid_json",
            False,
            endpoint,
        )


    for choice in (
        data.get("choices")
        or []
    ):

        if not isinstance(
            choice,
            dict,
        ):
            continue


        message = (
            choice.get(
                "message"
            )
            or {}
        )


        for call in (
            message.get(
                "tool_calls"
            )
            or []
        ):

            if not isinstance(
                call,
                dict,
            ):
                continue


            function = (
                call.get(
                    "function"
                )
                or {}
            )


            if (
                function.get("name")
                ==
                "hermes_fleet_probe"
            ):

                return (
                    True,
                    "verified",
                    False,
                    endpoint,
                )


        legacy = (
            message.get(
                "function_call"
            )
            or {}
        )


        if (
            isinstance(
                legacy,
                dict,
            )
            and
            legacy.get("name")
            ==
            "hermes_fleet_probe"
        ):

            return (
                True,
                "verified",
                False,
                endpoint,
            )


    return (
        False,
        "no_tool_call",
        False,
        endpoint,
    )


###############################################################################
# CANDIDATE RANKING
###############################################################################

def candidate_score(
    row,
):

    model = str(
        row.get("id")
        or ""
    )


    score = 0


    if row.get(
        "configuredSeed"
    ):

        score += 1000

    if row.get("verification") == "verified":
        score += 500


    if AGENT_FAMILY.search(
        model
    ):

        score += 100


    #
    # Slight preference for explicitly free
    # Zen IDs carrying the free marker.
    #

    if (
        row.get(
            "providerGroup"
        )
        == "zen"
        and "free"
        in model.lower()
    ):

        score += 50


    score -= int(
        row.get(
            "probeFailureCount",
            0,
        )
        or 0
    ) * 5


    return score


def likely_general(
    row,
):

    model = str(
        row.get(
            "id",
            ""
        )
    )


    if SPECIALIST.search(
        model
    ):

        return False


    return True


###############################################################################
# MAIN
###############################################################################

def main():

    data = json.loads(
        REGISTRY.read_text()
    )


    now_ms = int(time.time() * 1000)

    for group in (
        "zen",
        "nvidia",
    ):

        print()
        print(
            "========================================"
        )
        print(
            group.upper()
        )


        verified = [
            row
            for row
            in data.get(
                "models",
                []
            )
            if (
                row.get(
                    "providerGroup"
                )
                == group
                and row.get(
                    "verification"
                )
                == "verified"
                and verification_is_fresh(row, now_ms)
                and not row.get(
                    "excludedReason"
                )
            )
        ]


        print(
            "Verified before:",
            len(verified),
        )


        candidates = [
            row
            for row
            in data.get(
                "models",
                []
            )
            if (
                row.get(
                    "providerGroup"
                )
                == group
                and not row.get(
                    "excludedReason"
                )
                and not verification_is_fresh(row, now_ms)
                and likely_general(
                    row
                )
            )
        ]


        candidates.sort(
            key=lambda row: (
                -candidate_score(
                    row
                ),
                row.get(
                    "id",
                    "",
                ),
            )
        )


        print(
            "General-purpose candidates:",
            len(candidates),
        )


        attempts = 0


        for row in candidates:

            if (
                len(verified)
                >=
                TARGET[group]
                and row.get("verification") != "verified"
            ):

                break


            if (
                attempts
                >=
                MAX_ATTEMPTS[
                    group
                ]
            ):

                break


            attempts += 1


            model = row[
                "id"
            ]


            print()
            print(
                f"PROBE {attempts}:",
                model,
            )


            ok, reason, stop, endpoint = probe(
                row["provider"],
                model,
            )


            #
            # Endpoint is safe to display.
            # Credential is never printed.
            #

            if attempts == 1:

                print(
                    "  endpoint:",
                    endpoint,
                )


            row[
                "lastProbeMs"
            ] = int(
                time.time()
                * 1000
            )


            if ok:

                row[
                    "verification"
                ] = "verified"

                row["verifiedAtMs"] = row["lastProbeMs"]
                row["verificationStale"] = False

                row[
                    "probeFailureCount"
                ] = 0

                row[
                    "lastProbeReason"
                ] = None

                row[
                    "productionEligible"
                ] = True


                verified.append(
                    row
                )


                print(
                    "  PASS: synthetic tool call"
                )


            else:

                # A stale capability result has now been re-probed and failed;
                # it must not retain the old verified state.
                row["verification"] = "unverified"
                row["verificationStale"] = False

                row[
                    "probeFailureCount"
                ] = (
                    int(
                        row.get(
                            "probeFailureCount",
                            0,
                        )
                        or 0
                    )
                    + 1
                )

                row[
                    "lastProbeReason"
                ] = reason

                row[
                    "productionEligible"
                ] = False


                print(
                    "  FAIL:",
                    reason,
                )


                if reason in (
                    "no_tool_call",
                    "model_or_route_unavailable_http_403",
                    "model_or_route_unavailable_http_404",
                ):

                    row[
                        "verification"
                    ] = "incompatible"


                if stop:

                    print(
                        "  Provider-wide stop condition."
                    )

                    print(
                        "  Sibling models will NOT be hammered."
                    )

                    break


        print()
        print(
            "Attempts:",
            attempts,
        )

        print(
            "Verified after:",
            len(verified),
        )


    production = {
        "zen": [],
        "nvidia": [],
    }


    quarantine = {
        "zen": [],
        "nvidia": [],
    }


    for row in data.get(
        "models",
        []
    ):

        group = row.get(
            "providerGroup"
        )


        if group not in production:
            continue


        eligible = (
            row.get(
                "verification"
            )
            == "verified"
            and not row.get(
                "excludedReason"
            )
            and likely_general(
                row
            )
        )


        row[
            "productionEligible"
        ] = eligible


        if eligible:

            production[
                group
            ].append(
                row["id"]
            )


        elif (
            not row.get(
                "excludedReason"
            )
            and likely_general(
                row
            )
        ):

            quarantine[
                group
            ].append(
                row["id"]
            )


    data[
        "productionModels"
    ] = production

    data[
        "quarantineModels"
    ] = quarantine

    data[
        "lastVerificationRunMs"
    ] = int(
        time.time()
        * 1000
    )


    atomic_json(
        REGISTRY,
        data,
    )


    print()
    print(
        "========================================"
    )
    print(
        "FINAL VERIFIED CAPACITY"
    )

    print(
        "Zen free:",
        len(
            production[
                "zen"
            ]
        ),
    )

    print(
        "NVIDIA:",
        len(
            production[
                "nvidia"
            ]
        ),
    )

    print(
        "========================================"
    )


if __name__ == "__main__":
    with registry_lock(REGISTRY.parent):
        main()
