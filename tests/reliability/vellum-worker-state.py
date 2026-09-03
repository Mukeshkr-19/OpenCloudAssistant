#!/usr/bin/env python3
"""Deterministic parent/worker state-race regression tests."""

import importlib.util
import sys
import types
import json
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def load_server():
    fastmcp = types.ModuleType("mcp.server.fastmcp")
    class FastMCP:
        def __init__(self, _name, **_kwargs): self.tools = []
        def tool(self):
            def register(function):
                self.tools.append(function.__name__)
                return function
            return register
    fastmcp.FastMCP = FastMCP
    sys.modules.setdefault("mcp", types.ModuleType("mcp"))
    sys.modules.setdefault("mcp.server", types.ModuleType("mcp.server"))
    sys.modules["mcp.server.fastmcp"] = fastmcp
    spec = importlib.util.spec_from_file_location("vellum_server_test", ROOT / "integrations/vellum/server.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main():
    server = load_server()
    assert server.mcp.tools == [
        "repair_code",
        "get_user_context",
        "start_vellum_task",
        "get_vellum_task",
        "stop_vellum_task",
    ]
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        server.BASE_DIR = base
        server.STATE_DIR = base / "state"
        server.LOG_DIR = base / "logs"
        server.STATE_DIR.mkdir()
        server.LOG_DIR.mkdir()
        server.PYTHON = Path(sys.executable)
        server.WORKER = base / "worker.py"
        server.WORKER.write_text("# synthetic worker\n")

        observed = []
        real_write = server.write_state
        def recording_write(task_id, state):
            observed.append(state.get("status"))
            real_write(task_id, state)
        server.write_state = recording_write

        class InstantFailure:
            pid = 41
            def __init__(self, argv, **kwargs):
                task_id = argv[-1]
                server.update_state(task_id, lambda value: value.update(status="failed", error="instant failure"))

        server.subprocess.Popen = InstantFailure
        result = json.loads(server.start_vellum_task("synthetic instant failure"))
        state = server.read_state(result["task_id"])
        assert observed[:2] == ["queued", "starting"]
        assert result["status"] == state["status"] == "failed"
        assert state["worker_pid"] == 41

        class LaunchFailure:
            def __init__(self, *args, **kwargs):
                raise OSError("synthetic popen failure")

        server.subprocess.Popen = LaunchFailure
        result = json.loads(server.start_vellum_task("synthetic launch failure"))
        assert result["status"] == "failed"
        assert server.read_state(result["task_id"])["status"] == "failed"

        server.WORKER.unlink()
        result = json.loads(server.start_vellum_task("synthetic preflight failure"))
        assert result["status"] == "failed"

        task_id = "vellum_" + "a" * 32
        server.write_state(task_id, {
            "task_id": task_id,
            "status": "completed",
            "result": "synthetic completed payload",
        })
        result = json.loads(server.get_vellum_task(task_id))
        assert result["result"] == "synthetic completed payload"

    print("PASS exact public Vellum MCP tool surface")
    print("PASS completed worker result round-trips to Hermes")
    print("PASS queued state precedes worker start")
    print("PASS instant worker failure cannot regress to starting")
    print("PASS Popen and packaged-runtime failures become terminal")
    print("VELLUM_WORKER_STATE_RELIABILITY: PASS")


if __name__ == "__main__":
    main()
