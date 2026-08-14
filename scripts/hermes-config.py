#!/usr/bin/env python3
"""Apply and verify OpenCloud's generic Hermes runtime and task profiles."""

import argparse
import json
import os
import stat
import tempfile
import time
from pathlib import Path

import yaml


def load_json(path):
    data = json.loads(Path(path).read_text())
    if not isinstance(data, dict):
        raise SystemExit("ERROR: configuration must be an object")
    return data


def load_policy(path):
    data = load_json(path)
    bounds = {
        "max_concurrent_children": (1, 8), "max_iterations": (1, 50),
        "child_timeout_seconds": (1, 600), "provider_request_timeout_seconds": (1, 120),
        "max_spawn_depth": (1, 2),
    }
    if data.get("orchestrator_enabled") is not True or data.get("inherit_mcp_toolsets") is not True:
        raise SystemExit("ERROR: canonical orchestration policy is invalid")
    for key, (low, high) in bounds.items():
        if not isinstance(data.get(key), int) or not low <= data[key] <= high:
            raise SystemExit(f"ERROR: {key} must be between {low} and {high}")
    display = (((data.get("display") or {}).get("platforms") or {}).get("bluebubbles") or {})
    if display.get("tool_progress") != "off" or any(display.get(key) is not False for key in (
        "show_reasoning", "streaming", "interim_assistant_messages",
        "long_running_notifications", "busy_ack_detail", "thinking_progress",
    )):
        raise SystemExit("ERROR: final-only display policy is invalid")
    return data


def load_task_profile(path):
    data = load_json(path)
    if data.get("version") != 1 or data.get("mode") != "read-only-research":
        raise SystemExit("ERROR: unsupported task profile")
    forbidden = {"terminal", "file", "code_execution", "messaging", "clarify", "cronjob"}
    enabled = set(data.get("enabled_toolsets") or [])
    if not enabled or enabled & forbidden:
        raise SystemExit("ERROR: read-only profile requests a forbidden toolset")
    for key, low, high in (
        ("parent_max_turns", 1, 100), ("max_concurrent_children", 1, 8),
        ("child_max_iterations", 1, 50), ("child_timeout_seconds", 1, 600),
        ("max_spawn_depth", 1, 2),
    ):
        if not isinstance(data.get(key), int) or not low <= data[key] <= high:
            raise SystemExit(f"ERROR: task profile {key} must be between {low} and {high}")
    mcp_tools = data.get("mcp_tools")
    if not isinstance(mcp_tools, dict) or not mcp_tools:
        raise SystemExit("ERROR: restrictive profiles require explicit MCP tool allowlists")
    for server, rule in mcp_tools.items():
        include = rule.get("include") if isinstance(rule, dict) else None
        if not isinstance(include, list) or not include or not all(isinstance(x, str) for x in include):
            raise SystemExit(f"ERROR: {server} requires a non-empty tools.include list")
        required = rule.get("required") if isinstance(rule, dict) else None
        if required is not None:
            if not isinstance(required, list) or not all(isinstance(x, str) for x in required):
                raise SystemExit(f"ERROR: {server}.required must be a string list")
            if set(required) - set(include):
                raise SystemExit(f"ERROR: {server}.required must be a subset of tools.include")
        if server == "vellum-bridge" and set(include) - {"get_user_context"}:
            raise SystemExit("ERROR: read-only Vellum access permits only get_user_context")
    task = data.get("task")
    if task is not None:
        if not isinstance(task, dict):
            raise SystemExit("ERROR: task profile task must be an object")
        for key in ("schedule", "prompt"):
            if not isinstance(task.get(key), str) or not task[key].strip():
                raise SystemExit(f"ERROR: task profile task.{key} is required")
        topics = task.get("research_topics", [])
        if not isinstance(topics, list) or not all(isinstance(item, str) and item.strip() for item in topics):
            raise SystemExit("ERROR: task profile research_topics must be a string list")
        if not isinstance(task.get("use_vellum_context", False), bool):
            raise SystemExit("ERROR: task profile use_vellum_context must be boolean")
        if task.get("use_vellum_context") and "get_user_context" not in (
            (mcp_tools.get("vellum-bridge") or {}).get("include") or []
        ):
            raise SystemExit("ERROR: Vellum context requires get_user_context capability")
        deliver = task.get("deliver", "local")
        if not isinstance(deliver, str) or not deliver.strip():
            raise SystemExit("ERROR: task profile task.deliver must be a string")
        for key in ("output_policy", "scoring_policy"):
            value = task.get(key)
            if value is not None and not isinstance(value, (str, list, dict)):
                raise SystemExit(f"ERROR: task profile task.{key} has an unsupported type")
    return data


def required_operations(profile):
    """Derive explicitly-required operations for a task profile.

    Returns ``(protected, required_to_execute)`` — two sorted lists of
    fully-qualified MCP registry tool names (``mcp_<server>_<tool>``).

    Only explicit signals populate these sets, never the enabled toolset.
    ``protected`` operations must stay directly model-visible (survive
    progressive disclosure); ``required_to_execute`` operations must produce
    execution evidence on every run. Both are intersected with each server's
    ``tools.include`` allowlist, so a denied tool can never be marked required.
    """
    task = profile.get("task") or {}
    mcp_tools = profile.get("mcp_tools") or {}
    protected = set()
    must_execute = set()
    for server, rule in mcp_tools.items():
        if not isinstance(rule, dict):
            continue
        include = set(rule.get("include") or [])
        required = rule.get("required") or []
        if not isinstance(required, list):
            required = []
        required = [str(t) for t in required if isinstance(t, str)]
        if server == "vellum-bridge" and task.get("use_vellum_context") is True:
            required = list(dict.fromkeys([*required, "get_user_context"]))
        for tool in required:
            if tool in include:
                fq = f"mcp_{server.replace('-', '_')}_{tool}"
                protected.add(fq)
                must_execute.add(fq)
    return sorted(protected), sorted(must_execute)


def load_config(path):
    path = Path(path)
    if not path.exists():
        return {}
    data = yaml.safe_load(path.read_text()) or {}
    if not isinstance(data, dict):
        raise SystemExit("ERROR: Hermes config must be a YAML mapping")
    return data


def desired_config(data, policy, server, python_cmd, profile=None):
    delegation = data.setdefault("delegation", {})
    mcp = data.setdefault("mcp_servers", {})
    if not isinstance(delegation, dict) or not isinstance(mcp, dict):
        raise SystemExit("ERROR: Hermes delegation and MCP config must be mappings")
    for key in ("orchestrator_enabled", "max_concurrent_children", "max_iterations",
                "child_timeout_seconds", "max_spawn_depth", "inherit_mcp_toolsets"):
        delegation[key] = policy[key]
    providers = data.setdefault("providers", {})
    if not isinstance(providers, dict):
        raise SystemExit("ERROR: Hermes providers config must be a mapping")
    for name in ("opencode-zen", "nvidia", "openrouter"):
        provider = providers.setdefault(name, {})
        if not isinstance(provider, dict):
            raise SystemExit(f"ERROR: Hermes provider config must be a mapping: {name}")
        provider["request_timeout_seconds"] = 60 if name == "nvidia" else policy["provider_request_timeout_seconds"]
    mcp["vellum-bridge"] = {
        "enabled": True, "command": str(python_cmd), "args": [str(server)],
        "connect_timeout": 30.0,
    }
    display = data.setdefault("display", {}).setdefault("platforms", {}).setdefault("bluebubbles", {})
    display.update(policy["display"]["platforms"]["bluebubbles"])
    data.setdefault("gateway", {})["multiplex_profiles"] = True
    if profile:
        data.setdefault("agent", {})["max_turns"] = profile["parent_max_turns"]
        delegation.update(
            max_concurrent_children=profile["max_concurrent_children"],
            max_iterations=profile["child_max_iterations"],
            child_timeout_seconds=profile["child_timeout_seconds"],
            max_spawn_depth=profile["max_spawn_depth"],
        )
        data.setdefault("platform_toolsets", {})["cron"] = profile["enabled_toolsets"]
        for name, rule in profile["mcp_tools"].items():
            if name not in mcp:
                raise SystemExit(f"ERROR: task profile requests unconfigured MCP server: {name}")
            mcp[name]["tools"] = {"include": rule["include"]}
    return data


def verify(data, policy, server, python_cmd, profile=None):
    expected = desired_config({}, policy, server, python_cmd, profile)
    for key, value in expected["delegation"].items():
        if (data.get("delegation") or {}).get(key) != value:
            raise SystemExit("ERROR: Hermes delegation mismatch: " + key)
    for name, expected_provider in expected["providers"].items():
        actual_provider = (data.get("providers") or {}).get(name) or {}
        if actual_provider.get("request_timeout_seconds") != expected_provider["request_timeout_seconds"]:
            raise SystemExit("ERROR: Hermes provider request timeout mismatch: " + name)
    bridge = (data.get("mcp_servers") or {}).get("vellum-bridge") or {}
    for key, value in expected["mcp_servers"]["vellum-bridge"].items():
        if bridge.get(key) != value:
            raise SystemExit("ERROR: Vellum bridge mismatch: " + key)
    actual_display = (((data.get("display") or {}).get("platforms") or {}).get("bluebubbles") or {})
    for key, value in policy["display"]["platforms"]["bluebubbles"].items():
        if actual_display.get(key) != value:
            raise SystemExit("ERROR: final-only display mismatch: " + key)
    if (data.get("gateway") or {}).get("multiplex_profiles") is not True:
        raise SystemExit("ERROR: Hermes profile multiplexing is not enabled")
    if profile:
        if (data.get("agent") or {}).get("max_turns") != profile["parent_max_turns"]:
            raise SystemExit("ERROR: task profile parent turn budget mismatch")
        if (data.get("platform_toolsets") or {}).get("cron") != profile["enabled_toolsets"]:
            raise SystemExit("ERROR: task profile toolset mismatch")
        for name, rule in profile["mcp_tools"].items():
            actual = (((data.get("mcp_servers") or {}).get(name) or {}).get("tools") or {}).get("include")
            if actual != rule["include"]:
                raise SystemExit(f"ERROR: task profile MCP allowlist mismatch: {name}")
    print("HERMES_CONFIG_VERIFY: PASS")


def atomic_write(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and load_config(path) == data:
        print("HERMES_CONFIG_CHANGED: NO")
        return
    if path.exists():
        backup = path.with_name(path.name + ".before-opencloud-" + time.strftime("%Y%m%d-%H%M%S", time.gmtime()))
        backup.write_bytes(path.read_bytes())
        os.chmod(backup, stat.S_IMODE(path.stat().st_mode))
    mode = stat.S_IMODE(path.stat().st_mode) if path.exists() else 0o600
    fd, tmp = tempfile.mkstemp(prefix=path.name + ".", dir=str(path.parent))
    os.close(fd)
    Path(tmp).write_text(yaml.safe_dump(data, sort_keys=False))
    os.chmod(tmp, mode)
    os.replace(tmp, path)
    print("HERMES_CONFIG_CHANGED: YES")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("apply", "verify"))
    parser.add_argument("--config", required=True)
    parser.add_argument("--policy", required=True)
    parser.add_argument("--server", required=True)
    parser.add_argument("--python", required=True, dest="python_cmd")
    parser.add_argument("--task-profile")
    args = parser.parse_args()
    policy = load_policy(args.policy)
    profile = load_task_profile(args.task_profile) if args.task_profile else None
    data = load_config(args.config)
    if args.command == "apply":
        atomic_write(args.config, desired_config(data, policy, args.server, args.python_cmd, profile))
        data = load_config(args.config)
    verify(data, policy, args.server, args.python_cmd, profile)


if __name__ == "__main__":
    main()
