#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import json
import os
import tempfile
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]

DISPATCHER = (
    ROOT
    / "integrations"
    / "fleet"
    / "dispatcher.py"
)

PRODUCTION_POLICY = (
    ROOT
    / "config"
    / "fleet"
    / "hermes-fleet-policy.json"
)


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def fixture_policy() -> dict:
    production = json.loads(
        PRODUCTION_POLICY.read_text(
            encoding="utf-8"
        )
    )

    failure_policy = dict(
        production.get("failurePolicy")
        or {}
    )

    require(
        failure_policy.get(
            "providerRateTripCount"
        )
        == 2,
        "production rate-limit trip threshold changed",
    )

    require(
        failure_policy.get(
            "providerServerTripCount"
        )
        == 2,
        "production server trip threshold changed",
    )

    openrouter = (
        production.get("pools", {})
        .get("openrouter-free", {})
    )

    require(
        openrouter.get("route")
        == "openrouter/free",
        "production OpenRouter route is not openrouter/free",
    )

    return {
        "version": 1,
        "enabled": True,

        "roles": {
            "main": [
                "fault-fixture-a",
                "fault-fixture-b",
                "openrouter-free",
            ],
            "reviewer": [
                "fault-fixture-a",
                "fault-fixture-b",
                "openrouter-free",
            ],
        },

        "pools": {
            "fault-fixture-a": {
                "type": "stable-route",
                "providerGroup": "nvidia",
                "provider": "nvidia",
                "route": "fixture/a",
            },

            "fault-fixture-b": {
                "type": "stable-route",
                "providerGroup": "nvidia",
                "provider": "nvidia",
                "route": "fixture/b",
            },

            "openrouter-free": {
                "type": "stable-route",
                "providerGroup": "openrouter",
                "provider": "openrouter",
                "route": "openrouter/free",
            },
        },

        "failurePolicy":
            failure_policy,
    }


class FleetCase:

    def __enter__(self):

        self.temp = (
            tempfile.TemporaryDirectory(
                prefix="opencloud-fleet-fi-"
            )
        )

        root = Path(
            self.temp.name
        )

        self.home = (
            root
            / "home"
        )

        self.base = (
            self.home
            / ".local"
            / "share"
            / "hermes-fleet"
        )

        registry = (
            self.base
            / "registry"
        )

        registry.mkdir(
            parents=True,
            exist_ok=True,
        )

        (
            self.base
            / "fleet.json"
        ).write_text(
            json.dumps(
                fixture_policy(),
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )

        (
            registry
            / "models.json"
        ).write_text(
            "{}\n",
            encoding="utf-8",
        )

        self.health = (
            root
            / "health.sqlite"
        )

        os.environ[
            "HOME"
        ] = str(
            self.home
        )

        os.environ[
            "OPEN_CLOUD_FLEET_HOME"
        ] = str(
            self.base
        )

        os.environ[
            "HERMES_FLEET_HEALTH_DB"
        ] = str(
            self.health
        )

        module_name = (
            "opencloud_fault_"
            + str(
                time.time_ns()
            )
        )

        spec = (
            importlib.util.spec_from_file_location(
                module_name,
                DISPATCHER,
            )
        )

        require(
            spec is not None
            and spec.loader is not None,
            "unable to load Fleet dispatcher",
        )

        self.module = (
            importlib.util.module_from_spec(
                spec
            )
        )

        spec.loader.exec_module(
            self.module
        )

        require(
            self.module.HEALTH_DB
            == self.health,
            "dispatcher escaped isolated health database",
        )

        require(
            self.module.BASE
            == self.base.resolve(),
            "dispatcher escaped isolated HOME",
        )

        self.clock = [
            1000000.0
        ]

        self.module.now = (
            lambda:
                self.clock[0]
        )

        self.fleet = (
            self.module.HermesFleet()
        )

        return self


    def __exit__(
        self,
        exc_type,
        exc,
        traceback,
    ):

        try:
            self.fleet.close()
        finally:
            self.temp.cleanup()


def selected(
    case: FleetCase,
) -> dict:

    candidate = (
        case.fleet.select(
            "main",
            touch=False,
        )
    )

    require(
        candidate is not None,
        "Fleet returned no candidate",
    )

    return candidate


def provider_cooling(
    case: FleetCase,
    group: str,
) -> bool:

    row = (
        case.fleet._provider_row(
            group
        )
    )

    if row is None:
        return False

    return (
        float(
            row[
                "cooldown_until"
            ]
            or 0
        )
        > case.clock[0]
    )


def assert_model(
    candidate: dict,
    expected: str,
) -> None:

    require(
        candidate.get("model")
        == expected,
        (
            "expected "
            + expected
            + ", got "
            + str(
                candidate.get("model")
            )
        ),
    )


def test_candidate_isolation_and_success_recovery() -> None:

    start = (
        time.perf_counter()
    )

    with FleetCase() as case:

        first = selected(
            case
        )

        assert_model(
            first,
            "fixture/a",
        )

        case.fleet.failure(
            first,
            "model_unavailable",
        )

        second = selected(
            case
        )

        assert_model(
            second,
            "fixture/b",
        )

        require(
            not provider_cooling(
                case,
                "nvidia",
            ),
            "single unavailable model incorrectly cooled provider",
        )

        case.fleet.success(
            first
        )

        recovered = selected(
            case
        )

        assert_model(
            recovered,
            "fixture/a",
        )

    elapsed = (
        (
            time.perf_counter()
            - start
        )
        * 1000
    )

    print(
        "PASS candidate isolation and success recovery"
    )

    print(
        "MEASURE candidate_recovery_test_ms="
        + format(
            elapsed,
            ".3f",
        )
    )



def test_quota_candidate_isolation() -> None:

    with FleetCase() as case:

        first = selected(case)

        assert_model(
            first,
            "fixture/a",
        )

        case.fleet.failure(
            first,
            "quota",
        )

        require(
            case.fleet._candidate_cooling(
                first["candidateKey"]
            ),
            "quota failure did not cool candidate",
        )

        require(
            not provider_cooling(
                case,
                "nvidia",
            ),
            "candidate quota incorrectly cooled provider",
        )

        second = selected(case)

        assert_model(
            second,
            "fixture/b",
        )

    print(
        "PASS candidate-scoped quota isolation"
    )


def test_timeout_candidate_isolation() -> None:

    with FleetCase() as case:

        first = selected(case)

        assert_model(
            first,
            "fixture/a",
        )

        case.fleet.failure(
            first,
            "timeout",
        )

        require(
            case.fleet._candidate_cooling(
                first["candidateKey"]
            ),
            "timeout failure did not cool candidate",
        )

        require(
            not provider_cooling(
                case,
                "nvidia",
            ),
            "candidate timeout incorrectly cooled provider",
        )

        second = selected(case)

        assert_model(
            second,
            "fixture/b",
        )

    print(
        "PASS candidate-scoped timeout isolation"
    )


def test_account_quota_provider_scope() -> None:

    with FleetCase() as case:

        first = selected(case)
        case.fleet.failure(first, "account_quota")

        require(
            provider_cooling(case, "nvidia"),
            "account-wide quota did not cool provider",
        )

        fallback = selected(case)
        assert_model(fallback, "openrouter/free")

    print("PASS account-scoped quota isolation")



def test_routing_v1_profile_selection() -> None:

    with FleetCase() as case:

        #
        # A registry pool with three fixture models carrying MEASURED
        # evidence only: verification age and probe latency. Profiles
        # contain generic weighting rules — never model IDs. The
        # dispatcher clock is pinned at 1_000_000.0 s by FleetCase,
        # so now_ms == 1_000_000_000 and all ages are deterministic.
        #
        case.fleet.config["pools"]["fixture-registry"] = {
            "type": "registry",
            "providerGroup": "nvidia",
            "provider": "nvidia",
            "discoveryAliases": ["nvidia"],
            "freeOnly": False,
        }

        case.fleet.config["roles"]["main"] = [
            "fixture-registry",
            "openrouter-free",
        ]

        ttl_ms = 86_400_000  # fleet_runtime DEFAULT_TTL_SECONDS

        now_ms = 1_000_000_000

        def evidence_row(
            model: str,
            age_ms: int,
            latency_ms: int,
        ) -> dict:
            return {
                "provider": "nvidia",
                "providerGroup": "nvidia",
                "id": model,
                "verification": "verified",
                "verifiedAtMs": now_ms - age_ms,
                "lastProbeLatencyMs": latency_ms,
            }

        case.fleet.registry["productionModels"] = {
            "nvidia": [
                "fixture/a",
                "fixture/b",
                "fixture/c",
            ],
        }

        case.fleet.registry["models"] = [
            # Freshest verification, slowest measured probe.
            evidence_row("fixture/a", int(0.05 * ttl_ms), 25_000),
            # Middle of the road on both axes.
            evidence_row("fixture/b", int(0.50 * ttl_ms), 10_000),
            # Stalest (but still fresh) verification, fastest probe.
            evidence_row("fixture/c", int(0.95 * ttl_ms), 500),
        ]

        case.fleet.config["routingV1"] = {
            "enabled": True,
            "defaultProfile": "balanced",

            "roleProfiles": {
                "main": "balanced",
            },

            "allDiscoveredFallback": True,

            "profiles": {
                "fast": {
                    "weights": {
                        "health": 2.0,
                        "freshness": 1.0,
                        "latency": 4.0,
                    },
                },

                "balanced": {
                    "weights": {
                        "health": 2.0,
                        "freshness": 2.0,
                        "latency": 2.0,
                    },
                },

                "deep": {
                    "weights": {
                        "health": 3.0,
                        "freshness": 3.0,
                        "latency": 1.0,
                    },
                },
            },

            "finalEscape": {
                "providerGroup": "openrouter",
                "provider": "openrouter",
                "model": "openrouter/free",
            },
        }

        fast = case.fleet.select(
            "main",
            touch=False,
            profile="fast",
        )

        balanced = case.fleet.select(
            "main",
            touch=False,
            profile="balanced",
        )

        deep = case.fleet.select(
            "main",
            touch=False,
            profile="deep",
        )

        default = case.fleet.select(
            "main",
            touch=False,
        )

        require(
            fast is not None,
            "FAST returned no candidate",
        )

        require(
            balanced is not None,
            "BALANCED returned no candidate",
        )

        require(
            deep is not None,
            "DEEP returned no candidate",
        )

        require(
            default is not None,
            "default profile returned no candidate",
        )

        assert_model(
            fast,
            "fixture/c",
        )

        assert_model(
            balanced,
            "fixture/b",
        )

        assert_model(
            deep,
            "fixture/a",
        )

        assert_model(
            default,
            "fixture/b",
        )

        for candidate in (
            fast,
            balanced,
            deep,
            default,
        ):

            require(
                candidate["model"] != "openrouter/free",
                "final escape ranked before measured evidence",
            )

    print(
        "PASS Routing V1 evidence-weighted FAST/BALANCED/DEEP selection"
    )


def test_routing_v1_discovered_fallback_and_final_escape() -> None:

    with FleetCase() as case:

        case.fleet.config["routingV1"] = {
            "enabled": True,
            "defaultProfile": "fast",

            "roleProfiles": {
                "main": "fast",
            },

            "allDiscoveredFallback": True,

            "profiles": {
                "fast": {
                    #
                    # Generic weights only. With equal evidence the pool
                    # order is a tie-breaker: Fleet must still use a
                    # discovered/eligible model before OpenRouter.
                    #
                    "weights": {
                        "health": 1.0,
                        "freshness": 1.0,
                        "latency": 1.0,
                    },
                },
            },

            "finalEscape": {
                "providerGroup": "openrouter",
                "provider": "openrouter",
                "model": "openrouter/free",
            },
        }

        generic = case.fleet.select(
            "main",
            touch=False,
            profile="fast",
        )

        require(
            generic is not None,
            "discovered fallback returned no candidate",
        )

        require(
            generic["model"] != "openrouter/free",
            "OpenRouter escaped before discovered model capacity",
        )

        candidates = {
            candidate["model"]: candidate
            for candidate in case.fleet.candidates(
                "main"
            )
        }

        for model in (
            "fixture/a",
            "fixture/b",
        ):

            case.fleet.failure(
                candidates[model],
                "model_unavailable",
            )

        final = case.fleet.select(
            "main",
            touch=False,
            profile="fast",
        )

        require(
            final is not None,
            "final escape returned no candidate",
        )

        assert_model(
            final,
            "openrouter/free",
        )

    print(
        "PASS discovered fallback before final openrouter/free"
    )


def test_production_routing_v1_contract() -> None:

    production = json.loads(
        PRODUCTION_POLICY.read_text(
            encoding="utf-8"
        )
    )

    routing = production.get(
        "routingV1"
    ) or {}

    require(
        routing.get("enabled") is True,
        "production Routing V1 disabled",
    )

    require(
        routing.get("allDiscoveredFallback") is True,
        "all discovered fallback disabled",
    )

    final = routing.get(
        "finalEscape"
    ) or {}

    require(
        final.get("provider") == "openrouter"
        and final.get("model") == "openrouter/free",
        "production final escape changed from openrouter/free",
    )

    profiles = routing.get(
        "profiles"
    ) or {}

    require(
        set(profiles.keys()) == {"fast", "balanced", "deep"},
        "production Routing V1 profiles changed",
    )

    #
    # Profiles must carry generic weighting rules ONLY — never exact
    # model preferences or any concrete model IDs.
    #
    for name, profile in profiles.items():

        require(
            "preferredModels" not in profile,
            f"profile {name} still carries exact model preferences",
        )

        weights = profile.get("weights") or {}

        for key in ("health", "freshness", "latency"):

            value = weights.get(key)

            require(
                isinstance(value, (int, float))
                and float(value) >= 0.0,
                f"profile {name} missing generic weight {key}",
            )

    def profile_models(value):
        found = []
        if isinstance(value, dict):
            for key, item in value.items():
                if key == "model" and isinstance(item, str):
                    found.append(item.strip())
                found.extend(profile_models(item))
        elif isinstance(value, list):
            for item in value:
                found.extend(profile_models(item))
        return found

    require(
        profile_models(profiles) == [],
        "production Routing V1 profiles contain concrete model IDs",
    )

    print(
        "PASS production Routing V1 contract (evidence-based, model-free profiles)"
    )


def test_rate_limit_distinct_candidate_trip() -> None:

    start = (
        time.perf_counter()
    )

    with FleetCase() as case:

        first = selected(
            case
        )

        assert_model(
            first,
            "fixture/a",
        )

        case.fleet.failure(
            first,
            "rate_limit",
        )

        second = selected(
            case
        )

        assert_model(
            second,
            "fixture/b",
        )

        require(
            not provider_cooling(
                case,
                "nvidia",
            ),
            "first rate-limit incorrectly tripped provider",
        )

        case.fleet.failure(
            first,
            "rate_limit",
        )

        require(
            not provider_cooling(
                case,
                "nvidia",
            ),
            "repeated same-candidate failure incorrectly tripped provider",
        )

        still_second = selected(
            case
        )

        assert_model(
            still_second,
            "fixture/b",
        )

        case.fleet.failure(
            second,
            "rate_limit",
        )

        require(
            provider_cooling(
                case,
                "nvidia",
            ),
            "two distinct rate-limited candidates did not trip provider",
        )

        fallback = selected(
            case
        )

        assert_model(
            fallback,
            "openrouter/free",
        )

        cooldown = int(
            case.fleet.config[
                "failurePolicy"
            ][
                "rateLimitCooldownSeconds"
            ]
        )

        case.clock[0] += (
            cooldown
            + 1
        )

        recovered = selected(
            case
        )

        require(
            recovered.get(
                "providerGroup"
            )
            == "nvidia",
            "provider did not recover after cooldown expiry",
        )

        assert_model(
            recovered,
            "fixture/a",
        )

    elapsed = (
        (
            time.perf_counter()
            - start
        )
        * 1000
    )

    print(
        "PASS distinct-candidate rate-limit trip"
    )

    print(
        "PASS OpenRouter free fallback"
    )

    print(
        "PASS provider cooldown expiry recovery"
    )

    print(
        "MEASURE rate_limit_failover_test_ms="
        + format(
            elapsed,
            ".3f",
        )
    )


def test_server_error_distinct_candidate_trip() -> None:

    start = (
        time.perf_counter()
    )

    with FleetCase() as case:

        first = selected(
            case
        )

        case.fleet.failure(
            first,
            "server",
        )

        second = selected(
            case
        )

        assert_model(
            second,
            "fixture/b",
        )

        require(
            not provider_cooling(
                case,
                "nvidia",
            ),
            "first server error incorrectly tripped provider",
        )

        case.fleet.failure(
            second,
            "server",
        )

        require(
            provider_cooling(
                case,
                "nvidia",
            ),
            "two distinct server failures did not trip provider",
        )

        fallback = selected(
            case
        )

        assert_model(
            fallback,
            "openrouter/free",
        )

        cooldown = int(
            case.fleet.config[
                "failurePolicy"
            ][
                "serverErrorCooldownSeconds"
            ]
        )

        case.clock[0] += (
            cooldown
            + 1
        )

        recovered = selected(
            case
        )

        require(
            recovered.get(
                "providerGroup"
            )
            == "nvidia",
            "server-error provider did not recover after cooldown",
        )

    elapsed = (
        (
            time.perf_counter()
            - start
        )
        * 1000
    )

    print(
        "PASS distinct-candidate server-error trip"
    )

    print(
        "MEASURE server_failover_test_ms="
        + format(
            elapsed,
            ".3f",
        )
    )


def test_network_provider_failure() -> None:

    start = (
        time.perf_counter()
    )

    with FleetCase() as case:

        first = selected(
            case
        )

        case.fleet.failure(
            first,
            "network",
        )

        require(
            provider_cooling(
                case,
                "nvidia",
            ),
            "network failure did not cool provider",
        )

        fallback = selected(
            case
        )

        assert_model(
            fallback,
            "openrouter/free",
        )

        cooldown = int(
            case.fleet.config[
                "failurePolicy"
            ][
                "networkCooldownSeconds"
            ]
        )

        case.clock[0] += (
            cooldown
            + 1
        )

        recovered = selected(
            case
        )

        require(
            recovered.get(
                "providerGroup"
            )
            == "nvidia",
            "network-failed provider did not recover after cooldown",
        )

    elapsed = (
        (
            time.perf_counter()
            - start
        )
        * 1000
    )

    print(
        "PASS provider-wide network failover and recovery"
    )

    print(
        "MEASURE network_failover_test_ms="
        + format(
            elapsed,
            ".3f",
        )
    )


def main() -> None:

    require(
        DISPATCHER.is_file(),
        "Fleet dispatcher source missing",
    )

    require(
        PRODUCTION_POLICY.is_file(),
        "Fleet policy source missing",
    )

    print(
        "Open Cloud Assistant Fleet fault injection"
    )

    print(
        "Isolation: temporary HOME and SQLite health database"
    )

    print(
        "Provider calls: none"
    )

    test_candidate_isolation_and_success_recovery()
    test_quota_candidate_isolation()
    test_account_quota_provider_scope()
    test_timeout_candidate_isolation()
    test_routing_v1_profile_selection()
    test_routing_v1_discovered_fallback_and_final_escape()
    test_production_routing_v1_contract()
    test_rate_limit_distinct_candidate_trip()
    test_server_error_distinct_candidate_trip()
    test_network_provider_failure()

    print(
        "INFO measured times are local test execution duration, not provider latency or an SLO"
    )

    print(
        "FLEET_FAULT_INJECTION: PASS"
    )


if __name__ == "__main__":
    main()
