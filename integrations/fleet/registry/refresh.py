#!/usr/bin/env python3

from __future__ import annotations

import json
import os
import re
import tempfile
import time
from pathlib import Path

import yaml

from hermes_cli.model_switch import list_provider_models

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fleet_runtime import fleet_root, registry_lock, verification_ttl_ms


HOME = Path.home()

HERMES = HOME / ".hermes"

CONFIG = HERMES / "config.yaml"

ROOT = fleet_root() / "registry"

OUTPUT = ROOT / "models.json"

VERIFICATION_TTL_MS = verification_ttl_ms()


def policy_registry_providers() -> dict:
    """Enabled registry providers derived from validated Fleet policy.

    Reads fleet_root()/fleet.json (the installed hermes-fleet-policy.json).
    Only pools whose type is "registry" are discovery targets; each must
    declare provider, providerGroup, discoveryAliases, and freeOnly. There
    is no enabled-provider/group aggregation table in code.

    Raises SystemExit with a clear reason on malformed policy (fail closed).
    """

    policy_path = fleet_root() / "fleet.json"

    if not policy_path.is_file():
        raise SystemExit(
            f"ERROR: Fleet policy not found at {policy_path}; "
            "cannot derive enabled providers"
        )

    try:
        policy = json.loads(
            policy_path.read_text(
                encoding="utf-8"
            )
        )
    except Exception as exc:
        raise SystemExit(
            f"ERROR: invalid Fleet policy at {policy_path}: {exc}"
        ) from exc

    result = {}

    pools = (
        policy.get("pools")
        or {}
    )

    for pool_name, pool in pools.items():

        if not isinstance(pool, dict):
            continue

        if (
            str(
                pool.get("type")
                or ""
            ).strip().lower()
            != "registry"
        ):
            continue

        provider = str(
            pool.get("provider")
            or ""
        ).strip()

        group = str(
            pool.get("providerGroup")
            or ""
        ).strip()

        if not provider or not group:
            raise SystemExit(
                f"ERROR: registry pool {pool_name!r} must declare "
                "provider and providerGroup in Fleet policy"
            )

        aliases = pool.get("discoveryAliases")

        if not isinstance(aliases, list) or not aliases:
            raise SystemExit(
                f"ERROR: registry pool {pool_name!r} must declare a "
                "non-empty discoveryAliases list in Fleet policy"
            )

        cleaned_aliases = []

        for alias in aliases:
            if not isinstance(alias, str) or not alias.strip():
                raise SystemExit(
                    f"ERROR: registry pool {pool_name!r} has invalid "
                    "discoveryAliases entry in Fleet policy"
                )
            cleaned_aliases.append(alias.strip())

        if "freeOnly" not in pool:
            raise SystemExit(
                f"ERROR: registry pool {pool_name!r} must declare "
                "freeOnly in Fleet policy"
            )

        free_only = pool["freeOnly"]

        if not isinstance(free_only, bool):
            raise SystemExit(
                f"ERROR: registry pool {pool_name!r} freeOnly must be "
                "a boolean in Fleet policy"
            )

        result[provider] = {
            "providerGroup": group,
            "aliases": cleaned_aliases,
            "freeOnly": free_only,
        }

    if not result:
        raise SystemExit(
            "ERROR: Fleet policy declares no enabled registry providers"
        )

    return result


SPECIALIST = re.compile(
    r"""
    embedding|
    embed|
    rerank|
    reward|
    guard|
    safety|
    moderation|
    detector|
    retrieval|
    retriever|
    parser|
    translation|
    translate|
    diffusion|
    image[-_/]?gen|
    vision[-_/]?encoder|
    speech|
    tts|
    audio|
    whisper
    """,
    re.I | re.X,
)


FREE_TOKEN = re.compile(
    r"""
    (^|[-_:/.])
    free
    ($|[-_:/.])
    """,
    re.I | re.X,
)


def atomic_json(
    path: Path,
    data: dict,
) -> None:

    path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

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


def normalize_models(value):

    result = []


    if isinstance(
        value,
        dict,
    ):

        for key, metadata in value.items():

            model_id = str(
                key
            ).strip()

            if not model_id:
                continue

            result.append({
                "id": model_id,
                "metadata":
                    metadata
                    if isinstance(
                        metadata,
                        dict,
                    )
                    else {},
            })


    elif isinstance(
        value,
        (
            list,
            tuple,
            set,
        ),
    ):

        for item in value:

            if isinstance(
                item,
                str,
            ):

                model_id = (
                    item.strip()
                )

                if model_id:

                    result.append({
                        "id":
                            model_id,

                        "metadata":
                            {},
                    })


            elif isinstance(
                item,
                dict,
            ):

                model_id = (
                    item.get("id")
                    or item.get("model")
                    or item.get("name")
                )

                if (
                    isinstance(
                        model_id,
                        str,
                    )
                    and model_id.strip()
                ):

                    result.append({
                        "id":
                            model_id.strip(),

                        "metadata":
                            dict(item),
                    })


    dedup = {}

    for row in result:

        dedup[
            row["id"]
        ] = row

    return list(
        dedup.values()
    )


def discover(
    aliases,
):

    last_error = None


    for identity in aliases:

        try:

            raw = (
                list_provider_models(
                    identity
                )
            )

            rows = (
                normalize_models(
                    raw
                )
            )

            if rows:

                return (
                    identity,
                    rows,
                )

        except Exception as exc:

            last_error = (
                type(exc).__name__
            )


    raise RuntimeError(
        "Hermes native model discovery failed"
        + (
            f" ({last_error})"
            if last_error
            else ""
        )
    )


def numeric_zero(
    value,
):

    if isinstance(
        value,
        (
            int,
            float,
        ),
    ):

        return (
            float(value)
            == 0.0
        )


    if isinstance(
        value,
        str,
    ):

        try:

            return (
                float(
                    value.strip()
                )
                == 0.0
            )

        except Exception:

            return False


    return False


def explicitly_free(
    model_id: str,
    metadata: dict,
) -> bool:

    #
    # Conservative rule:
    #
    # The model must explicitly identify itself as
    # free by ID/name OR expose zero input/output
    # prices.
    #

    name = str(
        metadata.get(
            "name",
            "",
        )
    )


    if FREE_TOKEN.search(
        model_id
    ):

        return True


    if FREE_TOKEN.search(
        name
    ):

        return True


    for key in (
        "free",
        "is_free",
        "isFree",
    ):

        if (
            metadata.get(key)
            is True
        ):

            return True


    pricing = (
        metadata.get(
            "pricing"
        )
    )


    if isinstance(
        pricing,
        dict,
    ):

        values = []

        for key in (
            "prompt",
            "input",
            "completion",
            "output",
        ):

            if key in pricing:

                values.append(
                    pricing[key]
                )


        if (
            values
            and all(
                numeric_zero(x)
                for x in values
            )
        ):

            return True


    return False


def configured_seeds(
    specs: dict,
) -> dict:

    try:

        config = (
            yaml.safe_load(
                CONFIG.read_text()
            )
            or {}
        )

    except Exception:

        config = {}


    provider_to_group = {
        provider: spec["providerGroup"]
        for provider, spec in specs.items()
    }

    result = {
        group: set()
        for group in sorted(
            {
                spec["providerGroup"]
                for spec in specs.values()
            }
        )
    }


    def record(
        provider: str,
        model: str,
    ):

        group = provider_to_group.get(
            str(provider or "").strip()
        )

        if group and str(model or "").strip():
            result[group].add(model.strip())


    model_cfg = (
        config.get("model")
        or {}
    )


    if isinstance(
        model_cfg,
        dict,
    ):

        record(
            model_cfg.get("provider"),
            model_cfg.get("default"),
        )


        for fb in (
            model_cfg.get(
                "fallback_providers",
                []
            )
            or []
        ):

            if not isinstance(
                fb,
                dict,
            ):
                continue

            record(
                fb.get("provider"),
                fb.get("model"),
            )


    delegation = (
        config.get(
            "delegation"
        )
        or {}
    )


    if isinstance(
        delegation,
        dict,
    ):

        record(
            delegation.get("provider"),
            delegation.get("model"),
        )


    return result


def main():

    ROOT.mkdir(
        parents=True,
        exist_ok=True,
    )


    old = {}

    if OUTPUT.exists():

        try:

            old = json.loads(
                OUTPUT.read_text()
            )

        except Exception:

            old = {}


    previous = {
        (
            row.get(
                "provider"
            ),
            row.get(
                "id"
            ),
        ):
        row

        for row in old.get(
            "models",
            []
        )

        if isinstance(
            row,
            dict,
        )
    }


    specs = (
        policy_registry_providers()
    )

    seeds = (
        configured_seeds(
            specs
        )
    )


    now = int(
        time.time()
        * 1000
    )


    all_models = []

    provider_status = {}


    for provider, spec in (
        specs.items()
    ):

        try:

            identity, rows = (
                discover(
                    spec["aliases"]
                )
            )


            provider_status[
                provider
            ] = {
                "ok":
                    True,

                "discoveryIdentity":
                    identity,

                "liveCount":
                    len(rows),
            }


        except Exception as exc:

            provider_status[
                provider
            ] = {
                "ok":
                    False,

                "errorType":
                    type(exc).__name__,
            }

            # Discovery failure is not authoritative removal. Keep the last
            # known rows available, but mark them stale so operators can see
            # that this refresh did not confirm them.
            group = spec["providerGroup"]
            retained = 0
            for old_row in previous.values():
                if old_row.get("provider") != provider:
                    continue
                carried = dict(old_row)
                carried["discoveryStale"] = True
                carried["discoveryErrorType"] = type(exc).__name__
                all_models.append(carried)
                retained += 1
            provider_status[provider]["retainedModelCount"] = retained
            provider_status[provider]["degraded"] = True
            continue


        group = (
            spec[
                "providerGroup"
            ]
        )


        for row in rows:

            model_id = (
                row["id"]
            )

            metadata = (
                row.get(
                    "metadata"
                )
                or {}
            )


            excluded = None


            if SPECIALIST.search(
                model_id
                + " "
                + str(
                    metadata.get(
                        "name",
                        "",
                    )
                )
            ):

                excluded = (
                    "specialist"
                )


            free = None


            if spec[
                "freeOnly"
            ]:

                free = (
                    explicitly_free(
                        model_id,
                        metadata,
                    )
                )


                if not free:

                    excluded = (
                        excluded
                        or
                        "not_explicitly_free"
                    )


            old_row = (
                previous.get(
                    (
                        provider,
                        model_id,
                    ),
                    {},
                )
            )


            verification = (
                old_row.get(
                    "verification"
                )
                or
                "unverified"
            )

            verified_at = old_row.get("verifiedAtMs") or old_row.get("lastProbeMs")
            verification_stale = bool(
                verification == "verified"
                and (not verified_at or now - int(verified_at) >= VERIFICATION_TTL_MS)
            )


            eligible = (
                verification
                == "verified"
                and not verification_stale
                and excluded
                is None
            )


            all_models.append({
                "provider":
                    provider,

                "providerGroup":
                    group,

                "id":
                    model_id,

                "configuredSeed":
                    model_id
                    in seeds.get(
                        group,
                        set(),
                    ),

                "explicitFree":
                    free,

                "excludedReason":
                    excluded,

                "verification":
                    verification,

                "verifiedAtMs":
                    verified_at,

                "verificationStale":
                    verification_stale,

                "discoveryStale":
                    False,

                "productionEligible":
                    eligible,

                "firstSeenMs":
                    old_row.get(
                        "firstSeenMs",
                        now,
                    ),

                "lastSeenMs":
                    now,

                "lastProbeMs":
                    old_row.get(
                        "lastProbeMs"
                    ),

                # FLEET_PROBE_EVIDENCE_V1 — carry measured probe
                # evidence (latency, context length) across refreshes.
                "lastProbeLatencyMs":
                    old_row.get(
                        "lastProbeLatencyMs"
                    ),

                "contextLength":
                    old_row.get(
                        "contextLength"
                    ),

                "probeFailureCount":
                    int(
                        old_row.get(
                            "probeFailureCount",
                            0,
                        )
                        or 0
                    ),

                "lastProbeReason":
                    old_row.get(
                        "lastProbeReason"
                    ),
            })


    all_models.sort(
        key=lambda row: (
            row[
                "providerGroup"
            ],
            row["id"],
        )
    )


    groups = sorted(
        {
            spec["providerGroup"]
            for spec in specs.values()
        }
    )

    production = {
        group: []
        for group in groups
    }


    quarantine = {
        group: []
        for group in groups
    }


    for row in all_models:

        group = (
            row[
                "providerGroup"
            ]
        )


        if row[
            "productionEligible"
        ]:

            production[
                group
            ].append(
                row["id"]
            )


        elif (
            row[
                "excludedReason"
            ]
            is None
        ):

            quarantine[
                group
            ].append(
                row["id"]
            )


    result = {
        "version":
            2,

        "updatedAtMs":
            now,

        "lastVerificationRunMs":
            old.get("lastVerificationRunMs", 0),

        "providerStatus":
            provider_status,

        "productionModels":
            production,

        "quarantineModels":
            quarantine,

        "models":
            all_models,
    }


    atomic_json(
        OUTPUT,
        result,
    )


    print(
        "Hermes-native discovery:"
    )


    for provider, status in (
        provider_status.items()
    ):

        if status.get(
            "ok"
        ):

            print(
                f"  {provider}: "
                f"{status['liveCount']} live "
                f"via {status['discoveryIdentity']}"
            )

        else:

            print(
                f"  {provider}: FAILED "
                f"({status.get('errorType')})"
            )


    for group in groups:

        rows = [
            row
            for row
            in all_models
            if row[
                "providerGroup"
            ]
            == group
        ]


        usable = [
            row
            for row
            in rows
            if row[
                "excludedReason"
            ]
            is None
        ]


        excluded = [
            row
            for row
            in rows
            if row[
                "excludedReason"
            ]
            is not None
        ]


        print()

        print(
            group.upper()
        )

        print(
            "  live:",
            len(rows),
        )

        print(
            "  candidates:",
            len(usable),
        )

        print(
            "  verified:",
            len(
                production[
                    group
                ]
            ),
        )

        print(
            "  quarantine:",
            len(
                quarantine[
                    group
                ]
            ),
        )

        print(
            "  excluded:",
            len(excluded),
        )


if __name__ == "__main__":
    with registry_lock(ROOT):
        main()
