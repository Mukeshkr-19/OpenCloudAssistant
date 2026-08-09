#!/usr/bin/env python3

from __future__ import annotations

import json
import os
import sqlite3
import sys
import time
from pathlib import Path
from typing import Any

import yaml


HOME = Path.home()

BASE = (
    HOME
    / ".local"
    / "share"
    / "hermes-fleet"
)

FLEET_CONFIG = (
    BASE
    / "fleet.json"
)

REGISTRY = (
    BASE
    / "registry"
    / "models.json"
)

HERMES_CONFIG = (
    HOME
    / ".hermes"
    / "config.yaml"
)

HEALTH_DB = Path(
    os.environ.get(
        "HERMES_FLEET_HEALTH_DB",
        str(
            BASE
            / "health.sqlite"
        ),
    )
)


def now() -> float:
    return time.time()


def load_json(path: Path) -> dict:
    with path.open(
        encoding="utf-8"
    ) as f:
        value = json.load(f)

    return (
        value
        if isinstance(value, dict)
        else {}
    )


def load_yaml(path: Path) -> dict:
    try:
        with path.open(
            encoding="utf-8"
        ) as f:
            value = yaml.safe_load(f)

    except Exception:
        value = {}

    return (
        value
        if isinstance(value, dict)
        else {}
    )


class HermesFleet:

    def __init__(self):

        self.config = load_json(
            FLEET_CONFIG
        )

        self.registry = load_json(
            REGISTRY
        )

        HEALTH_DB.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        self.db = sqlite3.connect(
            HEALTH_DB,
            timeout=10,
        )

        self.db.row_factory = (
            sqlite3.Row
        )

        self._init_db()


    def close(self):

        try:
            self.db.close()
        except Exception:
            pass


    def _init_db(self):

        self.db.executescript(
            """
            PRAGMA journal_mode=WAL;
            PRAGMA synchronous=NORMAL;
            PRAGMA busy_timeout=10000;

            CREATE TABLE IF NOT EXISTS candidate_health (
                candidate_key TEXT PRIMARY KEY,
                provider_group TEXT NOT NULL,
                provider TEXT NOT NULL,
                model TEXT NOT NULL,

                cooldown_until REAL NOT NULL DEFAULT 0,

                last_failure_kind TEXT,
                last_failure_at REAL,

                last_used_at REAL NOT NULL DEFAULT 0,
                last_success_at REAL,

                successes INTEGER NOT NULL DEFAULT 0,
                failures INTEGER NOT NULL DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS provider_health (
                provider_group TEXT PRIMARY KEY,

                cooldown_until REAL NOT NULL DEFAULT 0,

                last_failure_kind TEXT,
                last_failure_at REAL,

                successes INTEGER NOT NULL DEFAULT 0,
                failures INTEGER NOT NULL DEFAULT 0,

                trip_window_started REAL NOT NULL DEFAULT 0,
                trip_rate_count INTEGER NOT NULL DEFAULT 0,
                trip_server_count INTEGER NOT NULL DEFAULT 0
            );
            """
        )

        self.db.commit()


    @staticmethod
    def candidate_key(
        provider_group: str,
        provider: str,
        model: str,
    ) -> str:

        return (
            f"{provider_group}:"
            f"{provider}:"
            f"{model}"
        )


    def _provider_row(
        self,
        group: str,
    ):

        return self.db.execute(
            """
            SELECT *
            FROM provider_health
            WHERE provider_group = ?
            """,
            (group,),
        ).fetchone()


    def _candidate_row(
        self,
        key: str,
    ):

        return self.db.execute(
            """
            SELECT *
            FROM candidate_health
            WHERE candidate_key = ?
            """,
            (key,),
        ).fetchone()


    def _provider_cooling(
        self,
        group: str,
    ) -> bool:

        row = self._provider_row(
            group
        )

        return bool(
            row
            and float(
                row["cooldown_until"]
                or 0
            ) > now()
        )


    def _candidate_cooling(
        self,
        key: str,
    ) -> bool:

        row = self._candidate_row(
            key
        )

        return bool(
            row
            and float(
                row["cooldown_until"]
                or 0
            ) > now()
        )


    def _last_used(
        self,
        key: str,
    ) -> float:

        row = self._candidate_row(
            key
        )

        if not row:
            return 0.0

        return float(
            row["last_used_at"]
            or 0
        )


    def _runtime_gemini_models(
        self,
    ) -> list[str]:

        cfg = load_yaml(
            HERMES_CONFIG
        )

        found = []


        def walk(value: Any):

            if isinstance(
                value,
                dict,
            ):

                provider = str(
                    value.get(
                        "provider",
                        "",
                    )
                    or ""
                ).strip().lower()

                model = str(
                    value.get(
                        "model",
                        "",
                    )
                    or ""
                ).strip()

                if (
                    provider
                    == "gemini"
                    and model
                ):

                    found.append(
                        model
                    )


                for child in (
                    value.values()
                ):

                    walk(child)


            elif isinstance(
                value,
                list,
            ):

                for child in value:
                    walk(child)


        walk(cfg)


        result = []

        seen = set()

        for model in found:

            if model in seen:
                continue

            seen.add(model)
            result.append(model)


        return result


    def _pool_candidates(
        self,
        pool_name: str,
    ) -> list[dict]:

        pools = (
            self.config.get(
                "pools"
            )
            or {}
        )

        pool = (
            pools.get(
                pool_name
            )
            or {}
        )

        pool_type = str(
            pool.get(
                "type",
                "",
            )
        )

        group = str(
            pool.get(
                "providerGroup",
                "",
            )
        )


        if pool_type == "registry":

            models = (
                self.registry
                .get(
                    "productionModels",
                    {}
                )
                .get(
                    group,
                    []
                )
                or []
            )


            provider = {
                "nvidia":
                    "nvidia",

                "zen":
                    "opencode-zen",
            }.get(group)


            if not provider:
                return []


            result = []

            for model in models:

                if not isinstance(
                    model,
                    str,
                ):
                    continue

                model = (
                    model.strip()
                )

                if not model:
                    continue

                result.append({
                    "pool":
                        pool_name,

                    "providerGroup":
                        group,

                    "provider":
                        provider,

                    "model":
                        model,
                })


            return result


        if (
            pool_type
            == "stable-route"
        ):

            provider = str(
                pool.get(
                    "provider",
                    "",
                )
            ).strip()

            model = str(
                pool.get(
                    "route",
                    "",
                )
            ).strip()


            if not provider or not model:
                return []


            return [{
                "pool":
                    pool_name,

                "providerGroup":
                    group,

                "provider":
                    provider,

                "model":
                    model,
            }]


        if (
            pool_type
            == "hermes-runtime-config"
            and group
            == "gemini"
        ):

            provider = str(
                pool.get(
                    "provider",
                    "gemini",
                )
            ).strip()


            return [
                {
                    "pool":
                        pool_name,

                    "providerGroup":
                        group,

                    "provider":
                        provider,

                    "model":
                        model,
                }

                for model
                in self._runtime_gemini_models()
            ]


        return []


    def candidates(
        self,
        role: str,
    ) -> list[dict]:

        roles = (
            self.config.get(
                "roles"
            )
            or {}
        )

        pool_names = (
            roles.get(role)
            or []
        )

        result = []


        for pool_index, pool_name in enumerate(
            pool_names
        ):

            pool_candidates = (
                self._pool_candidates(
                    pool_name
                )
            )


            for candidate in pool_candidates:

                candidate = dict(
                    candidate
                )

                candidate[
                    "poolPriority"
                ] = pool_index

                candidate[
                    "candidateKey"
                ] = self.candidate_key(
                    candidate[
                        "providerGroup"
                    ],
                    candidate[
                        "provider"
                    ],
                    candidate[
                        "model"
                    ],
                )

                result.append(
                    candidate
                )


        return result


    def select(
        self,
        role: str,
        *,
        touch: bool = True,
    ) -> dict | None:

        candidates = (
            self.candidates(
                role
            )
        )


        by_pool: dict[
            int,
            list[dict],
        ] = {}


        for candidate in candidates:

            group = candidate[
                "providerGroup"
            ]

            key = candidate[
                "candidateKey"
            ]


            if self._provider_cooling(
                group
            ):

                continue


            if self._candidate_cooling(
                key
            ):

                continue


            by_pool.setdefault(
                candidate[
                    "poolPriority"
                ],
                [],
            ).append(
                candidate
            )


        if not by_pool:
            return None


        first_pool = min(
            by_pool
        )


        available = (
            by_pool[
                first_pool
            ]
        )


        available.sort(
            key=lambda c: (
                self._last_used(
                    c[
                        "candidateKey"
                    ]
                ),

                c[
                    "candidateKey"
                ],
            )
        )


        selected = dict(
            available[0]
        )


        if touch:

            self.db.execute(
                """
                INSERT INTO candidate_health (
                    candidate_key,
                    provider_group,
                    provider,
                    model,
                    last_used_at
                )
                VALUES (?, ?, ?, ?, ?)

                ON CONFLICT(candidate_key)
                DO UPDATE SET
                    last_used_at = excluded.last_used_at
                """,

                (
                    selected[
                        "candidateKey"
                    ],

                    selected[
                        "providerGroup"
                    ],

                    selected[
                        "provider"
                    ],

                    selected[
                        "model"
                    ],

                    now(),
                ),
            )

            self.db.commit()


        return selected


    def success(
        self,
        candidate: dict,
    ):

        key = candidate[
            "candidateKey"
        ]

        group = candidate[
            "providerGroup"
        ]


        self.db.execute(
            """
            INSERT INTO candidate_health (
                candidate_key,
                provider_group,
                provider,
                model,
                last_success_at,
                successes
            )
            VALUES (?, ?, ?, ?, ?, 1)

            ON CONFLICT(candidate_key)
            DO UPDATE SET
                last_success_at = excluded.last_success_at,
                cooldown_until = 0,
                last_failure_kind = NULL,
                successes = successes + 1
            """,

            (
                key,
                group,
                candidate[
                    "provider"
                ],
                candidate[
                    "model"
                ],
                now(),
            ),
        )


        self.db.execute(
            """
            INSERT INTO provider_health (
                provider_group,
                last_failure_at,
                successes
            )
            VALUES (?, NULL, 1)

            ON CONFLICT(provider_group)
            DO UPDATE SET
                successes = successes + 1
            """,

            (
                group,
            ),
        )

        self.db.commit()


    def _policy_seconds(
        self,
        key: str,
        default: int,
    ) -> int:

        policy = (
            self.config.get(
                "failurePolicy"
            )
            or {}
        )

        try:
            return int(
                policy.get(
                    key,
                    default,
                )
            )

        except Exception:
            return default


    def provider_failure(
        self,
        group: str,
        kind: str,
    ):

        kind = str(
            kind
        ).strip().lower()


        mapping = {
            "quota":
                (
                    "quotaCooldownSeconds",
                    21600,
                ),

            "rate_limit":
                (
                    "rateLimitCooldownSeconds",
                    300,
                ),

            "server":
                (
                    "serverErrorCooldownSeconds",
                    60,
                ),

            "network":
                (
                    "networkCooldownSeconds",
                    30,
                ),

            "auth":
                (
                    "authCooldownSeconds",
                    21600,
                ),

            "account_access":
                (
                    "accountAccessCooldownSeconds",
                    86400,
                ),
        }


        policy_key, default = (
            mapping.get(
                kind,
                (
                    "networkCooldownSeconds",
                    30,
                ),
            )
        )


        cooldown = (
            now()
            + self._policy_seconds(
                policy_key,
                default,
            )
        )


        self.db.execute(
            """
            INSERT INTO provider_health (
                provider_group,
                cooldown_until,
                last_failure_kind,
                last_failure_at,
                failures
            )
            VALUES (?, ?, ?, ?, 1)

            ON CONFLICT(provider_group)
            DO UPDATE SET
                cooldown_until = excluded.cooldown_until,
                last_failure_kind = excluded.last_failure_kind,
                last_failure_at = excluded.last_failure_at,
                failures = failures + 1
            """,

            (
                group,
                cooldown,
                kind,
                now(),
            ),
        )

        self.db.commit()



    def _record_transient_provider_failure(
        self,
        candidate: dict,
        kind: str,
    ) -> bool:
        """
        Promote repeated transient failures across DISTINCT candidates
        into a provider-wide cooldown.

        A single weak/unavailable model must not burn an entire provider,
        but multiple different models returning the same rate/server
        condition inside a short window is strong evidence that continuing
        to cycle siblings will only add latency.

        Returns True when the provider was tripped.
        """

        kind = str(
            kind
        ).strip().lower()

        if kind not in {
            "rate_limit",
            "server",
        }:
            return False


        group = candidate[
            "providerGroup"
        ]

        candidate_key = candidate[
            "candidateKey"
        ]

        timestamp = now()


        if kind == "rate_limit":

            threshold = self._policy_seconds(
                "providerRateTripCount",
                2,
            )

        else:

            threshold = self._policy_seconds(
                "providerServerTripCount",
                2,
            )


        window = self._policy_seconds(
            "providerTripWindowSeconds",
            60,
        )


        #
        # This table intentionally records only model/candidate identity +
        # time. No prompts, responses, credentials, account IDs or user data.
        #
        self.db.execute(
            """
            CREATE TABLE IF NOT EXISTS provider_failure_events (
                provider_group TEXT NOT NULL,
                failure_kind TEXT NOT NULL,
                candidate_key TEXT NOT NULL,
                seen_at REAL NOT NULL,

                PRIMARY KEY (
                    provider_group,
                    failure_kind,
                    candidate_key
                )
            )
            """
        )


        cutoff = (
            timestamp
            - max(
                1,
                window,
            )
        )


        self.db.execute(
            """
            DELETE FROM provider_failure_events
            WHERE seen_at < ?
            """,
            (
                cutoff,
            ),
        )


        self.db.execute(
            """
            INSERT INTO provider_failure_events (
                provider_group,
                failure_kind,
                candidate_key,
                seen_at
            )
            VALUES (?, ?, ?, ?)

            ON CONFLICT(
                provider_group,
                failure_kind,
                candidate_key
            )
            DO UPDATE SET
                seen_at = excluded.seen_at
            """,
            (
                group,
                kind,
                candidate_key,
                timestamp,
            ),
        )


        row = self.db.execute(
            """
            SELECT COUNT(
                DISTINCT candidate_key
            ) AS candidate_count

            FROM provider_failure_events

            WHERE provider_group = ?
              AND failure_kind = ?
              AND seen_at >= ?
            """,
            (
                group,
                kind,
                cutoff,
            ),
        ).fetchone()


        count = int(
            row[
                "candidate_count"
            ]
            if row
            else 0
        )


        self.db.commit()


        if count < max(
            1,
            threshold,
        ):
            return False


        #
        # Existing provider_failure() owns the canonical provider cooldown
        # durations and provider_health bookkeeping.
        #
        self.provider_failure(
            group,
            kind,
        )


        #
        # Consume this evidence after a provider trip so stale events do not
        # immediately retrip it when the cooldown later expires.
        #
        self.db.execute(
            """
            DELETE FROM provider_failure_events
            WHERE provider_group = ?
              AND failure_kind = ?
            """,
            (
                group,
                kind,
            ),
        )

        self.db.commit()

        return True


    def failure(
        self,
        candidate: dict,
        kind: str,
    ):

        kind = str(
            kind
        ).strip().lower()


        if kind in {
            "quota",
            "auth",
            "account_access",
            "network",
        }:

            self.provider_failure(
                candidate[
                    "providerGroup"
                ],
                kind,
            )

            return


        if kind == "model_unavailable":

            seconds = (
                self._policy_seconds(
                    "modelUnavailableCooldownSeconds",
                    21600,
                )
            )


        elif kind == "rate_limit":

            seconds = (
                self._policy_seconds(
                    "rateLimitCooldownSeconds",
                    300,
                )
            )


        elif kind == "server":

            seconds = (
                self._policy_seconds(
                    "serverErrorCooldownSeconds",
                    60,
                )
            )


        else:

            seconds = (
                self._policy_seconds(
                    "networkCooldownSeconds",
                    30,
                )
            )


        key = candidate[
            "candidateKey"
        ]


        self.db.execute(
            """
            INSERT INTO candidate_health (
                candidate_key,
                provider_group,
                provider,
                model,
                cooldown_until,
                last_failure_kind,
                last_failure_at,
                failures
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, 1)

            ON CONFLICT(candidate_key)
            DO UPDATE SET
                cooldown_until = excluded.cooldown_until,
                last_failure_kind = excluded.last_failure_kind,
                last_failure_at = excluded.last_failure_at,
                failures = failures + 1
            """,

            (
                key,

                candidate[
                    "providerGroup"
                ],

                candidate[
                    "provider"
                ],

                candidate[
                    "model"
                ],

                now()
                + seconds,

                kind,

                now(),
            ),
        )

        self.db.commit()

        if kind in {
            "rate_limit",
            "server",
        }:
            self._record_transient_provider_failure(
                candidate,
                kind,
            )


    def status(self):

        print(
            "Health DB:",
            HEALTH_DB,
        )

        print()


        for role in (
            "main",
            "worker",
            "reviewer",
        ):

            candidate = (
                self.select(
                    role,
                    touch=False,
                )
            )

            if candidate:

                print(
                    f"{role}: "
                    f"{candidate['providerGroup']} / "
                    f"{candidate['provider']} / "
                    f"{candidate['model']}"
                )

            else:

                print(
                    f"{role}: NO AVAILABLE CANDIDATE"
                )


        print()
        print(
            "Provider health:"
        )


        rows = self.db.execute(
            """
            SELECT
                provider_group,
                cooldown_until,
                last_failure_kind,
                successes,
                failures
            FROM provider_health
            ORDER BY provider_group
            """
        ).fetchall()


        for row in rows:

            remaining = max(
                0,
                int(
                    float(
                        row[
                            "cooldown_until"
                        ]
                        or 0
                    )
                    - now()
                ),
            )


            print(
                " ",
                row[
                    "provider_group"
                ],
                "cooldown=",
                remaining,
                "kind=",
                row[
                    "last_failure_kind"
                ],
                "successes=",
                row[
                    "successes"
                ],
                "failures=",
                row[
                    "failures"
                ],
            )


def main():

    fleet = HermesFleet()

    try:

        if len(
            sys.argv
        ) < 2:

            fleet.status()
            return


        command = (
            sys.argv[1]
        )


        if command == "select":

            role = (
                sys.argv[2]
                if len(
                    sys.argv
                ) > 2
                else "worker"
            )

            candidate = (
                fleet.select(
                    role
                )
            )


            print(
                json.dumps(
                    candidate,
                    sort_keys=True,
                )
            )

            return


        if (
            command
            == "provider-fail"
        ):

            group = sys.argv[2]
            kind = sys.argv[3]

            fleet.provider_failure(
                group,
                kind,
            )

            print(
                "OK"
            )

            return


        if command == "status":

            fleet.status()
            return


        raise SystemExit(
            "Unknown command"
        )


    finally:

        fleet.close()


if __name__ == "__main__":
    main()
