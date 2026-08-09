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


HOME = Path.home()

HERMES = HOME / ".hermes"

CONFIG = HERMES / "config.yaml"

ROOT = (
    HOME
    / ".local"
    / "share"
    / "hermes-fleet"
    / "registry"
)

OUTPUT = ROOT / "models.json"


PROVIDERS = {
    "opencode-zen": {
        "providerGroup": "zen",
        "aliases": [
            "opencode-zen",
            "opencode",
        ],
        "freeOnly": True,
    },

    "nvidia": {
        "providerGroup": "nvidia",
        "aliases": [
            "nvidia",
        ],
        "freeOnly": False,
    },
}


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


def configured_seeds():

    try:

        config = (
            yaml.safe_load(
                CONFIG.read_text()
            )
            or {}
        )

    except Exception:

        config = {}


    result = {
        "zen": set(),
        "nvidia": set(),
    }


    model_cfg = (
        config.get("model")
        or {}
    )


    if isinstance(
        model_cfg,
        dict,
    ):

        provider = str(
            model_cfg.get(
                "provider",
                "",
            )
        ).strip()

        model = str(
            model_cfg.get(
                "default",
                "",
            )
        ).strip()


        if (
            provider
            == "nvidia"
            and model
        ):

            result[
                "nvidia"
            ].add(model)


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

            provider = str(
                fb.get(
                    "provider",
                    "",
                )
            ).strip()

            model = str(
                fb.get(
                    "model",
                    "",
                )
            ).strip()


            if (
                provider
                == "nvidia"
                and model
            ):

                result[
                    "nvidia"
                ].add(model)


            if (
                provider
                == "opencode-zen"
                and model
            ):

                result[
                    "zen"
                ].add(model)


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

        provider = str(
            delegation.get(
                "provider",
                "",
            )
        ).strip()

        model = str(
            delegation.get(
                "model",
                "",
            )
        ).strip()


        if (
            provider
            == "opencode-zen"
            and model
        ):

            result[
                "zen"
            ].add(model)


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


    seeds = (
        configured_seeds()
    )


    now = int(
        time.time()
        * 1000
    )


    all_models = []

    provider_status = {}


    for provider, spec in (
        PROVIDERS.items()
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


            eligible = (
                verification
                == "verified"
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


    production = {
        "zen": [],
        "nvidia": [],
    }


    quarantine = {
        "zen": [],
        "nvidia": [],
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


    for group in (
        "zen",
        "nvidia",
    ):

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
    main()
