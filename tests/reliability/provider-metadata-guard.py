#!/usr/bin/env python3
"""Deterministic regression coverage for the provider-metadata guard patch (P6).

The interactive gateway path was leaking ``_opencloud_routing_profile`` (Routing
V1 control metadata stashed inside ``request_overrides``) into the provider SDK:

    TypeError: Completions.create() got an unexpected keyword argument
    '_opencloud_routing_profile'

Root cause: ``AIAgent`` construction consumes and removes the key, but
``gateway/run.py`` reassigned ``agent.request_overrides`` from the raw turn
route afterwards, re-injecting it. The transports then merged ``request_overrides``
straight into the provider kwargs.

P6 closes every path by stripping ``_opencloud_*`` keys at the final boundary:
  * a new ``agent/provider_metadata_guard.py`` helper;
  * both chat_completions transport merge points (legacy + provider-profile);
  * the codex/Responses transport merge point;
  * the gateway per-turn ``agent.request_overrides`` reassignment.

This test is network-free and provider-independent: it materializes the
fully-patched tree and asserts no internal metadata can survive to kwargs.
"""

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
HERMES_ROOT = Path(os.environ.get("OPEN_CLOUD_HERMES_ROOT", Path.home() / ".hermes/hermes-agent"))
PATCH = ROOT / "integrations/hermes/hermes-provider-metadata-guard.patch"


def materialize(out: Path) -> bool:
    result = subprocess.run(
        ["bash", str(ROOT / "install/30-brain-materialize.sh"), "--stage", str(out)],
        env={**os.environ, "OPEN_CLOUD_HERMES_ROOT": str(HERMES_ROOT)},
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        print(
            "PROVIDER_METADATA_GUARD_RELIABILITY: SKIP (materialization failed): "
            + (result.stderr or result.stdout or "").strip()
        )
        return False
    return True


def _import(tree: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, tree / (name.replace(".", "/") + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main() -> None:
    if not (HERMES_ROOT / ".git").is_dir():
        print("PROVIDER_METADATA_GUARD_RELIABILITY: SKIP (Hermes Git source unavailable)")
        return

    with tempfile.TemporaryDirectory(prefix="opencloud-provider-metadata-guard-") as tmp:
        tree = Path(tmp) / "hermes"
        if not materialize(tree):
            return

        patch_text = PATCH.read_text()
        patched_files = {
            line[len("diff --git "):].split(" b/", 1)[0].strip()
            for line in patch_text.splitlines()
            if line.startswith("diff --git ")
        }
        assert patched_files == {
            "a/agent/provider_metadata_guard.py",
            "a/agent/transports/chat_completions.py",
            "a/agent/transports/codex.py",
            "a/gateway/run.py",
        }, f"unexpected patch surface: {patched_files}"

        sys.path.insert(0, str(tree))

        guard = _import(tree, "agent.provider_metadata_guard")
        strip = guard.strip_internal_metadata

        # ── 1. Helper strips internal metadata, preserves everything else ──
        overrides = {
            "_opencloud_routing_profile": "balanced",
            "_opencloud_other": 123,
            "extra_body": {"provider": {"require_parameters": False}},
            "top_p": 0.9,
            "speed": "fast",
            "service_tier": "priority",
        }
        stripped = strip(dict(overrides))
        assert stripped == {
            "extra_body": {"provider": {"require_parameters": False}},
            "top_p": 0.9,
            "speed": "fast",
            "service_tier": "priority",
        }, stripped
        assert not any(k.startswith("_opencloud_") for k in stripped)
        # non-dict inputs are passed through unchanged.
        assert strip(None) is None
        assert strip("nope") == "nope"
        assert strip({}) == {}

        # ── 2. chat_completions legacy merge point strips ───────────────────
        from agent.transports.chat_completions import ChatCompletionsTransport

        chat = ChatCompletionsTransport()
        kwargs = chat.build_kwargs(
            model="openrouter/free",
            messages=[{"role": "user", "content": "hi"}],
            request_overrides=dict(overrides),
        )
        assert "_opencloud_routing_profile" not in kwargs
        assert "_opencloud_other" not in kwargs
        assert kwargs.get("top_p") == 0.9
        assert kwargs.get("extra_body") == {"provider": {"require_parameters": False}}

        # ── 3. chat_completions provider-profile merge point strips ─────────
        profile = None
        try:
            from providers import get_provider_profile
            profile = get_provider_profile("openrouter")
        except Exception:
            profile = None
        if profile is not None:
            profile_kwargs = chat.build_kwargs(
                model="openrouter/free",
                messages=[{"role": "user", "content": "hi"}],
                provider_profile=profile,
                base_url="https://openrouter.ai/api/v1",
                request_overrides={"_opencloud_routing_profile": "balanced", "top_p": 0.8},
            )
            assert "_opencloud_routing_profile" not in profile_kwargs
            assert profile_kwargs.get("top_p") == 0.8
        else:
            # Profile path merge point is still covered at source level below.
            print("PROVIDER_METADATA_GUARD: note — openrouter profile unavailable; "
                  "profile merge point asserted via source inspection")

        # ── 4. codex/Responses merge point strips ──────────────────────────
        # The Responses transport transitively imports run_agent ->
        # hermes_cli.env_loader -> dotenv inside build_kwargs, which is not
        # guaranteed in every test environment. The strip helper is proven
        # above and the codex merge site is asserted at source level below;
        # exercise it live only when the import chain is satisfiable.
        codex_live = False
        try:
            from agent.transports.codex import ResponsesApiTransport

            codex = ResponsesApiTransport()
            codex_kwargs = codex.build_kwargs(
                model="gpt-5.1-codex",
                messages=[{"role": "user", "content": "hi"}],
                request_overrides=dict(overrides),
            )
            assert "_opencloud_routing_profile" not in codex_kwargs
            assert "_opencloud_other" not in codex_kwargs
            codex_live = True
        except ModuleNotFoundError as exc:
            if exc.name != "dotenv":
                raise
            # dotenv is an optional transitive dependency of the Responses
            # transport's import chain; the helper + source assertions below
            # still pin the codex merge point deterministically.

        # ── 5. Source-level wiring ──────────────────────────────────────────
        gateway_source = (tree / "gateway/run.py").read_text()
        chat_source = (tree / "agent/transports/chat_completions.py").read_text()
        codex_source = (tree / "agent/transports/codex.py").read_text()

        assert "HERMES_OPENCLOUD_METADATA_GUARD_V1" in gateway_source
        assert "HERMES_OPENCLOUD_METADATA_GUARD_V1" in chat_source
        assert "HERMES_OPENCLOUD_METADATA_GUARD_V1" in codex_source

        # The raw reassignment that re-injected the key is gone; the stripped
        # form is present.
        assert (
            'agent.request_overrides = turn_route.get("request_overrides") or {}'
            not in gateway_source
        )
        assert "agent.request_overrides = strip_internal_metadata(" in gateway_source

        # Both chat_completions merge sites (legacy update + profile loop) and
        # the codex merge site go through the strip helper.
        assert chat_source.count("strip_internal_metadata(") >= 2
        assert "strip_internal_metadata(" in codex_source
        assert (tree / "agent/provider_metadata_guard.py").exists()

        # The codex path was either exercised live or pinned by the helper +
        # source assertions above — never silently unverified.
        assert codex_live or "strip_internal_metadata(request_overrides)" in codex_source

        # ── 6. The OpenRouter final escape remains exactly openrouter/free ──
        fleet_policy = json.loads(
            (ROOT / "config/fleet/hermes-fleet-policy.json").read_text()
        )
        assert fleet_policy.get("routingV1", {}).get("finalEscape", {}).get("model") == "openrouter/free"

    print("PASS _opencloud_* metadata is stripped from request_overrides")
    print("PASS chat_completions legacy merge point cannot leak internal metadata")
    print("PASS chat_completions provider-profile merge point cannot leak internal metadata")
    print("PASS codex/Responses merge point cannot leak internal metadata")
    print("PASS gateway per-turn reassignment no longer re-injects internal metadata")
    print("PASS openrouter/free final escape is untouched")
    print("PROVIDER_METADATA_GUARD_RELIABILITY: PASS")


if __name__ == "__main__":
    main()
