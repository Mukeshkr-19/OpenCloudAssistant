#!/usr/bin/env python3

from __future__ import annotations

import importlib
import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
COMPAT = ROOT / "integrations/hermes/routing_v1_compat.py"


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def write(path: Path, text: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def main():
    with tempfile.TemporaryDirectory() as tmp:
        tree = Path(tmp) / "hermes"

        # ------------------------------------------------------------
        # Minimal package structure.
        # ------------------------------------------------------------

        write(tree / "agent/__init__.py", "")
        write(tree / "gateway/__init__.py", "")
        write(tree / "hermes_cli/__init__.py", "")

        write(
            tree / "hermes_cli/models.py",
            """
def resolve_fast_mode_overrides(model):
    return {"speed": "fast"}
""",
        )

        # ------------------------------------------------------------
        # Synthetic gateway containing the exact upstream anchors
        # consumed by routing_v1_compat.py.
        # ------------------------------------------------------------

        write(
            tree / "gateway/run.py",
            '''
class GatewayRunner:
    def __init__(self):
        self._service_tier = None

    def cache_signature(self, turn_route):
        _sig = ("base-cache-signature",)

        agent = None
        reused_cached_agent = False

        return _sig

    def _resolve_turn_agent_config(self, user_message: str, model: str, runtime_kwargs: dict) -> dict:
        return {
            "model": model,
            "runtime": runtime_kwargs,
            "signature": (model,),
        }

    def _sync_session_model_from_agent(self, session_id, agent):
        pass
''',
        )

        # ------------------------------------------------------------
        # Synthetic CLI router anchors.
        # ------------------------------------------------------------

        write(
            tree / "hermes_cli/cli_agent_setup_mixin.py",
            '''
class CliAgentSetupMixin:
    def _resolve_turn_agent_config(self, user_message: str) -> dict:
        return {
            "model": self.model,
            "runtime": {},
            "signature": (self.model,),
        }

    def _init_agent(self, **kwargs):
        return True
''',
        )

        # ------------------------------------------------------------
        # Fake Fleet bridge records the profile passed by agent_init.
        # ------------------------------------------------------------

        write(
            tree / "agent/hermes_fleet_bridge.py",
            '''
LAST_PROFILE = None

def should_manage_main(**kwargs):
    return True

def resolve_role(role, *, session_key=None, profile=None):
    global LAST_PROFILE
    LAST_PROFILE = profile
    return {
        "candidate": {
            "provider": "nvidia",
            "model": "synthetic/model",
        },
        "runtime": {},
    }
''',
        )

        # ------------------------------------------------------------
        # Synthetic agent_init containing the exact integration anchors.
        # ------------------------------------------------------------

        write(
            tree / "agent/agent_init.py",
            '''
def init_agent(
    model="synthetic/model",
    provider="nvidia",
    requested_provider=None,
    gateway_session_key="session-1",
    request_overrides=None,
):
    _hermes_fleet_bootstrap = None
    _hermes_fleet_session_key = gateway_session_key

    try:
        from agent.hermes_fleet_bridge import (
            resolve_role as _fleet_resolve,
            should_manage_main as _fleet_should_manage_main,
        )

        if _fleet_should_manage_main(
            model=model,
            provider=provider or requested_provider,
            gateway_session_key=_hermes_fleet_session_key,
        ):
            _hermes_fleet_bootstrap = _fleet_resolve(
                "main",
                session_key=_hermes_fleet_session_key,
            )

    except Exception:
        pass

    return request_overrides, _hermes_fleet_bootstrap
''',
        )

        # ------------------------------------------------------------
        # Run the real compatibility transform.
        # ------------------------------------------------------------

        subprocess.run(
            [
                sys.executable,
                str(COMPAT),
                str(tree),
            ],
            check=True,
            cwd=ROOT,
        )

        # Syntax validation of generated code.
        for rel in (
            "gateway/run.py",
            "hermes_cli/cli_agent_setup_mixin.py",
            "agent/agent_init.py",
            "agent/opencloud_routing_v1.py",
        ):
            source = (tree / rel).read_text()
            compile(source, str(tree / rel), "exec")

        # Import the transformed synthetic Hermes.
        sys.path.insert(0, str(tree))

        try:
            gateway_run = importlib.import_module("gateway.run")
            agent_init = importlib.import_module("agent.agent_init")
            fleet_bridge = importlib.import_module(
                "agent.hermes_fleet_bridge"
            )

            runner = gateway_run.GatewayRunner()

            runtime = {
                "api_key": "synthetic",
                "base_url": "https://example.invalid/v1",
                "provider": "nvidia",
                "requested_provider": "nvidia",
                "api_mode": "chat_completions",
                "command": None,
                "args": [],
                "credential_pool": None,
                "max_tokens": None,
            }

            fast_route = runner._resolve_turn_agent_config(
                "what is DNS?",
                "synthetic/model",
                runtime,
            )

            balanced_route = runner._resolve_turn_agent_config(
                "Review these deployment notes and tell me "
                "what I should change before release.",
                "synthetic/model",
                runtime,
            )

            deep_route = runner._resolve_turn_agent_config(
                "Debug this distributed race condition and "
                "perform root cause analysis.",
                "synthetic/model",
                runtime,
            )

            require(
                fast_route["routing_profile"] == "fast",
                "simple workload must route FAST",
            )

            require(
                balanced_route["routing_profile"] == "balanced",
                "normal workload must route BALANCED",
            )

            require(
                deep_route["routing_profile"] == "deep",
                "complex workload must route DEEP",
            )

            # A profile transition must change the turn-route signature.
            require(
                fast_route["signature"]
                != deep_route["signature"],
                "FAST -> DEEP must change route signature",
            )

            # A profile transition must also change the gateway AIAgent
            # cache signature, preventing reuse of the previous route.
            fast_cache = runner.cache_signature(
                fast_route
            )

            deep_cache = runner.cache_signature(
                deep_route
            )

            require(
                fast_cache != deep_cache,
                "FAST -> DEEP must invalidate gateway agent cache",
            )

            # The internal routing key must reach Fleet but must not
            # survive in provider-facing request_overrides.
            cleaned_overrides, bootstrap = agent_init.init_agent(
                request_overrides={
                    "_opencloud_routing_profile": "deep",
                    "speed": "fast",
                }
            )

            require(
                fleet_bridge.LAST_PROFILE == "deep",
                "agent_init must propagate DEEP profile to Fleet",
            )

            require(
                cleaned_overrides == {
                    "speed": "fast"
                },
                "internal routing metadata must be stripped",
            )

            require(
                bootstrap is not None,
                "Fleet bootstrap must still execute",
            )

        finally:
            sys.path.remove(str(tree))

    print(
        "HERMES_ROUTING_V1_COMPAT_RELIABILITY: PASS"
    )


if __name__ == "__main__":
    main()
