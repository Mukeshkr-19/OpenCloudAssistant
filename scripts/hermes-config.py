#!/usr/bin/env python3
import argparse
import json
import os
import stat
import tempfile
import time
from pathlib import Path

import yaml


def load_policy(path):
    data = json.loads(Path(path).read_text())
    required = {
        "orchestrator_enabled": True,
        "max_concurrent_children": 3,
        "max_iterations": 50,
        "max_spawn_depth": 1,
        "inherit_mcp_toolsets": True,
    }
    for key, value in required.items():
        if data.get(key) != value:
            raise SystemExit("ERROR: unexpected canonical policy value for " + key)
    if "vellum-bridge" not in data.get("required_mcp_toolsets", []):
        raise SystemExit("ERROR: canonical policy must require vellum-bridge")
    return data


def load_config(path):
    p = Path(path)
    if not p.exists():
        return {}
    data = yaml.safe_load(p.read_text()) or {}
    if not isinstance(data, dict):
        raise SystemExit("ERROR: Hermes config must be a YAML mapping")
    return data


def desired_config(data, policy, server, python_cmd):
    delegation = data.setdefault("delegation", {})
    if not isinstance(delegation, dict):
        raise SystemExit("ERROR: delegation config must be a mapping")

    delegation["orchestrator_enabled"] = bool(policy["orchestrator_enabled"])
    delegation["max_concurrent_children"] = int(policy["max_concurrent_children"])
    delegation["max_iterations"] = int(policy["max_iterations"])
    delegation["max_spawn_depth"] = int(policy["max_spawn_depth"])
    delegation["inherit_mcp_toolsets"] = bool(policy["inherit_mcp_toolsets"])

    mcp = data.setdefault("mcp_servers", {})
    if not isinstance(mcp, dict):
        raise SystemExit("ERROR: mcp_servers config must be a mapping")

    mcp["vellum-bridge"] = {
        "enabled": True,
        "command": str(python_cmd),
        "args": [str(server)],
        "connect_timeout": 30.0,
    }

    return data


def verify(data, policy, server, python_cmd):
    delegation = data.get("delegation", {}) or {}
    expected = {
        "orchestrator_enabled": bool(policy["orchestrator_enabled"]),
        "max_concurrent_children": int(policy["max_concurrent_children"]),
        "max_iterations": int(policy["max_iterations"]),
        "max_spawn_depth": int(policy["max_spawn_depth"]),
        "inherit_mcp_toolsets": bool(policy["inherit_mcp_toolsets"]),
    }

    for key, value in expected.items():
        if delegation.get(key) != value:
            raise SystemExit("ERROR: Hermes delegation mismatch: " + key)

    bridge = (data.get("mcp_servers", {}) or {}).get("vellum-bridge", {}) or {}

    if bridge.get("enabled") is not True:
        raise SystemExit("ERROR: vellum-bridge is not enabled")
    if str(bridge.get("command", "")) != str(python_cmd):
        raise SystemExit("ERROR: vellum-bridge Python command mismatch")
    if bridge.get("args") != [str(server)]:
        raise SystemExit("ERROR: vellum-bridge server argument mismatch")
    if float(bridge.get("connect_timeout", 0)) != 30.0:
        raise SystemExit("ERROR: vellum-bridge timeout mismatch")

    print("HERMES_VELLUM_CONFIG_VERIFY: PASS")


def atomic_write(path, data):
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)

    old = load_config(p) if p.exists() else {}
    if old == data:
        print("HERMES_CONFIG_CHANGED: NO")
        return

    if p.exists():
        stamp = time.strftime("%Y%m%d-%H%M%S", time.gmtime())
        backup = p.with_name(p.name + ".before-opencloud-" + stamp)
        backup.write_bytes(p.read_bytes())
        os.chmod(backup, stat.S_IMODE(p.stat().st_mode))
        print("HERMES_CONFIG_BACKUP:", backup)

    mode = stat.S_IMODE(p.stat().st_mode) if p.exists() else 0o600
    fd, tmp = tempfile.mkstemp(prefix=p.name + ".", dir=str(p.parent))
    os.close(fd)

    tp = Path(tmp)
    tp.write_text(yaml.safe_dump(data, sort_keys=False))
    os.chmod(tp, mode)
    os.replace(tp, p)

    print("HERMES_CONFIG_CHANGED: YES")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("command", choices=["apply", "verify"])
    ap.add_argument("--config", required=True)
    ap.add_argument("--policy", required=True)
    ap.add_argument("--server", required=True)
    ap.add_argument("--python", required=True, dest="python_cmd")
    args = ap.parse_args()

    policy = load_policy(args.policy)
    data = load_config(args.config)

    if args.command == "apply":
        data = desired_config(data, policy, args.server, args.python_cmd)
        atomic_write(args.config, data)
        data = load_config(args.config)

    verify(data, policy, args.server, args.python_cmd)


if __name__ == "__main__":
    main()
