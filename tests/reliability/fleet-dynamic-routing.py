#!/usr/bin/env python3
"""Deterministic dynamic Fleet routing: eligibility gates + evidence ranking.

Proves, with fixture model IDs only:
  * an unseen verified registry model becomes eligible automatically;
  * the highest evidence-ranked healthy candidate is selected first;
  * cooldown changes the winner;
  * verification expiry removes a candidate;
  * failure history changes ranking;
  * routing policy contains no exact production model IDs;
  * automatic ranking is deterministic;
  * openrouter/free stays the final escape route;
  * manual session pins are not overridden by automatic ranking.
"""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import sys
import tempfile
import time
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DISPATCHER = ROOT / "integrations/fleet/dispatcher.py"
POLICY = ROOT / "config/fleet/hermes-fleet-policy.json"
HERMES_ROOT = Path(
    os.environ.get("OPEN_CLOUD_HERMES_ROOT", Path.home() / ".hermes/hermes-agent")
)

TTL_MS = 86_400_000  # fleet_runtime DEFAULT_TTL_SECONDS
BASE_CLOCK_S = 1_000_000.0
BASE_NOW_MS = int(BASE_CLOCK_S * 1000)

ALPHA = "fixture/alpha"
BRAVO = "fixture/bravo"
CHARLIE = "fixture/charlie"


def require(cond, msg):
    if not cond:
        raise AssertionError(msg)


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def stub_hermes_registry_imports() -> None:
    hermes_cli = types.ModuleType("hermes_cli")
    model_switch = types.ModuleType("hermes_cli.model_switch")
    model_switch.list_provider_models = lambda _: {}
    runtime = types.ModuleType("hermes_cli.runtime_provider")
    runtime.resolve_runtime_provider = lambda *args, **kwargs: {
        "provider": kwargs.get("requested", ""),
        "base_url": "https://example.invalid/v1",
        "api_key": "test",
    }
    agent = types.ModuleType("agent")
    credentials = types.ModuleType("agent.credential_pool")
    credentials.load_pool = lambda: None
    metadata = types.ModuleType("agent.model_metadata")
    metadata.MINIMUM_CONTEXT_LENGTH = 64_000
    metadata.get_model_context_length = lambda model, **kwargs: 128_000
    sys.modules.update(
        {
            "hermes_cli": hermes_cli,
            "hermes_cli.model_switch": model_switch,
            "hermes_cli.runtime_provider": runtime,
            "agent": agent,
            "agent.credential_pool": credentials,
            "agent.model_metadata": metadata,
        }
    )


class FleetCase:
    """Isolated fleet root + health DB + deterministic clock."""

    def __enter__(self):
        self.temp = tempfile.TemporaryDirectory(
            prefix="opencloud-fleet-dynamic-"
        )
        root = Path(self.temp.name)

        self.home = root / "home"
        self.base = self.home / ".local" / "share" / "hermes-fleet"
        (self.base / "registry").mkdir(parents=True)

        (self.base / "fleet.json").write_text(
            POLICY.read_text(encoding="utf-8"), encoding="utf-8"
        )
        (self.base / "registry" / "models.json").write_text("{}\n")

        # The Hermes fleet bridge loads its own dispatcher copy from the
        # fleet root, so mirror the runtime layout for the pin test.
        shutil.copy2(DISPATCHER, self.base / "dispatcher.py")
        shutil.copy2(
            ROOT / "integrations/fleet/fleet_runtime.py",
            self.base / "fleet_runtime.py",
        )
        (self.base / "session-pin.key").write_bytes(b"x" * 32)

        self.health = root / "health.sqlite"

        os.environ["HOME"] = str(self.home)
        os.environ["OPEN_CLOUD_FLEET_HOME"] = str(self.base)
        os.environ["HERMES_FLEET_HEALTH_DB"] = str(self.health)
        os.environ["OPEN_CLOUD_MODEL_VERIFICATION_TTL_SECONDS"] = str(
            TTL_MS // 1000
        )

        spec = importlib.util.spec_from_file_location(
            f"opencloud_fleet_dynamic_{id(self)}", DISPATCHER
        )
        self.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.module)

        self.clock = [BASE_CLOCK_S]
        self.module.now = lambda: self.clock[0]

        self.fleet = self.module.HermesFleet()
        return self

    def __exit__(self, *exc):
        try:
            self.fleet.close()
        finally:
            self.temp.cleanup()

    def evidence_row(self, model: str, age_ms: int, latency_ms: int,
                     *, context_length=None, probe_failures=0,
                     provider="nvidia", group="nvidia",
                     verified_at_ms=None, last_probe_ms=None) -> dict:
        row = {
            "provider": provider,
            "providerGroup": group,
            "id": model,
            "verification": "verified",
            "verifiedAtMs": verified_at_ms if verified_at_ms is not None
            else BASE_NOW_MS - age_ms,
            "lastProbeLatencyMs": latency_ms,
        }
        if context_length is not None:
            row["contextLength"] = context_length
            row["lastProbeMs"] = (
                last_probe_ms if last_probe_ms is not None
                else BASE_NOW_MS - age_ms
            )
        if probe_failures:
            row["probeFailureCount"] = probe_failures
        return row

    def write_registry(self, registry: dict) -> None:
        (self.base / "registry" / "models.json").write_text(
            json.dumps(registry, indent=2, sort_keys=True) + "\n"
        )
        self.fleet.registry = json.loads(
            (self.base / "registry" / "models.json").read_text()
        )


# --- test definitions appended below ---


def test_unseen_verified_model_becomes_eligible() -> None:
    with FleetCase() as case:
        case.write_registry(
            {
                "productionModels": {"nvidia": [ALPHA]},
                "models": [case.evidence_row(ALPHA, 1_000, 5_000)],
            }
        )
        first = case.fleet.select("main", touch=False)
        require(first is not None and first["model"] == ALPHA,
                "verified registry model not selected")

        #
        # A brand-new verified model appears in the registry (discovery +
        # verification). No policy or config change: it must become
        # eligible and, with stronger evidence, win immediately.
        #
        rows = case.fleet.registry["models"]
        rows.append(case.evidence_row(BRAVO, 500, 1_000))
        case.fleet.registry["productionModels"]["nvidia"].append(BRAVO)

        second = case.fleet.select("main", touch=False)
        require(second is not None and second["model"] == BRAVO,
                "unseen verified model did not become eligible")

    print("PASS unseen verified registry model becomes eligible automatically")


def test_evidence_ranking_health_cooldown_and_determinism() -> None:
    with FleetCase() as case:
        case.write_registry(
            {
                "productionModels": {"nvidia": [ALPHA, BRAVO]},
                "models": [
                    case.evidence_row(ALPHA, 1_000, 5_000),
                    case.evidence_row(BRAVO, 1_000, 5_000),
                ],
            }
        )

        # Equal registry evidence: measured health history decides.
        alpha = next(c for c in case.fleet.candidates("main")
                     if c["model"] == ALPHA)
        case.fleet.success(alpha)
        winner = case.fleet.select("main", touch=False)
        require(winner["model"] == ALPHA,
                "healthy candidate did not outrank unproven candidate")

        # Deterministic: identical state selects identically.
        for _ in range(5):
            again = case.fleet.select("main", touch=False)
            require(again["model"] == ALPHA, "ranking is not deterministic")

        # Cooldown changes the winner without touching ranking inputs.
        case.fleet.failure(alpha, "model_unavailable")
        after = case.fleet.select("main", touch=False)
        require(after["model"] == BRAVO,
                "candidate cooldown did not change the winner")

    print("PASS evidence-ranked healthy candidate first, deterministic")
    print("PASS cooldown changes the winner")


def test_failure_history_changes_ranking() -> None:
    with FleetCase() as case:
        case.write_registry(
            {
                "productionModels": {"nvidia": [ALPHA, BRAVO]},
                "models": [
                    case.evidence_row(ALPHA, 1_000, 5_000),
                    case.evidence_row(BRAVO, 1_000, 5_000),
                ],
            }
        )

        candidates = {c["model"]: c for c in case.fleet.candidates("main")}

        # A measured success makes alpha the evidence leader.
        case.fleet.success(candidates[ALPHA])
        require(
            case.fleet.select("main", touch=False)["model"] == ALPHA,
            "success history did not lead the ranking",
        )

        # Repeated measured candidate-scoped failures flip the evidence
        # leader. The cooldown is advanced past so only the history
        # term differs (quota is candidate-scoped, never provider-wide).
        for _ in range(3):
            case.fleet.failure(candidates[ALPHA], "quota")
        case.clock[0] += 22_000.0  # quota cooldown is 6h

        require(
            case.fleet.select("main", touch=False)["model"] == BRAVO,
            "failure history did not change the ranking",
        )

    print("PASS failure history changes ranking")


# --- capability evidence tests ---


def test_deep_prefers_measured_capability() -> None:
    with FleetCase() as case:
        case.fleet.config["routingV1"]["roleProfiles"]["main"] = "deep"
        case.write_registry(
            {
                "productionModels": {"nvidia": [ALPHA, BRAVO]},
                "models": [
                    # Fast but capability-unknown (= neutral, 0.5).
                    case.evidence_row(ALPHA, 1_000, 100),
                    # Slower, but demonstrably more capable: measured
                    # 256k context with a clean verification probe.
                    case.evidence_row(
                        BRAVO, 1_000, 30_000,
                        context_length=262_144,
                    ),
                ],
            }
        )

        winner = case.fleet.select("main", touch=False)
        require(
            winner is not None and winner["model"] == BRAVO,
            "deep profile did not prefer measured capability over latency",
        )

        # The capability edge is real only when measured: prove the
        # evidence score is model-independent and driven by the row.
        cap_alpha = case.fleet._capability_evidence(
            case.fleet.registry["models"][0]
        )
        cap_bravo = case.fleet._capability_evidence(
            case.fleet.registry["models"][1]
        )
        require(cap_bravo > cap_alpha,
                "measured capability must exceed unknown-neutral")
        require(cap_alpha == 0.5,
                "unknown capability must be neutral")

    print("PASS deep profile prefers measured capability over raw latency")


def test_fast_prefers_health_and_latency() -> None:
    with FleetCase() as case:
        case.fleet.config["routingV1"]["roleProfiles"]["main"] = "fast"
        case.write_registry(
            {
                "productionModels": {"nvidia": [ALPHA, BRAVO]},
                "models": [
                    # Healthy low-latency candidate.
                    case.evidence_row(ALPHA, 1_000, 100),
                    # Capable but slow.
                    case.evidence_row(
                        BRAVO, 1_000, 30_000,
                        context_length=262_144,
                    ),
                ],
            }
        )

        winner = case.fleet.select("main", touch=False)
        require(
            winner is not None and winner["model"] == ALPHA,
            "fast profile did not prefer the healthy low-latency route",
        )

    print("PASS fast profile prefers health and latency")


# --- remaining capability tests appended below ---


def test_balanced_deterministic_and_unknown_capability_neutral() -> None:
    with FleetCase() as case:
        case.write_registry(
            {
                "productionModels": {"nvidia": [ALPHA, BRAVO]},
                "models": [
                    case.evidence_row(ALPHA, 1_000, 1_000),
                    case.evidence_row(BRAVO, 1_000, 1_000),
                ],
            }
        )

        # Equal evidence → balanced picks deterministically across many
        # passes, and the unknown-capability candidate is not favored.
        picks = set()
        for _ in range(10):
            picks.add(case.fleet.select("main", touch=False)["model"])
        require(len(picks) == 1, "balanced ranking is not deterministic")

        # Unknown capability (0.5) must never outrank measured-strong
        # capability when every other evidence term is equal.
        case.write_registry(
            {
                "productionModels": {"nvidia": [ALPHA, BRAVO]},
                "models": [
                    case.evidence_row(ALPHA, 1_000, 5_000),
                    case.evidence_row(
                        BRAVO, 1_000, 5_000,
                        context_length=262_144,
                    ),
                ],
            }
        )
        cap_alpha = case.fleet._capability_evidence(
            next(
                r for r in case.fleet.registry["models"]
                if r["id"] == ALPHA
            )
        )
        cap_bravo = case.fleet._capability_evidence(
            next(
                r for r in case.fleet.registry["models"]
                if r["id"] == BRAVO
            )
        )
        require(
            cap_alpha == 0.5 and cap_bravo > 0.5,
            "unknown capability must be neutral while measured is evidence",
        )

    print("PASS balanced ranking is deterministic")
    print("PASS unknown capability is neutral, never best")


def test_last_probe_ms_alone_is_not_capability_evidence() -> None:
    with FleetCase() as case:
        row = {
            "provider": "nvidia",
            "providerGroup": "nvidia",
            "id": ALPHA,
            "verification": "verified",
            "verifiedAtMs": BASE_NOW_MS - 1_000,
            "lastProbeMs": BASE_NOW_MS - 1_000,
            "lastProbeLatencyMs": 5_000,
        }
        require(
            "contextLength" not in row
            and "probeFailureCount" not in row,
            "fixture must omit capability/capacity measurements",
        )
        cap = case.fleet._capability_evidence(row)
        require(
            cap == 0.5,
            "lastProbeMs alone must leave capability neutral at 0.5",
        )

    print("PASS lastProbeMs alone leaves capability neutral at 0.5")


def test_ranking_contains_no_model_identity() -> None:
    with FleetCase() as case:
        case.write_registry(
            {
                "productionModels": {"nvidia": [ALPHA, BRAVO]},
                "models": [
                    case.evidence_row(ALPHA, 1_000, 5_000),
                    case.evidence_row(BRAVO, 1_000, 5_000),
                ],
            }
        )

        candidates = {
            c["model"]: c for c in case.fleet.candidates("main")
        }
        weights = case.fleet._routing_weights("balanced")

        # Feed the two different candidates the IDENTICAL registry evidence.
        # The evidence row carries no capability/health/freshness/latency
        # distinction, so the score must be identical: ranking never reads
        # the model ID or provider name.
        evidence = case.fleet._candidate_eligibility(
            candidates[ALPHA]
        )[2]
        score_alpha = case.fleet._evidence_penalty(
            candidates[ALPHA], evidence, weights
        )
        score_bravo = case.fleet._evidence_penalty(
            candidates[BRAVO], evidence, weights
        )
        require(
            score_alpha == score_bravo,
            "evidence score depends on model identity",
        )

        key_alpha = case.fleet._routing_sort_key(
            candidates[ALPHA], "balanced"
        )
        key_bravo = case.fleet._routing_sort_key(
            candidates[BRAVO], "balanced"
        )
        require(
            key_alpha[:4] == key_bravo[:4],
            "ranking order depends on model identity",
        )
        require(
            key_alpha[4] != key_bravo[4],
            "deterministic tie-break missing",
        )

        # The routing policy itself carries generic weights only
        # (verified by test_policy_has_no_concrete_models); double check
        # the dispatcher source contains no per-model preference lookup.
        require(
            "preferredModels" not in DISPATCHER.read_text(),
            "dispatcher reads an exact-model preference list",
        )

    print("PASS ranking contains no model identity (IDs only tie-break)")


# --- more tests appended below ---


def test_verification_expiry_removes_candidate() -> None:
    with FleetCase() as case:
        case.write_registry(
            {
                "productionModels": {"nvidia": [ALPHA, BRAVO]},
                "models": [
                    # Fresh but slow measured probe.
                    case.evidence_row(ALPHA, 1_000, 25_000),
                    # Fast probe, verified halfway through its TTL.
                    case.evidence_row(BRAVO, TTL_MS // 2, 100),
                ],
            }
        )

        first = case.fleet.select("main", touch=False)
        require(first is not None and first["model"] == BRAVO,
                "fresh fast candidate did not lead while eligible")

        # Advance the clock past bravo's verification TTL.
        case.clock[0] += TTL_MS // 2000 + 120

        second = case.fleet.select("main", touch=False)
        require(
            second is not None and second["model"] == ALPHA,
            "verification-expired candidate was still selected",
        )

        by_model = {c["model"]: c for c in case.fleet.candidates("main")}
        eligible, reason, _ = case.fleet._candidate_eligibility(
            by_model[BRAVO]
        )
        require(
            not eligible and reason == "verification_expired",
            f"unexpected eligibility state: {eligible}/{reason}",
        )
        eligible_alpha, reason_alpha, _ = case.fleet._candidate_eligibility(
            by_model[ALPHA]
        )
        require(
            eligible_alpha and reason_alpha == "verified",
            "fresh candidate incorrectly removed",
        )

    print("PASS verification expiry removes a candidate at select time")


def test_malformed_verification_timestamp_rejects_only_bad_row() -> None:
    with FleetCase() as case:
        bad = case.evidence_row(ALPHA, 1_000, 1_000)
        bad["verifiedAtMs"] = "not-a-timestamp"
        good = case.evidence_row(BRAVO, 1_000, 2_000)
        case.write_registry({
            "productionModels": {"nvidia": [ALPHA, BRAVO]},
            "models": [bad, good],
        })
        selected = case.fleet.select("main", touch=False)
        require(
            selected is not None and selected["model"] == BRAVO,
            "malformed timestamp must reject only its candidate",
        )

    print("PASS malformed verification timestamp rejects only bad candidate")


def test_touched_candidate_gets_no_ranking_advantage() -> None:
    with FleetCase() as case:
        case.write_registry(
            {
                "productionModels": {"nvidia": [ALPHA, BRAVO]},
                "models": [
                    case.evidence_row(ALPHA, 1_000, 10_000),
                    case.evidence_row(BRAVO, 1_000, 10_000),
                ],
            }
        )

        #
        # select(touch=True) records a zero-success / zero-failure
        # health row for the winner (alpha, by deterministic
        # tie-break). That row must stay health-neutral.
        #
        touched = case.fleet.select("main", touch=True)
        require(touched is not None and touched["model"] == ALPHA,
                "tie-break changed")

        # Give bravo strictly better measured probe evidence.
        for row in case.fleet.registry["models"]:
            if row["id"] == BRAVO:
                row["lastProbeLatencyMs"] = 5_000
        case.write_registry(case.fleet.registry)

        winner = case.fleet.select("main", touch=False)
        require(
            winner["model"] == BRAVO,
            "merely touching a candidate created a ranking advantage",
        )

    print("PASS zero-history touch is health-neutral for ranking")


def test_ttl_zero_rejects_cached_verification() -> None:
    with FleetCase() as case:
        case.fleet.config["roles"]["main"] = [
            "nvidia-dynamic",
            "openrouter-free",
        ]
        case.write_registry(
            {
                "productionModels": {"nvidia": [ALPHA, BRAVO]},
                "models": [
                    case.evidence_row(ALPHA, 1_000, 1_000),
                    case.evidence_row(BRAVO, 1_000, 1_000),
                ],
            }
        )

        # TTL 0 = "no freshness cache": cached verification evidence is
        # deterministically rejected; selection must fall through to the
        # safe final route without any traceback.
        os.environ["OPEN_CLOUD_MODEL_VERIFICATION_TTL_SECONDS"] = "0"
        try:
            for _ in range(3):
                picked = case.fleet.select("main", touch=False)
                require(
                    picked is not None
                    and picked["model"] == "openrouter/free",
                    "TTL 0 did not reject cached verification evidence",
                )

            by_model = {
                c["model"]: c for c in case.fleet.candidates("main")
            }
            eligible, reason, _ = case.fleet._candidate_eligibility(
                by_model[ALPHA]
            )
            require(
                not eligible and reason == "verification_expired",
                f"unexpected TTL-0 eligibility state: {eligible}/{reason}",
            )
        finally:
            os.environ["OPEN_CLOUD_MODEL_VERIFICATION_TTL_SECONDS"] = str(
                TTL_MS // 1000
            )

    print("PASS TTL 0 deterministically rejects cached verification evidence")


# --- remaining repair tests appended below ---


def test_legacy_snapshot_without_fresh_evidence_rejected() -> None:
    with FleetCase() as case:
        case.fleet.config["roles"]["main"] = [
            "nvidia-dynamic",
            "openrouter-free",
        ]

        # Legacy snapshot-only registry: membership without detailed
        # verification evidence must NOT satisfy the verified gate.
        case.write_registry(
            {
                "productionModels": {"nvidia": [ALPHA, BRAVO]},
                "models": [],
            }
        )
        picked = case.fleet.select("main", touch=False)
        require(
            picked is not None and picked["model"] == "openrouter/free",
            "legacy productionModels membership bypassed the verified gate",
        )

        # A detailed row whose verification has expired is equally
        # rejected; only fresh, per-model evidence qualifies.
        case.write_registry(
            {
                "productionModels": {"nvidia": [ALPHA, BRAVO]},
                "models": [
                    case.evidence_row(ALPHA, TTL_MS + 1_000, 1_000),
                ],
            }
        )
        picked = case.fleet.select("main", touch=False)
        require(
            picked is not None and picked["model"] == "openrouter/free",
            "stale verification satisfied the verified gate",
        )

        by_model = {c["model"]: c for c in case.fleet.candidates("main")}
        eligible_missing, reason_missing, _ = case.fleet._candidate_eligibility(
            by_model[BRAVO]
        )
        require(
            not eligible_missing
            and reason_missing == "no_fresh_verification_evidence",
            f"unexpected missing-evidence state: {reason_missing}",
        )
        eligible_stale, reason_stale, _ = case.fleet._candidate_eligibility(
            by_model[ALPHA]
        )
        require(
            not eligible_stale and reason_stale == "verification_expired",
            f"unexpected stale-evidence state: {reason_stale}",
        )

    print("PASS legacy production snapshot without fresh verification rejected")


def test_stale_discovery_strictly_excluded() -> None:
    with FleetCase() as case:
        case.fleet.config["roles"]["main"] = [
            "nvidia-dynamic",
            "openrouter-free",
        ]
        case.write_registry(
            {
                "productionModels": {"nvidia": [ALPHA, BRAVO]},
                "models": [
                    dict(
                        case.evidence_row(ALPHA, 1_000, 1_000),
                        discoveryStale=True,
                    ),
                    case.evidence_row(BRAVO, 1_000, 1_000),
                ],
            }
        )

        picked = case.fleet.select("main", touch=False)
        require(
            picked is not None and picked["model"] == BRAVO,
            "stale-discovery row outranked a currently confirmed model",
        )

        by_model = {c["model"]: c for c in case.fleet.candidates("main")}
        eligible, reason, _ = case.fleet._candidate_eligibility(
            by_model[ALPHA]
        )
        require(
            not eligible and reason == "discovery_stale",
            f"unexpected stale-discovery state: {eligible}/{reason}",
        )

        # When every registry row is unconfirmed by the current cycle,
        # selection must fall through to the safe final escape route.
        for row in case.fleet.registry["models"]:
            row["discoveryStale"] = True
        case.write_registry(case.fleet.registry)

        final = case.fleet.select("main", touch=False)
        require(
            final is not None and final["model"] == "openrouter/free",
            "unconfirmed catalog did not fall through to the escape route",
        )

    print("PASS stale discovery is strictly excluded (no grace period)")


def test_openrouter_free_is_final_escape() -> None:
    with FleetCase() as case:
        case.write_registry(
            {
                "productionModels": {"nvidia": [ALPHA, BRAVO, CHARLIE]},
                "models": [
                    case.evidence_row(m, 1_000, 1_000)
                    for m in (ALPHA, BRAVO, CHARLIE)
                ],
            }
        )
        case.fleet.config["roles"]["main"] = [
            "nvidia-dynamic",
            "openrouter-free",
        ]

        for _ in range(6):
            picked = case.fleet.select("main", touch=False)
            require(picked is not None, "no eligible candidate")
            require(picked["model"] != "openrouter/free",
                    "escape route used before verified capacity")

        by_model = {c["model"]: c for c in case.fleet.candidates("main")}
        for model in (ALPHA, BRAVO, CHARLIE):
            case.fleet.failure(by_model[model], "model_unavailable")

        final = case.fleet.select("main", touch=False)
        require(final is not None and final["model"] == "openrouter/free",
                "openrouter/free did not remain the final escape")

    print("PASS openrouter/free stays the final escape route")


def test_policy_has_no_concrete_models() -> None:
    policy = json.loads(POLICY.read_text())
    routing = policy.get("routingV1") or {}

    require(routing.get("enabled") is True, "routingV1 disabled")
    require(
        routing.get("finalEscape", {}).get("model") == "openrouter/free",
        "final escape changed",
    )

    profiles = routing.get("profiles") or {}
    require(set(profiles) == {"fast", "balanced", "deep"},
            "profiles changed")

    def model_keys(value):
        found = []
        if isinstance(value, dict):
            for key, item in value.items():
                if key in ("model", "preferredModels"):
                    found.append(key)
                found.extend(model_keys(item))
        elif isinstance(value, list):
            for item in value:
                found.extend(model_keys(item))
        return found

    require(model_keys(profiles) == [],
            "profiles contain model keys or exact preference lists")

    pools = policy.get("pools") or {}
    require(pools.get("openrouter-free-dynamic", {}).get("freeOnly") is True,
            "dynamic OpenRouter pool must be free-only")
    require(pools.get("gemini-emergency", {}).get("type") == "registry",
            "Gemini must use dynamic registry verification")
    require(float(pools.get("gemini-emergency", {}).get("automaticPenalty", 0)) > 0,
            "Gemini automatic quota-conservation penalty missing")

    for profile in profiles.values():
        weights = profile.get("weights") or {}
        for key in ("capability", "health", "freshness", "latency"):
            require(isinstance(weights.get(key), (int, float)),
                    f"missing generic weight {key}")

    text = POLICY.read_text().lower()
    for term in (ALPHA, BRAVO, CHARLIE, "nemotron", "muse-", "deepseek"):
        require(term.lower() not in text,
                f"policy contains concrete model term {term}")

    print("PASS routing policy carries generic weights only, no model IDs")


def test_gemini_automatic_conservation_penalty() -> None:
    with FleetCase() as case:
        case.write_registry(
            {
                "productionModels": {"nvidia": [ALPHA], "gemini": [BRAVO]},
                "models": [
                    case.evidence_row(ALPHA, 1_000, 1_000),
                    case.evidence_row(
                        BRAVO, 1_000, 1_000, provider="gemini", group="gemini"
                    ),
                ],
            }
        )
        selected = case.fleet.select("main", touch=False)
        require(selected is not None and selected["model"] == ALPHA,
                "automatic routing did not conserve equal-evidence Gemini capacity")

    print("PASS Gemini automatic quota conservation is policy-level, not model-hardcoded")


def test_sqlite_query_count_is_constant() -> None:
    """The audit measured ~4,003 SELECTs for 1,000 candidates.

    Selection now bulk-loads candidate and provider health once per pass
    and reuses the in-memory indexes through filtering and ranking, so the
    number of SELECT statements is a small fixed number that does NOT scale
    per candidate. Proof: 100 vs 1,000 candidates → identical SELECT count.
    """

    def measure(case, count):
        models = [
            case.evidence_row(f"fixture/model{i:04d}", 1_000, 5_000)
            for i in range(count)
        ]
        case.write_registry(
            {
                "productionModels": {
                    "nvidia": [r["id"] for r in models]
                },
                "models": models,
            }
        )
        case.fleet.db.executemany(
            """
            INSERT OR IGNORE INTO candidate_health (
                candidate_key, provider_group, provider, model,
                successes, failures, last_used_at
            )
            VALUES (?, ?, ?, ?, ?, ?, 0)
            """,
            [
                (
                    f"nvidia:nvidia:fixture/model{i:04d}",
                    "nvidia",
                    "nvidia",
                    f"fixture/model{i:04d}",
                    i % 5,
                    i % 3,
                )
                for i in range(count)
            ],
        )
        case.fleet.db.commit()

        counters = {"select": 0}
        case.fleet.db.set_trace_callback(
            lambda sql: counters.__setitem__(
                "select",
                counters["select"] + (
                    1
                    if str(sql).lstrip().upper().startswith("SELECT")
                    else 0
                ),
            )
        )
        try:
            picked = case.fleet.select("main", touch=False)
        finally:
            case.fleet.db.set_trace_callback(None)

        require(
            picked is not None and picked["model"] == "fixture/model0003",
            "constant-count selection did not pick the evidence leader "
            "(best measured health, deterministic tie-break)",
        )
        return counters["select"]

    with FleetCase() as small:
        small_count = measure(small, 100)

    with FleetCase() as large:
        large_count = measure(large, 1000)

    require(
        small_count == large_count,
        f"SELECT count scales with candidate count "
        f"({small_count} vs {large_count})",
    )
    require(
        large_count <= 8,
        f"SELECT count not small and fixed: {large_count} for 1,000 "
        "candidates",
    )

    print(
        f"PASS SQLite query count constant: {large_count} SELECTs "
        "regardless of candidate count"
    )


# --- synthetic provider test appended below ---


def test_synthetic_third_provider_config_only() -> None:
    """A synthetic third provider added purely through configuration."""

    with FleetCase() as case:

        # 1) Enable a third provider through configuration ONLY: a new
        #    registry pool in fleet.json. The routing/selection code is
        #    generic and knows nothing about "acme".
        cfg = case.fleet.config
        cfg["pools"]["acme-dynamic"] = {
            "type": "registry",
            "providerGroup": "acme",
            "provider": "acme",
            "discoveryAliases": ["acme"],
            "freeOnly": False,
        }
        cfg["roles"]["main"].append("acme-dynamic")
        (case.base / "fleet.json").write_text(json.dumps(cfg))
        cfg = json.loads((case.base / "fleet.json").read_text())

        require(
            "acme" not in DISPATCHER.read_text().lower(),
            "synthetic provider leaked into routing code",
        )

        ACME = "acme/greatest"

        stub_hermes_registry_imports()

        # 2) DISCOVERY — refresh.py derives enabled providers from the
        #    configured policy and discovers the acme catalog through its
        #    declared discoveryAliases.
        refresh_path = ROOT / "integrations/fleet/registry/refresh.py"
        refresh_spec = importlib.util.spec_from_file_location(
            "opencloud_refresh_acme", refresh_path
        )
        refresh = importlib.util.module_from_spec(refresh_spec)
        refresh_spec.loader.exec_module(refresh)

        refresh.discover_opencode_models = lambda: None
        refresh.list_provider_models = lambda identity: (
            {ACME: {"name": "Acme Greatest"}}
            if identity == "acme"
            else {}
        )
        refresh.main()

        refreshed = json.loads(
            (case.base / "registry" / "models.json").read_text()
        )
        acme_rows = [
            r for r in refreshed["models"]
            if r["providerGroup"] == "acme"
        ]
        require(
            acme_rows
            and all(r["provider"] == "acme" for r in acme_rows),
            "acme discovery did not land in the registry",
        )
        require(
            "acme" in refreshed.get("productionModels", {}),
            "acme group missing from aggregation",
        )

        # 3) VERIFICATION — the verifier aggregates by configured group and
        #    verifies the acme row with the stub probe + recorded evidence.
        verify_path = ROOT / "integrations/fleet/registry/verify.py"
        verify_spec = importlib.util.spec_from_file_location(
            "opencloud_verify_acme", verify_path
        )
        verify = importlib.util.module_from_spec(verify_spec)
        verify_spec.loader.exec_module(verify)

        verify.probe = lambda provider, model: (
            True, "verified", False, "http://acme.example/chat/completions"
        )
        verify.hermes_context_compatible = (
            lambda provider, model, runtime=None: (True, 262_144)
        )
        verify._LAST_PROBE_EVIDENCE["latencyMs"] = 250
        verify._LAST_PROBE_EVIDENCE["contextLength"] = 262_144
        verify.main()

        verified_registry = json.loads(
            (case.base / "registry" / "models.json").read_text()
        )
        acme_row = next(
            r for r in verified_registry["models"] if r["id"] == ACME
        )
        require(
            acme_row.get("verification") == "verified"
            and acme_row.get("productionEligible") is True,
            "acme model did not verify",
        )
        require(
            ACME in verified_registry.get("productionModels", {}).get(
                "acme", []
            ),
            "verified acme model missing from production capacity",
        )

        # 4) RANKING + SELECTION — the dispatcher selects the acme route.
        case.fleet.registry = verified_registry
        require(
            any(
                c["providerGroup"] == "acme"
                for c in case.fleet.candidates("main")
            ),
            "acme candidate absent from selection",
        )
        picked = case.fleet.select("main", touch=False)
        require(
            picked is not None
            and picked["model"] == ACME
            and picked["provider"] == "acme",
            "acme route not selected end-to-end",
        )

    print("PASS synthetic third provider runs discovery→verify→selection "
          "via configuration only")


# --- pin test appended below ---


def load_bridge_module():
    bridge_path = HERMES_ROOT / "agent" / "hermes_fleet_bridge.py"
    if bridge_path.is_file():
        return load("opencloud_dynamic_pin_bridge", bridge_path)

    patch_path = ROOT / "integrations/hermes/hermes-fleet-bridge.patch"
    lines = patch_path.read_text(encoding="utf-8").splitlines()
    start = next(i for i, x in enumerate(lines) if x.startswith("@@")) + 1
    source = "\n".join(
        x[1:]
        for x in lines[start:]
        if x.startswith("+") and not x.startswith("+++")
    ) + "\n"
    with tempfile.TemporaryDirectory(prefix="opencloud-fleet-bridge-") as tmp:
        path = Path(tmp) / "hermes_fleet_bridge.py"
        path.write_text(source, encoding="utf-8")
        return load("opencloud_dynamic_pin_bridge_patch", path)


def test_manual_pins_not_overridden_by_ranking() -> None:
    # The bridge loads its own fresh dispatcher copy from the fleet root
    # whose clock is the REAL one. Verification evidence must therefore be
    # anchored near the real wall clock so the bridge's freshness gate
    # sees it as fresh (the mocked BASE_NOW_MS lives in the past).
    real_now_ms = int(time.time() * 1000)

    with FleetCase() as case:
        try:
            bridge = load_bridge_module()
        except Exception as exc:
            raise AssertionError(
                f"manual pin test could not load Hermes fleet bridge: {exc}"
            ) from exc

        case.write_registry(
            {
                "productionModels": {"nvidia": [ALPHA, BRAVO]},
                "models": [
                    case.evidence_row(
                        ALPHA, 1_000, 1_000,
                        verified_at_ms=real_now_ms - 10_000,
                    ),
                    case.evidence_row(
                        BRAVO, 1_000, 30_000,
                        verified_at_ms=real_now_ms - 10_000,
                    ),
                ],
            }
        )

        session = "fixture-pin-session"
        bridge._runtime = lambda candidate: {
            "provider": candidate["provider"],
            "requested_provider": candidate["provider"],
        }

        # (1) A NEW session receives the highest evidence-ranked route.
        initial = bridge.resolve_role("main", session_key=session)
        require(initial["candidate"]["model"] == ALPHA,
                "pin test: strongest evidence not selected for a new session")

        # (2) Texting an exact model + provider creates a session pin:
        #     route to BRAVO even though ALPHA leads the evidence ranking.
        exact = next(
            c for c in case.fleet.candidates("main")
            if c["model"] == BRAVO
        )
        bridge._set_pin(
            case.fleet, "main", session, exact, profile="manual"
        )
        require(
            bridge.session_is_pinned(session),
            "texting an exact model did not create a session pin",
        )
        pinned_now = bridge.resolve_role("main", session_key=session, profile="fast")
        require(
            pinned_now["candidate"]["model"] == BRAVO
            and pinned_now["pinned"] is True,
            "session pin was not honored",
        )

        # (3) Later ranking changes must NOT override the manual pin:
        #     make ALPHA the clear evidence leader on every measured
        #     dimension while the user's pinned BRAVO gets worse.
        rows = case.fleet.registry["models"]
        for row in rows:
            if row["id"] == ALPHA:
                row["lastProbeLatencyMs"] = 50
                row["contextLength"] = 262_144
                row["lastProbeMs"] = real_now_ms
            if row["id"] == BRAVO:
                row["lastProbeLatencyMs"] = 60_000
                row["probeFailureCount"] = 4
        case.write_registry(case.fleet.registry)

        still_pinned = bridge.resolve_role("main", session_key=session, profile="deep")
        require(
            still_pinned["candidate"]["model"] == BRAVO
            and still_pinned["pinned"] is True,
            "automatic ranking overrode a manual session pin",
        )

        # (4) Clearing the pin restores automatic routing to the new
        #     evidence leader.
        bridge.clear_session_pin(session)
        require(
            not bridge.session_is_pinned(session),
            "cleared pin still reported as pinned",
        )
        restored = bridge.resolve_role("main", session_key=session)
        require(
            restored["candidate"]["model"] == ALPHA,
            "clearing the pin did not restore automatic routing",
        )

        # Only a measured failure on a pinned route still releases it.
        agent = type("Agent", (), {
            "_hermes_fleet_role": "main",
            "_hermes_fleet_session_key": session,
            "provider": restored["candidate"]["provider"],
            "model": restored["candidate"]["model"],
        })()
        bridge.note_agent_failure(agent, TimeoutError("fixture timeout"))
        replaced = bridge.resolve_role("main", session_key=session)
        require(
            replaced["candidate"]["model"] == BRAVO,
            "released pin did not fall through to the evidence leader",
        )

    print("PASS manual session pins: new-session ranking, text pin, "
          "no ranking override, clear restores routing")


def main() -> None:
    test_policy_has_no_concrete_models()
    test_gemini_automatic_conservation_penalty()
    test_unseen_verified_model_becomes_eligible()
    test_touched_candidate_gets_no_ranking_advantage()
    test_evidence_ranking_health_cooldown_and_determinism()
    test_failure_history_changes_ranking()
    test_deep_prefers_measured_capability()
    test_fast_prefers_health_and_latency()
    test_balanced_deterministic_and_unknown_capability_neutral()
    test_last_probe_ms_alone_is_not_capability_evidence()
    test_ranking_contains_no_model_identity()
    test_verification_expiry_removes_candidate()
    test_malformed_verification_timestamp_rejects_only_bad_row()
    test_ttl_zero_rejects_cached_verification()
    test_legacy_snapshot_without_fresh_evidence_rejected()
    test_stale_discovery_strictly_excluded()
    test_openrouter_free_is_final_escape()
    test_sqlite_query_count_is_constant()
    test_synthetic_third_provider_config_only()
    test_manual_pins_not_overridden_by_ranking()

    print("FLEET_DYNAMIC_ROUTING: PASS")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"FAIL {exc}")
        raise SystemExit(1)
