#!/usr/bin/env python3
from __future__ import annotations

import ast
import importlib
import importlib.util
import os
import subprocess
import tempfile
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[2]


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    require(spec is not None and spec.loader is not None, f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FakeFleet:
    def __init__(self) -> None:
        self.failures: list[tuple[str, str]] = []
        self.successes: list[str] = []
        self.closed = False

    def failure(self, candidate: dict, kind: str) -> None:
        self.failures.append((candidate["candidateKey"], kind))

    def success(self, candidate: dict) -> None:
        self.successes.append(candidate["candidateKey"])

    def close(self) -> None:
        self.closed = True

    @staticmethod
    def routing_profile(role: str, requested: str | None = None) -> str:
        return requested or "balanced"

    @staticmethod
    def _routing_sort_key(candidate: dict, profile: str) -> tuple[int, str]:
        return candidate.get("rank", 99), candidate["candidateKey"]

    @staticmethod
    def _routing_is_final_escape(candidate: dict) -> bool:
        return (
            candidate.get("provider") == "openrouter"
            and candidate.get("model") == "openrouter/free"
        )


def candidate(group: str, provider: str, model: str, rank: int) -> dict:
    return {
        "candidateKey": f"{group}:{provider}:{model}",
        "providerGroup": group,
        "provider": provider,
        "model": model,
        "rank": rank,
    }


def isolated_function(path: Path, name: str, globals_: dict):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    node = next(item for item in tree.body if isinstance(item, ast.FunctionDef) and item.name == name)
    namespace = dict(globals_)
    exec(compile(ast.Module(body=[node], type_ignores=[]), str(path), "exec"), namespace)
    return namespace[name]


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="opencloud-provider-survival-") as tmp:
        selected_tree = os.environ.get("OPEN_CLOUD_HERMES_ROOT")
        tree = Path(selected_tree) if selected_tree else Path(tmp) / "hermes"
        if not selected_tree:
            result = subprocess.run(
                ["bash", str(ROOT / "install/30-brain-materialize.sh"), "--stage", str(tree)],
                cwd=ROOT,
                text=True,
                capture_output=True,
                check=False,
            )
            require(result.returncode == 0, result.stderr or result.stdout)

        bridge = load("opencloud_provider_survival_bridge", tree / "agent/hermes_fleet_bridge.py")

        # Selection must apply the same context floor as AIAgent construction,
        # before a candidate can be pinned to an interactive session.
        runtime_provider = importlib.import_module("hermes_cli.runtime_provider")
        model_metadata = importlib.import_module("agent.model_metadata")
        context_candidate = candidate("nvidia", "nvidia", "nvidia/too-small", 0)
        with (
            patch.object(runtime_provider, "resolve_runtime_provider", return_value={
                "provider": "nvidia",
                "base_url": "https://example.invalid/v1",
                "api_key": "test",
            }),
            patch.object(model_metadata, "get_model_context_length", return_value=16_000),
        ):
            try:
                bridge._runtime(context_candidate)
            except bridge.FleetBridgeError:
                pass
            else:
                raise AssertionError("sub-64K model passed Fleet runtime eligibility")
        with (
            patch.object(runtime_provider, "resolve_runtime_provider", return_value={
                "provider": "nvidia",
                "base_url": "https://example.invalid/v1",
                "api_key": "test",
            }),
            patch.object(model_metadata, "get_model_context_length", return_value=128_000),
        ):
            require(
                bridge._runtime(context_candidate)["provider"] == "nvidia",
                "Hermes-compatible candidate was rejected",
            )

        require(bridge.MAX_PROVIDER_ATTEMPTS_PER_REQUEST == 6, "request attempt bound drifted")
        require(bridge.MAX_ATTEMPTS_PER_PROVIDER == 2, "provider attempt bound drifted")
        require(bridge.MAX_RATE_LIMIT_RETRIES == 0, "rate-limit retry bound drifted")
        require(bridge.MAX_FAILOVER_TIME_SECONDS == 180, "failover deadline drifted")

        require(bridge._failure_kind("HTTP 429 rate limit") == "rate_limit", "429 misclassified")
        require(bridge._failure_kind("model free usage limit") == "quota", "model quota misclassified")
        require(
            bridge._failure_kind("limit_source=openrouter_free_tier_daily free-models-per-day")
            == "account_quota",
            "OpenRouter daily account quota misclassified",
        )
        require(bridge._failure_kind("HTTP 401 unauthorized") == "auth", "auth misclassified")
        require(bridge._failure_kind("network DNS failure") == "network", "network misclassified")
        require(bridge._failure_kind("request timeout") == "timeout", "timeout misclassified")
        require(
            bridge._failure_kind("This model only supports single tool-calls at once!")
            == "model_unavailable",
            "tool-call compatibility failure misclassified",
        )

        parallel_calls = [
            {"id": "call-search", "function": {"name": "web_search", "arguments": "{}"}},
            {"id": "call-extract", "function": {"name": "web_extract", "arguments": "{}"}},
        ]
        history = [
            {"role": "system", "content": "system"},
            {"role": "assistant", "content": "Gathering evidence.", "tool_calls": parallel_calls},
            {"role": "tool", "tool_call_id": "call-extract", "content": "extract evidence"},
            {"role": "tool", "tool_call_id": "call-search", "content": "search evidence"},
        ]
        limited = SimpleNamespace(_hermes_single_tool_call_history=True)
        normalized = bridge.normalize_tool_history(limited, history)
        require(history[1]["tool_calls"] == parallel_calls, "stored tool history was mutated")
        require(
            all(len(message.get("tool_calls") or []) <= 1 for message in normalized),
            "limited fallback still received parallel tool history",
        )
        require(
            [message["content"] for message in normalized if message.get("role") == "tool"]
            == ["search evidence", "extract evidence"],
            "tool evidence was lost or mismatched while serializing history",
        )
        require("one tool call" in normalized[0]["content"], "single-tool guidance missing")
        require(
            bridge.normalize_tool_history(
                SimpleNamespace(), history
            )
            is history,
            "compatible model history was rewritten",
        )

        unmanaged = SimpleNamespace(provider="openrouter", model="openrouter/free")
        require(
            bridge.note_api_failure(unmanaged, RuntimeError("HTTP 429 rate limit")),
            "Routing V1 policy leaked into an unmanaged Hermes session",
        )

        fleet = FakeFleet()
        active = candidate("openrouter", "openrouter", "openrouter/free", 99)
        bridge._fleet = lambda: fleet
        bridge._find = lambda _fleet, _role, provider, model: active
        agent = SimpleNamespace(
            _hermes_fleet_role="main",
            _hermes_fleet_session_key=None,
            provider="openrouter",
            model="openrouter/free",
        )
        retry_allowed = bridge.note_api_failure(
            agent,
            RuntimeError("HTTP 429: free-models-per-day; openrouter_free_tier_daily"),
        )
        require(not retry_allowed, "account quota allowed same-candidate retry")
        require(fleet.failures == [(active["candidateKey"], "account_quota")], "failure not recorded once")
        bridge.note_agent_failure(agent, "rate_limit")
        require(len(fleet.failures) == 1, "fallback enum downgraded duplicate failure evidence")
        agent._hermes_fleet_failover_started_at = 1.0
        bridge.note_agent_success(agent)
        require(fleet.successes == [active["candidateKey"]], "recovered route success was not recorded")
        require(agent._hermes_fleet_last_failure_route is None, "success did not reset failure dedupe")
        require(agent._hermes_fleet_failover_started_at is None, "success did not reset failover deadline")
        bridge.note_agent_failure(agent, "rate_limit")
        require(len(fleet.failures) == 2, "later failure on a recovered route was suppressed")

        primary = candidate("nvidia", "nvidia", "nvidia/a", 0)
        available = [
            primary,
            candidate("nvidia", "nvidia", "nvidia/b", 1),
            candidate("nvidia", "nvidia", "nvidia/c", 2),
            candidate("zen", "opencode-zen", "zen/a", 3),
            candidate("zen", "opencode-zen", "zen/b", 4),
            candidate("other", "other", "other/a", 5),
            active,
        ]
        bridge._available = lambda _fleet, _role: list(available)
        chain = bridge._chain(FakeFleet(), "main", primary, profile="balanced")
        require(len(chain) + 1 <= bridge.MAX_PROVIDER_ATTEMPTS_PER_REQUEST, "request bound exceeded")
        require(chain[-1]["model"] == "openrouter/free", "exact final escape was not last")
        require(sum(item["_hermes_fleet_provider_group"] == "nvidia" for item in chain) == 1,
                "per-provider bound exceeded")
        worker_chain = bridge._chain(FakeFleet(), "worker", primary, profile="balanced")
        require(len(worker_chain) == 1, "worker gained more than one fallback")
        require(worker_chain[0]["provider"] != "nvidia", "worker fallback was not cross-provider")

        loop_source = (tree / "agent/conversation_loop.py").read_text(encoding="utf-8")
        start = loop_source.index("# HERMES_PROVIDER_SURVIVAL_V1")
        end = loop_source.index("# ── Auth-failure provider failover", start)
        failover_segment = loop_source[start:end]
        require("note_api_failure" in failover_segment, "real API error is not reported before fallback")
        require("_sync_failover_system_message" in failover_segment, "fallback does not continue same API state")
        require("messages = []" not in failover_segment, "fallback discards gathered tool evidence")
        require("api_messages = []" not in failover_segment, "fallback discards API evidence")
        require("note_agent_success(agent)" in loop_source, "successful inference does not reset failover state")
        require("normalize_tool_history(agent, api_messages)" in loop_source,
                "main request path does not normalize limited fallback history")
        require("only supports single tool-call" in loop_source,
                "single-tool provider rejection does not activate compatibility retry")
        require(
            "normalize_tool_history(agent, api_messages)"
            in (tree / "agent/chat_completion_helpers.py").read_text(encoding="utf-8"),
            "summary request path does not normalize limited fallback history",
        )

        summarize = isolated_function(
            tree / "cron/scheduler.py",
            "_summarize_cron_failure_for_delivery",
            {
                "re": __import__("re"),
                "_hermes_now": lambda: datetime(2026, 8, 20, 9, 30),
            },
        )
        report = summarize(
            {"name": "Daily Career Job Match Report", "output_schema": "career_job_match_v1"},
            "RuntimeError: HTTP 429 free-models-per-day",
        )
        require(report.count("STATUS:") == 1, "total failure produced duplicate status")
        require("STATUS: Temporarily unavailable" in report, "infrastructure state missing")
        require("VERIFIED MATCHES: 0" not in report, "provider failure rendered fake zero matches")
        require("free-models-per-day" not in report, "raw provider detail leaked to delivery")

        compatibility_report = summarize(
            {"name": "Daily Career Job Match Report", "output_schema": "career_job_match_v1"},
            "RuntimeError: HTTP 400: This model only supports single tool-calls at once!",
        )
        require(
            "STATUS: Temporarily unavailable" in compatibility_report,
            "tool-call compatibility exhaustion was not sanitized",
        )
        require(
            "single tool-call" not in compatibility_report,
            "raw tool-call compatibility error leaked to delivery",
        )

    print("PROVIDER_CRON_SURVIVAL_RELIABILITY: PASS")


if __name__ == "__main__":
    main()
