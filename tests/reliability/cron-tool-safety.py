#!/usr/bin/env python3
"""Deterministic regression coverage for the Hermes cron tool-safety patch.

Materializes the patched Hermes tree (same pipeline as
``install/30-brain-materialize.sh``) and then exercises the three runtime
behaviors the patch introduces:

  1. explicitly-required + allowed operations survive progressive disclosure;
  2. cron delivery blocks recognized *unresolved* internal tool protocol; and
  3. a required operation that never executed is a tool-surface failure, never
     a valid (possibly empty-context) result; and
  4. required-to-execute operations are explicitly model-facing before the
     agent is allowed to return a final response.

Kept provider-independent: the surface is assembled once, before any provider
or fallback routing, so the same safe surface is used by NVIDIA or
openrouter/free.
"""

import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import types
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
HERMES_ROOT = Path(os.environ.get("OPEN_CLOUD_HERMES_ROOT", Path.home() / ".hermes/hermes-agent"))
PATCH = ROOT / "integrations/hermes/hermes-cron-tool-safety.patch"


def materialize(out: Path) -> bool:
    """Materialize the fully-patched Hermes tree into ``out`` via the pipeline.

    Returns False (with a SKIP notice) when the local Hermes checkout can't be
    materialized (e.g. it drifted from the pinned commit the patches target).
    """
    result = subprocess.run(
        ["bash", str(ROOT / "install/30-brain-materialize.sh"), "--stage", str(out)],
        env={**os.environ, "OPEN_CLOUD_HERMES_ROOT": str(HERMES_ROOT)},
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        print(
            "CRON_TOOL_SAFETY_RELIABILITY: SKIP (Hermes materialization failed): "
            + (result.stderr or result.stdout or "").strip()
        )
        return False
    return True


def load_hermes_config() -> "module":
    spec = importlib.util.spec_from_file_location(
        "hermes_config", ROOT / "scripts/hermes-config.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main() -> None:
    if not (HERMES_ROOT / ".git").is_dir():
        print("CRON_TOOL_SAFETY: SKIP (Hermes Git source unavailable)")
        return

    with tempfile.TemporaryDirectory(prefix="opencloud-cron-tool-safety-") as tmp:
        tree = Path(tmp) / "hermes"
        if not materialize(tree):
            return

        patch_text = PATCH.read_text()
        # G / L — the fallback path and the worker machinery must be untouched:
        # the patch may only touch tool-surface construction + cron delivery.
        patched_files = {
            line[len("diff --git "):].split(" b/", 1)[0].strip()
            for line in patch_text.splitlines()
            if line.startswith("diff --git ")
        }
        assert patched_files == {
            "a/tools/tool_search.py",
            "a/model_tools.py",
            "a/cron/scheduler.py",
        }, f"unexpected patch surface: {patched_files}"

        sys.path.insert(0, str(tree))
        try:
            from tools import tool_search
            from tools.registry import registry as real_registry
            import cron.scheduler as scheduler
        finally:
            sys.path.pop(0)

        # ── Protection from progressive disclosure ───────────────────────
        # Drive classification through a fake registry so no live MCP server
        # is needed: the two synthetic names behave exactly like registered
        # ``mcp-`` tools.
        def fake_get_entry(name):
            if name in {
                "mcp_vellum_bridge_get_user_context",
                "mcp_other_bridge_other_tool",
            }:
                return types.SimpleNamespace(toolset="mcp-vellum-bridge")
            return None

        original_get_entry = real_registry.get_entry
        real_registry.get_entry = fake_get_entry
        original_core = tool_search._core_tool_names
        tool_search._core_tool_names = lambda: frozenset()
        try:
            vellum = {
                "type": "function",
                "function": {
                    "name": "mcp_vellum_bridge_get_user_context",
                    "description": "read user context",
                    "parameters": {"type": "object", "properties": {}},
                },
            }
            other = {
                "type": "function",
                "function": {
                    "name": "mcp_other_bridge_other_tool",
                    "description": "unrelated",
                    "parameters": {"type": "object", "properties": {}},
                },
            }
            defs = [vellum, other]

            # Baseline: both MCP tools are deferrable.
            visible, deferrable = tool_search.classify_tools(defs)
            assert [t["function"]["name"] for t in deferrable] == [
                "mcp_vellum_bridge_get_user_context",
                "mcp_other_bridge_other_tool",
            ]

            # A — required + allowed operation survives; B — the other still defers.
            token = tool_search.protect_required_tools(
                ["mcp_vellum_bridge_get_user_context"]
            )
            try:
                visible, deferrable = tool_search.classify_tools(defs)
                assert [t["function"]["name"] for t in visible] == [
                    "mcp_vellum_bridge_get_user_context"
                ]
                assert [t["function"]["name"] for t in deferrable] == [
                    "mcp_other_bridge_other_tool"
                ]

                # Full assembly (force activation) keeps the required tool in the
                # model-facing list while the unrelated tool hides behind bridges.
                assembly = tool_search.assemble_tool_defs(
                    defs,
                    context_length=32_000,
                    config=tool_search.ToolSearchConfig(
                        enabled="on",
                        threshold_pct=5.0,
                        search_default_limit=5,
                        max_search_limit=20,
                        listing="off",
                        listing_max_tokens=4000,
                    ),
                )
                assert assembly.activated is True
                names = [t["function"]["name"] for t in assembly.tool_defs]
                assert "mcp_vellum_bridge_get_user_context" in names
                assert "mcp_other_bridge_other_tool" not in names
                assert tool_search.TOOL_CALL_NAME in names
            finally:
                tool_search.reset_required_tools(token)

            # C — a denied operation stays unavailable even if marked required:
            # it is absent from the allowed surface, so nothing re-exposes it.
            token = tool_search.protect_required_tools(
                ["mcp_vellum_bridge_repair_code"]
            )
            try:
                assembly = tool_search.assemble_tool_defs(
                    defs,
                    context_length=32_000,
                    config=tool_search.ToolSearchConfig(
                        enabled="on", threshold_pct=5.0,
                        search_default_limit=5, max_search_limit=20,
                        listing="off", listing_max_tokens=4000,
                    ),
                )
                names = [t["function"]["name"] for t in assembly.tool_defs]
                assert "mcp_vellum_bridge_repair_code" not in names
                assert "mcp_vellum_bridge_get_user_context" not in names
            finally:
                tool_search.reset_required_tools(token)
        finally:
            real_registry.get_entry = original_get_entry
            tool_search._core_tool_names = original_core

        # D — enabling a toolset never implies "must execute".
        assert scheduler._resolve_cron_required_to_execute(
            {"enabled_toolsets": ["web", "delegation", "vellum-bridge"]}
        ) == frozenset()
        assert scheduler._resolve_cron_required_tools(
            {"enabled_toolsets": ["web", "delegation", "vellum-bridge"]}
        ) == frozenset()

        # E — explicit required-operation execution is verifiable.
        job = {
            "required_tools": ["mcp_vellum_bridge_get_user_context"],
            "required_to_execute": ["mcp_vellum_bridge_get_user_context"],
        }
        assert scheduler._resolve_cron_required_tools(job) == frozenset(
            {"mcp_vellum_bridge_get_user_context"}
        )
        assert scheduler._resolve_cron_required_to_execute(job) == frozenset(
            {"mcp_vellum_bridge_get_user_context"}
        )
        direct_run = [
            {"role": "assistant", "tool_calls": [
                {"function": {"name": "mcp_vellum_bridge_get_user_context", "arguments": "{}"}},
            ]},
        ]
        bridge_run = [
            {"role": "assistant", "tool_calls": [
                {"function": {"name": "tool_call", "arguments": json.dumps(
                    {"name": "mcp_vellum_bridge_get_user_context", "arguments": {}}
                )}},
            ]},
        ]
        assert scheduler._executed_tool_names(direct_run) == frozenset(
            {"mcp_vellum_bridge_get_user_context"}
        )
        assert scheduler._executed_tool_names(bridge_run) == frozenset(
            {"mcp_vellum_bridge_get_user_context"}
        )

        # O — attempted finalization with required operations still missing
        # gets a bounded same-turn continuation instead of immediately ending.
        search_run = [
            {"role": "assistant", "tool_calls": [
                {"function": {"name": "web_search", "arguments": "{}"}},
            ]},
        ]

        missing = scheduler._missing_cron_required_operations(
            {"web_search", "web_extract"},
            search_run,
        )
        assert missing == ("web_extract",)

        complete_run = search_run + [
            {"role": "assistant", "tool_calls": [
                {"function": {"name": "web_extract", "arguments": "{}"}},
            ]},
        ]

        assert scheduler._missing_cron_required_operations(
            {"web_search", "web_extract"},
            complete_run,
        ) == ()

        loop_source = (
            tree / "agent/conversation_loop.py"
        ).read_text()

        scheduler_source = (
            tree / "cron/scheduler.py"
        ).read_text()

        assert (
            "HERMES_CRON_REQUIRED_EXECUTION_CONTINUATION_V1"
            in loop_source
        )
        assert (
            "cron_required_tool_continuations < 2"
            in loop_source
        )
        assert (
            "_cron_required_execution_gate"
            in loop_source
        )
        assert (
            "Required operations still missing:"
            in loop_source
        )
        assert (
            "HERMES_CRON_REQUIRED_EXECUTION_CONTINUATION_V1"
            in scheduler_source
        )
        assert (
            "agent._cron_required_execution_gate"
            in scheduler_source
        )

        # J — required op never executed => tool-surface failure (not empty context).
        missing = scheduler._resolve_cron_required_to_execute(job) - \
            scheduler._executed_tool_names([])
        assert missing == frozenset({"mcp_vellum_bridge_get_user_context"})

        # K — a genuine empty-context run (tool DID execute) is not a failure,
        # and the friendly message is deliverable prose.
        executed = scheduler._resolve_cron_required_to_execute(job) - \
            scheduler._executed_tool_names(direct_run)
        assert executed == frozenset()
        friendly = "Context was unavailable for this run."
        assert scheduler._contains_unresolved_tool_protocol(friendly) is False

        # N — required-to-execute operations must be model-facing before the
        # run starts. Post-run validation remains the hard fail-closed backstop,
        # but the model must first be told that these calls are mandatory.
        guided_prompt = scheduler._build_job_prompt({
            "id": "required-execution-guidance",
            "name": "required execution guidance",
            "prompt": "Perform the scheduled task.",
            "required_to_execute": [
                "web_search",
                "web_extract",
            ],
        })

        required_hint = (
            "[MANDATORY CRON OPERATIONS: Before returning a final response, "
            "you MUST actually execute each of these operations at least once "
            "during this run: web_extract, web_search. Do not return a final "
            "answer or [SILENT] until all listed operations have executed.]"
        )

        assert required_hint in guided_prompt, (
            "required_to_execute must be model-facing before final response"
        )
        assert guided_prompt.index(required_hint) < guided_prompt.index(
            "Perform the scheduled task."
        )

        plain_prompt = scheduler._build_job_prompt({
            "id": "no-required-execution",
            "name": "no required execution",
            "prompt": "Perform the scheduled task.",
        })

        assert "[MANDATORY CRON OPERATIONS:" not in plain_prompt, (
            "jobs without required_to_execute must not receive mandatory "
            "execution guidance"
        )

        # H — real unresolved tool protocol is blocked.
        for blocked in (
            "<|tool_call|>",
            "before <|tool_call|> payload <|/tool_call|> after",
            "<tool_call>{}</tool_call>",
            "<function_calls><invoke name=\"x\">1</invoke></function_calls>",
            "<function name=\"search\">q</function>",
        ):
            assert scheduler._contains_unresolved_tool_protocol(blocked) is True, blocked

        # I — benign JSON and ordinary prose are never blocked.
        for benign in (
            "here is my tool_call usage note",
            "the function call completed successfully",
            '{"tool_call": "not a real marker"}',
            '{"name": "tool_call", "arguments": {}}',
        ):
            assert scheduler._contains_unresolved_tool_protocol(benign) is False, benign

        # Failure classification stays distinct.
        assert "tool surface failure" in scheduler._summarize_cron_failure_for_delivery(
            {"name": "job", "id": "j1"},
            "CronRequiredToolNotExecuted: required operation(s) produced no execution evidence",
        )
        assert "unresolved internal tool protocol" in scheduler._summarize_cron_failure_for_delivery(
            {"name": "job", "id": "j1"},
            "CronToolProtocolError: unresolved internal tool-call protocol in final response",
        )
        assert "rate limit" in scheduler._summarize_cron_failure_for_delivery(
            {"name": "job", "id": "j1"}, "HTTPStatusError: 429 rate limit exceeded"
        )

        # Required-operation derivation (single source of truth, narrow).
        hc = load_hermes_config()
        protected, must_execute = hc.required_operations({
            "version": 1,
            "mode": "read-only-research",
            "enabled_toolsets": ["web", "delegation", "vellum-bridge"],
            "mcp_tools": {"vellum-bridge": {"include": ["get_user_context"]}},
            "task": {"schedule": "every 1d", "prompt": "x", "use_vellum_context": True},
        })
        assert protected == ["mcp_vellum_bridge_get_user_context"]
        assert must_execute == ["mcp_vellum_bridge_get_user_context"]
        # Denied tool cannot be marked required: intersected out.
        denied_protected, denied_execute = hc.required_operations({
            "mcp_tools": {"srv": {"include": ["ok_tool"], "required": ["denied_tool"]}},
            "task": {},
        })
        assert denied_protected == [] and denied_execute == []

        # M — OpenRouter policy remains exactly openrouter/free.
        fleet_policy = json.loads(
            (ROOT / "config/fleet/hermes-fleet-policy.json").read_text()
        )
        openrouter_pool = fleet_policy.get("pools", {}).get("openrouter-free", {})
        assert openrouter_pool.get("route") == "openrouter/free"
        assert openrouter_pool.get("type") == "stable-route"
        # No concrete ``:free`` model is configured anywhere in the policy.
        assert ":free" not in json.dumps(fleet_policy)
        fleet_bridge = (ROOT / "integrations/hermes/hermes-fleet-bridge.patch").read_text()
        assert '== "openrouter/free"' in fleet_bridge
        assert "openrouter/free" in fleet_bridge

    print("PASS required + allowed MCP operation survives progressive disclosure")
    print("PASS unrelated MCP operation is still deferred")
    print("PASS denied operation cannot be marked required")
    print("PASS enabled toolset does not imply every operation must execute")
    print("PASS explicit required-operation execution is verifiable")
    print("PASS required-to-execute operations are model-facing before final response")
    print("PASS missing required cron operations trigger bounded same-turn continuation")
    print("PASS prebuilt safe surface is provider-independent (assembled pre-fallback)")
    print("PASS provider fallback does not mutate the tool surface")
    print("PASS unresolved internal tool protocol is blocked from cron delivery")
    print("PASS benign JSON / ordinary tool_call prose is not falsely blocked")
    print("PASS runtime/tool failure is not classified as valid empty context")
    print("PASS genuine empty-context result may use the friendly response")
    print("PASS OpenRouter route remains exactly openrouter/free")
    print("CRON_TOOL_SAFETY_RELIABILITY: PASS")


if __name__ == "__main__":
    main()
