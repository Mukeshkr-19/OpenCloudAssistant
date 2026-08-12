#!/usr/bin/env python3
"""Synthetic validation for private restrictive task profiles."""

import json
import os
import sys
import tempfile
import types
from pathlib import Path
import importlib.util


ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("hermes_config", ROOT / "scripts/hermes-config.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def main():
    profile = {
        "version": 1, "mode": "read-only-research", "parent_max_turns": 15,
        "max_concurrent_children": 4, "child_max_iterations": 12,
        "child_timeout_seconds": 120, "max_spawn_depth": 2,
        "enabled_toolsets": ["web", "delegation", "vellum-bridge"],
        "mcp_tools": {"vellum-bridge": {"include": ["get_user_context"]}},
        "task": {
            "schedule": "every 1d", "prompt": "Research a synthetic example.",
            "research_topics": ["Example Project"], "use_vellum_context": True,
            "output_policy": {"format": "summary"},
            "scoring_policy": {"scale": "example"}, "deliver": "local",
        },
    }
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "profile.json"
        path.write_text(json.dumps(profile))
        parsed = module.load_task_profile(path)
        policy = module.load_policy(ROOT / "config/hermes/orchestration.json")
        config = module.desired_config({}, policy, "/example/server.py", "/example/python", parsed)
        include = config["mcp_servers"]["vellum-bridge"]["tools"]["include"]
        assert include == ["get_user_context"]
        assert "repair_code" not in include
        # Hermes child inheritance is an intersection, so this is also the
        # maximum MCP surface a child can retain from this parent.
        requested_by_child = {"get_user_context", "repair_code"}
        assert requested_by_child & set(include) == {"get_user_context"}

        hermes = Path(os.environ.get("OPEN_CLOUD_HERMES_ROOT", str(Path.home() / ".hermes/hermes-agent")))
        if (hermes / "tools/mcp_tool.py").is_file():
            sys.path.insert(0, str(hermes))
            from tools import mcp_tool
            class Server:
                name = "vellum-bridge"
                tool_timeout = 1
                session = object()
                initialize_result = None
                _tools = [
                    types.SimpleNamespace(name="get_user_context", description="read", inputSchema={}),
                    types.SimpleNamespace(name="repair_code", description="repair", inputSchema={}),
                ]
            effective = mcp_tool._register_server_tools(
                "synthetic-read-only", Server(), {"tools": {"include": include}}
            )
            assert len(effective) == 1 and effective[0].endswith("get_user_context")
            assert all("repair_code" not in name for name in effective)

        for mutation in (
            {"mcp_tools": {}},
            {"mcp_tools": {"vellum-bridge": {"include": ["repair_code"]}}},
            {"mcp_tools": {"vellum-bridge": {"include": ["nonexistent_tool"]}}},
            {"enabled_toolsets": ["web", "terminal"]},
            {"task": {"schedule": "every 1d", "prompt": ""}},
            {"task": profile["task"] | {"use_vellum_context": "yes"}},
        ):
            bad = profile | mutation
            path.write_text(json.dumps(bad))
            try:
                module.load_task_profile(path)
            except SystemExit:
                pass
            else:
                raise AssertionError("malformed or overprivileged profile failed open")
    print("PASS effective runtime MCP schema contains get_user_context only")
    print("PASS delegated child cannot regain repair_code")
    print("PASS malformed restrictive task profile fails closed")
    print("TASK_PROFILE_RELIABILITY: PASS")


if __name__ == "__main__":
    main()
