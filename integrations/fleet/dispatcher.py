#!/usr/bin/env python3

from __future__ import annotations

import json
import math
import os
import sqlite3
import sys
import time
from pathlib import Path
from typing import Any

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
from fleet_runtime import fleet_root, verification_ttl_ms


HOME = Path.home()

BASE = fleet_root()

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
            PRAGMA busy_timeout=10000;
            PRAGMA journal_mode=WAL;
            PRAGMA synchronous=NORMAL;

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


    def _load_candidate_health(
        self,
    ) -> dict:

        """Load every candidate health row exactly once per selection pass.

        The audit measured ~4 SELECTs per candidate (4,003 total for 1,000
        candidates). Selection now issues two bulk reads (candidate +
        provider health) and reuses the in-memory index through filtering
        and ranking, so the query count is constant regardless of how many
        candidates the registry exposes.
        """

        rows = self.db.execute(
            """
            SELECT *
            FROM candidate_health
            """
        ).fetchall()

        return {
            row["candidate_key"]: row
            for row in rows
        }


    def _load_provider_health(
        self,
    ) -> dict:

        rows = self.db.execute(
            """
            SELECT *
            FROM provider_health
            """
        ).fetchall()

        return {
            row["provider_group"]: row
            for row in rows
        }


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
        provider_health: dict | None = None,
    ) -> bool:

        if provider_health is None:
            row = self._provider_row(
                group
            )
        else:
            row = provider_health.get(
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
        candidate_health: dict | None = None,
    ) -> bool:

        if candidate_health is None:
            row = self._candidate_row(
                key
            )
        else:
            row = candidate_health.get(
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
        candidate_health: dict | None = None,
    ) -> float:

        if candidate_health is None:
            row = self._candidate_row(
                key
            )
        else:
            row = candidate_health.get(
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


            #
            # The provider for a registry pool comes exclusively from
            # validated Fleet policy (fleet.json) — e.g. "nvidia",
            # "opencode-zen" or any protocol-backed provider configured
            # there. There is deliberately NO hardcoded Zen/NVIDIA
            # provider fallback map: enabling and aggregating providers
            # is configuration-only, and a registry pool that omits its
            # provider fails closed with a clear reason instead of
            # silently guessing a runtime provider from the group name.
            #
            provider = str(
                pool.get(
                    "provider"
                )
                or ""
            ).strip()

            if not provider:
                raise ValueError(
                    f"registry pool {pool_name!r} must declare its "
                    "provider in fleet.json (fail closed: no provider "
                    "mapping exists in routing code)"
                )


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

                    "providerAliases": [
                        str(alias).strip().lower()
                        for alias in (
                            pool.get("discoveryAliases")
                            or []
                        )
                        if str(alias).strip()
                    ],
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


    # HERMES_ROUTING_V1_BEGIN

    def _routing_v1(
        self,
    ) -> dict:

        value = (
            self.config.get(
                "routingV1"
            )
            or {}
        )

        if not isinstance(
            value,
            dict,
        ):
            return {}

        if value.get(
            "enabled"
        ) is not True:
            return {}

        return value


    def routing_profile(
        self,
        role: str,
        requested: str | None = None,
    ) -> str | None:

        routing = (
            self._routing_v1()
        )

        if not routing:
            return None

        profiles = (
            routing.get(
                "profiles"
            )
            or {}
        )

        requested = str(
            requested
            or ""
        ).strip().lower()

        if (
            requested
            and requested in profiles
        ):
            return requested

        role_profiles = (
            routing.get(
                "roleProfiles"
            )
            or {}
        )

        profile = str(
            role_profiles.get(
                role
            )
            or routing.get(
                "defaultProfile"
            )
            or ""
        ).strip().lower()

        if profile in profiles:
            return profile

        return None


    # FLEET_DYNAMIC_EVIDENCE_ROUTING_V2
    #
    # Profiles carry generic weighting rules only — never model IDs.
    # Ranking uses measured evidence: runtime health, verification
    # freshness, and probe latency when actually recorded.
    #
    def _routing_weights(
        self,
        profile: str | None,
    ) -> dict:

        routing = (
            self._routing_v1()
        )

        profile_data = (
            (
                routing.get(
                    "profiles"
                )
                or {}
            ).get(
                profile or ""
            )
            or {}
        )

        weights = (
            profile_data.get(
                "weights"
            )
            or {}
        )

        result = {}

        for key in (
            "capability",
            "health",
            "freshness",
            "latency",
        ):

            value = weights.get(
                key,
                1.0,
            )

            try:
                value = float(
                    value
                )
            except Exception:
                value = 1.0

            result[key] = max(
                0.0,
                value,
            )

        return result


    def _registry_model_rows(
        self,
    ) -> dict:

        rows = {}

        models = (
            self.registry.get(
                "models"
            )
        )

        if not isinstance(
            models,
            list,
        ):
            return rows

        for row in models:

            if not isinstance(
                row,
                dict,
            ):
                continue

            provider = str(
                row.get(
                    "provider"
                )
                or ""
            ).strip()

            model_id = str(
                row.get(
                    "id"
                )
                or ""
            ).strip()

            if provider and model_id:

                rows[
                    (provider, model_id)
                ] = row

        return rows


    def _verification_evidence(
        self,
        row: dict,
    ) -> tuple:

        """Return (fresh, age_ratio 0..1) for a registry row.

        Uses the dispatcher clock (now()) so tests can advance time
        deterministically; verification must be fresh at select time,
        not only at the last registry refresh.
        """

        if (
            row.get(
                "verification"
            )
            != "verified"
        ):
            return (
                False,
                1.0,
            )

        verified_at = (
            row.get(
                "verifiedAtMs"
            )
            or row.get(
                "lastProbeMs"
            )
        )

        if not verified_at:
            return (
                False,
                1.0,
            )

        ttl = verification_ttl_ms()

        #
        # TTL 0 means "no freshness cache" (fleet_runtime): cached
        # verification evidence is deterministically rejected rather
        # than trusted, and never divides by zero.
        #
        if ttl <= 0:
            return (
                False,
                1.0,
            )

        now_ms = int(
            now() * 1000
        )

        try:
            age = max(
                0,
                now_ms
                - int(
                    verified_at
                ),
            )
        except (TypeError, ValueError, OverflowError):
            return (
                False,
                1.0,
            )

        return (
            age < ttl,
            min(
                1.0,
                age / ttl,
            ),
        )


    def _candidate_eligibility(
        self,
        candidate: dict,
        registry_rows: dict | None = None,
    ) -> tuple:

        """Mandatory eligibility gates before ranking.

        Returns (eligible, reason, evidence). Evidence carries the
        registry row (when present) for the ranking stage.

        Registry-pool candidates require per-model verification
        evidence that is fresh at select time. A legacy
        productionModels snapshot alone never satisfies the gate, and
        rows not confirmed by the current discovery cycle
        (discoveryStale) are strictly excluded.
        """

        pools = (
            self.config.get("pools")
            or {}
        )

        pool = (
            pools.get(
                candidate.get("pool") or ""
            )
            or {}
        )

        pool_type = str(
            pool.get("type") or ""
        ).strip().lower()

        if registry_rows is None:
            registry_rows = (
                self._registry_model_rows()
            )

        row = registry_rows.get(
            (
                str(
                    candidate.get(
                        "provider"
                    )
                    or ""
                ).strip(),
                str(
                    candidate.get("model") or ""
                ).strip(),
            )
        )

        if pool_type == "registry":

            if row is None:
                return (
                    False,
                    "no_fresh_verification_evidence",
                    {},
                )

            if row.get("excludedReason"):
                return (
                    False,
                    "specialist_model",
                    {},
                )

            if row.get("discoveryStale"):
                return (
                    False,
                    "discovery_stale",
                    {},
                )

            if (
                row.get("verification")
                != "verified"
            ):
                return (
                    False,
                    "not_verified",
                    {},
                )

            fresh, age_ratio = (
                self._verification_evidence(
                    row
                )
            )

            if not fresh:
                return (
                    False,
                    "verification_expired",
                    {},
                )

            return (
                True,
                "verified",
                {
                    "row": row,
                    "age_ratio": age_ratio,
                },
            )

        if pool_type == "stable-route":

            #
            # Provider-managed route (e.g. the openrouter/free final
            # escape). Eligible by policy, not catalog verification.
            #
            return (
                True,
                "provider_managed",
                {},
            )

        if (
            pool_type
            == "hermes-runtime-config"
        ):

            #
            # Runtime-config candidates require an independently verified,
            # fresh registry row for the exact runtime model.
            #
            if row is None:
                return (
                    False,
                    "not_independently_verified",
                    {},
                )

            if row.get("discoveryStale"):
                return (
                    False,
                    "discovery_stale",
                    {},
                )

            fresh, age_ratio = (
                self._verification_evidence(
                    row
                )
            )

            if not fresh:
                return (
                    False,
                    "verification_expired",
                    {},
                )

            return (
                True,
                "independently_verified",
                {
                    "row": row,
                    "age_ratio": age_ratio,
                },
            )

        return (
            False,
            "unknown_pool_type",
            {},
        )


    def _capability_evidence(
        self,
        row,
    ) -> float:

        """Provider-neutral measured capability evidence in [0, 1].

        Ranking never uses model IDs, names or provider reputation.
        Capability is derived only from verification-time measurements:

          capability = reliability * (0.6 + 0.4 * context_capacity)

          reliability     — probe success history: 1.0 with zero measured
                            probe failures, minus 0.25 per failure.
          context_capacity— log-scaled measured context length:
                            8k → 0.0, 32k → 0.5, 128k → 1.0 (capped).

        Context length is a bounded measured signal, not model intelligence.
        lastProbeMs is freshness evidence only and never counts here.

        Unknown capability is exactly neutral (0.5) — never best, never
        artificially advantaged over measured evidence.
        """

        if row is None:
            return 0.5

        context_length = row.get(
            "contextLength"
        )

        has_context = isinstance(
            context_length,
            (int, float),
        ) and float(context_length) > 0

        try:
            probe_failures = int(
                row.get(
                    "probeFailureCount",
                    0,
                )
                or 0
            )
        except Exception:
            probe_failures = 0

        measured = bool(
            has_context
            or probe_failures > 0
        )

        if not measured:
            return 0.5

        if has_context:
            context_capacity = min(
                1.0,
                math.log2(
                    max(
                        float(context_length),
                        8192,
                    )
                    / 8192
                )
                / 4.0,
            )
        else:
            context_capacity = 0.0

        reliability = max(
            0.0,
            1.0
            - probe_failures * 0.25,
        )

        return round(
            reliability
            * (0.6 + 0.4 * context_capacity),
            6,
        )


    def _evidence_penalty(
        self,
        candidate: dict,
        evidence: dict,
        weights: dict,
        candidate_health: dict | None = None,
    ) -> float:

        """Deterministic evidence score (lower is better).

        Components, all normalized to 0..1:
          capability — provider-neutral measured verification evidence
                       (probe reliability + measured context capacity);
                       unknown is neutral (0.5).
          health     — failures / (successes + failures + 1) when the
                       candidate has at least one measured outcome;
                       zero-history (or unknown) is neutral (0.5).
          freshness  — verification age / TTL; unknown is worst (1.0).
          latency    — measured probe latency when actually recorded
                       (min(1, ms / 30000)); unrecorded is neutral (0.5).
        Stale discovery is handled by eligibility exclusion, not here.

        candidate_health is the per-pass in-memory index (candidate_key →
        row) so selection performs exactly two SELECT statements total,
        never one per candidate.
        """

        row = evidence.get("row")

        if candidate_health is None:
            candidate_health = {}

        health = candidate_health.get(
            candidate["candidateKey"]
        )

        #
        # Health is observed only when there is at least one measured
        # outcome. A merely-touched candidate (select(touch=True))
        # creates a zero-success / zero-failure row that must stay
        # neutral — it must never read as artificially perfect.
        #
        if health is not None and (
            int(health["successes"] or 0)
            + int(health["failures"] or 0)
        ) > 0:

            successes = int(
                health["successes"] or 0
            )

            failures = int(
                health["failures"] or 0
            )

            health_penalty = (
                failures
                / (
                    successes
                    + failures
                    + 1.0
                )
            )

        else:

            health_penalty = 0.5

        capability_penalty = (
            1.0
            - self._capability_evidence(row)
        )

        if row is not None:

            freshness_penalty = (
                evidence.get(
                    "age_ratio",
                    1.0,
                )
            )

            latency_ms = row.get(
                "lastProbeLatencyMs"
            )

            if (
                latency_ms is None
                or float(latency_ms) <= 0
            ):
                latency_penalty = 0.5
            else:
                latency_penalty = min(
                    1.0,
                    float(latency_ms)
                    / 30000.0,
                )

        else:

            freshness_penalty = 1.0
            latency_penalty = 0.5

        return (
            weights["capability"]
            * capability_penalty
            + weights["health"]
            * health_penalty
            + weights["freshness"]
            * freshness_penalty
            + weights["latency"]
            * latency_penalty
        )


    def _routing_is_final_escape(
        self,
        candidate: dict,
    ) -> bool:

        routing = (
            self._routing_v1()
        )

        final = (
            routing.get(
                "finalEscape"
            )
            or {}
        )

        if not isinstance(
            final,
            dict,
        ):
            return False

        return (
            str(
                candidate.get(
                    "providerGroup"
                )
                or ""
            ).strip().lower()
            ==
            str(
                final.get(
                    "providerGroup"
                )
                or ""
            ).strip().lower()

            and

            str(
                candidate.get(
                    "provider"
                )
                or ""
            ).strip().lower()
            ==
            str(
                final.get(
                    "provider"
                )
                or ""
            ).strip().lower()

            and

            str(
                candidate.get(
                    "model"
                )
                or ""
            ).strip()
            ==
            str(
                final.get(
                    "model"
                )
                or ""
            ).strip()
        )


    def _routing_sort_key(
        self,
        candidate: dict,
        profile: str,
        registry_rows: dict | None = None,
        candidate_health: dict | None = None,
    ):

        #
        # openrouter/free is the explicit FINAL escape route and must
        # stay last; every discovered eligible candidate outranks it.
        #
        if self._routing_is_final_escape(
            candidate
        ):

            return (
                2,
                0.0,
                int(
                    candidate.get(
                        "poolPriority",
                        999,
                    )
                ),
                self._last_used(
                    candidate[
                        "candidateKey"
                    ],
                    candidate_health,
                ),
                candidate[
                    "candidateKey"
                ],
            )

        weights = self._routing_weights(
            profile
        )

        eligible, _reason, evidence = (
            self._candidate_eligibility(
                candidate,
                registry_rows,
            )
        )

        #
        # Defensive: _select filters ineligible candidates before
        # ranking. An unexpected ineligible candidate must never win.
        #
        if not eligible:

            return (
                3,
                0.0,
                int(
                    candidate.get(
                        "poolPriority",
                        999,
                    )
                ),
                self._last_used(
                    candidate[
                        "candidateKey"
                    ],
                    candidate_health,
                ),
                candidate[
                    "candidateKey"
                ],
            )

        score = self._evidence_penalty(
            candidate,
            evidence,
            weights,
            candidate_health,
        )

        pool = (
            (self.config.get("pools") or {}).get(candidate.get("pool") or "")
            or {}
        )
        try:
            score += max(0.0, float(pool.get("automaticPenalty") or 0.0))
        except (TypeError, ValueError):
            pass

        #
        # Dynamic evidence ranking: measured capability, runtime health,
        # verification freshness and measured probe latency under profile
        # weights to pick the best verified route for the selected profile.
        # Pool priority, LRU recency and the candidate key are deterministic
        # tie-breakers only; no production model ID or provider name ever
        # enters the score.
        #
        return (
            1,
            round(
                score,
                9,
            ),
            int(
                candidate.get(
                    "poolPriority",
                    999,
                )
            ),
            self._last_used(
                candidate[
                    "candidateKey"
                ],
                candidate_health,
            ),
            candidate[
                "candidateKey"
            ],
        )

    # HERMES_ROUTING_V1_END


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
        profile: str | None = None,
    ) -> dict | None:

        if not touch:
            return self._select(
                role,
                touch=False,
                profile=profile,
            )

        self.db.execute(
            "BEGIN IMMEDIATE"
        )

        try:
            selected = self._select(
                role,
                touch=True,
                profile=profile,
            )
            self.db.commit()
            return selected

        except Exception:
            self.db.rollback()
            raise


    def _select(
        self,
        role: str,
        *,
        touch: bool,
        profile: str | None = None,
    ) -> dict | None:

        candidates = (
            self.candidates(
                role
            )
        )

        routing_profile = (
            self.routing_profile(
                role,
                requested=profile,
            )
        )

        #
        # Build the registry row index once per selection pass; both
        # the eligibility filter and the ranking sort reuse it.
        #
        registry_rows = (
            self._registry_model_rows()
        )

        #
        # Load candidate + provider health exactly once per selection
        # pass. Filtering (cooldown), ranking evidence and LRU recency
        # all reuse these in-memory indexes — never per-candidate
        # SELECTs.
        #
        candidate_health = (
            self._load_candidate_health()
        )

        provider_health = (
            self._load_provider_health()
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
                group,
                provider_health,
            ):

                continue


            if self._candidate_cooling(
                key,
                candidate_health,
            ):

                continue


            # FLEET_DYNAMIC_EVIDENCE_ROUTING_V2 — mandatory
            # eligibility before ranking: catalog presence,
            # fresh verification, non-specialist, and pool policy.
            eligible, _reason, _evidence = (
                self._candidate_eligibility(
                    candidate,
                    registry_rows,
                )
            )

            if not eligible:

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

        # HERMES_ROUTING_V1_SELECT_BEGIN
        if routing_profile:

            available = [
                candidate
                for pool in by_pool.values()
                for candidate in pool
            ]

            if role == "worker":
                reuse_delay = self._policy_seconds(
                    "workerRouteReuseDelaySeconds",
                    5,
                )

                reusable = [
                    candidate
                    for candidate in available
                    if (
                        now()
                        - self._last_used(
                            candidate[
                                "candidateKey"
                            ],
                            candidate_health,
                        )
                        >= reuse_delay
                    )
                ]

                if reusable:
                    available = reusable

            available.sort(
                key=lambda candidate:
                    self._routing_sort_key(
                        candidate,
                        routing_profile,
                        registry_rows,
                        candidate_health,
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

            return selected
        # HERMES_ROUTING_V1_SELECT_END

        if role == "worker":
            reuse_delay = self._policy_seconds(
                "workerRouteReuseDelaySeconds",
                5,
            )
            reusable = {
                priority: [
                    candidate
                    for candidate in pool
                    if now() - self._last_used(
                        candidate["candidateKey"],
                        candidate_health,
                    ) >= reuse_delay
                ]
                for priority, pool in by_pool.items()
            }
            reusable = {
                priority: pool
                for priority, pool in reusable.items()
                if pool
            }
            if reusable:
                by_pool = reusable


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
                    ],
                    candidate_health
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
            "account_quota":
                (
                    "quotaCooldownSeconds",
                    21600,
                ),

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


        # HERMES_FLEET_MODEL_SCOPED_FAILURES_V1
        #
        # Authentication/account-access failures normally invalidate the
        # provider credential itself and therefore remain provider-wide.
        #
        # Quota and request-timeout failures are intentionally
        # candidate-scoped. Genuine network/auth/account failures remain
        # provider-scoped.
        #
        # OpenCode Zen and NVIDIA can expose models with independent quotas,
        # queues and upstream health; one exhausted/slow model must not burn
        # every sibling route.
        if kind in {
            "auth",
            "account_access",
            "account_quota",
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


        elif kind == "quota":

            seconds = (
                self._policy_seconds(
                    "quotaCooldownSeconds",
                    21600,
                )
            )


        elif kind == "timeout":

            seconds = (
                self._policy_seconds(
                    "networkCooldownSeconds",
                    30,
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
