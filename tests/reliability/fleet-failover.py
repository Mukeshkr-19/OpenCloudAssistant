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



def test_routing_v1_profile_selection() -> None:

    with FleetCase() as case:

        #
        # Add a third synthetic model so FAST / BALANCED / DEEP can each
        # select a distinct candidate.
        #
        case.fleet.config["pools"]["fault-fixture-c"] = {
            "type": "stable-route",
            "providerGroup": "nvidia",
            "provider": "nvidia",
            "route": "fixture/c",
        }

        case.fleet.config["roles"]["main"] = [
            "fault-fixture-a",
            "fault-fixture-b",
            "fault-fixture-c",
            "openrouter-free",
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
                    "preferredModels": [
                        {
                            "providerGroup": "nvidia",
                            "model": "fixture/a",
                        },
                    ],
                },

                "balanced": {
                    "preferredModels": [
                        {
                            "providerGroup": "nvidia",
                            "model": "fixture/b",
                        },
                    ],
                },

                "deep": {
                    "preferredModels": [
                        {
                            "providerGroup": "nvidia",
                            "model": "fixture/c",
                        },
                    ],
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
            "fixture/a",
        )

        assert_model(
            balanced,
            "fixture/b",
        )

        assert_model(
            deep,
            "fixture/c",
        )

        assert_model(
            default,
            "fixture/b",
        )

    print(
        "PASS Routing V1 FAST/BALANCED/DEEP selection"
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
                    # Deliberately absent preferred model. Fleet must still use
                    # another discovered/eligible model before OpenRouter.
                    #
                    "preferredModels": [
                        {
                            "providerGroup": "nvidia",
                            "model": "not-present",
                        },
                    ],
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

    configured = {
        (
            item.get("providerGroup"),
            item.get("model"),
        )
        for profile in profiles.values()
        for item in (
            profile.get("preferredModels")
            or []
        )
        if isinstance(item, dict)
    }

    expected = {
        (
            "nvidia",
            "nvidia/nemotron-3-super-120b-a12b",
        ),
        (
            "nvidia",
            "nvidia/nemotron-3-ultra-550b-a55b",
        ),
        (
            "nvidia",
            "thinkingmachines/inkling",
        ),
        (
            "nvidia",
            "deepseek-ai/deepseek-v4-flash-0731",
        ),
        (
            "zen",
            "mimo-v2.5-free",
        ),
        (
            "zen",
            "nemotron-3-ultra-free",
        ),
        (
            "zen",
            "nemotron-3.5-lightning-free",
        ),
    }

    require(
        expected.issubset(configured),
        "production Routing V1 lost benchmarked model routes",
    )

    print(
        "PASS production Routing V1 contract"
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
