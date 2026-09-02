#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

echo "Open Cloud Assistant brain reference smoke test"

test -f config/fleet/hermes-fleet-policy.json
test -f integrations/hermes/hermes-fleet-bridge.patch
test -f integrations/hermes/hermes-live.patch
test -f integrations/vellum/mcp-managed-blocks.py
test -f docs/COMPATIBILITY.md
test -f docs/BRAIN_REFERENCE.md

if grep -RInEi "/home/ubuntu|cloud-assistant-core|assistant-core|(^|[^A-Za-z0-9_])sanju([^A-Za-z0-9_]|$)|100\.[0-9]+\.[0-9]+\.[0-9]+" config/fleet integrations/hermes integrations/vellum docs/COMPATIBILITY.md; then
    echo "BRAIN_REFERENCE_SMOKE: FAIL private reference"
    exit 1
fi

# Runtime integration code must remain model-agnostic. Concrete model
# preferences belong only in Fleet policy, never in Hermes/Vellum glue.
if grep -RInEi "meta/llama-[A-Za-z0-9._:-]+|nvidia/[A-Za-z0-9._:-]+|qwen/[A-Za-z0-9._:-]+|deepseek/[A-Za-z0-9._:-]+|mistral[A-Za-z0-9._:/-]*" integrations/hermes integrations/vellum; then
    echo "BRAIN_REFERENCE_SMOKE: FAIL concrete integration model"
    exit 1
fi

# Routing V1 must be fully model-dynamic: profiles carry generic
# weighting rules only, never concrete model IDs. openrouter/free is
# the one stable exact escape route (provider-managed, always last).
python3 - <<'PY_POLICY'
import json
from pathlib import Path

policy = json.loads(
    Path("config/fleet/hermes-fleet-policy.json").read_text()
)

violations = []


def walk(value, path=()):
    if isinstance(value, dict):
        for key, item in value.items():
            child = path + (str(key),)

            if key == "model" and isinstance(item, str) and item.strip():
                model = item.strip()

                stable_escape = (
                    model == "openrouter/free"
                )

                if not stable_escape:
                    violations.append(
                        (".".join(child), model)
                    )

            walk(item, child)

    elif isinstance(value, list):
        for index, item in enumerate(value):
            walk(item, path + (str(index),))


walk(policy)

if violations:
    for location, model in violations:
        print(
            f"{location}: forbidden concrete model {model}"
        )

    raise SystemExit(
        "BRAIN_REFERENCE_SMOKE: FAIL concrete Fleet runtime model"
    )

if "preferredModels" in Path("config/fleet/hermes-fleet-policy.json").read_text():
    raise SystemExit(
        "BRAIN_REFERENCE_SMOKE: FAIL exact-model preference lists returned"
    )

print(
    "PASS: Fleet routing policy is model-dynamic "
    "(openrouter/free is the only stable exact route)"
)
PY_POLICY

grep -qF "diff --git a/agent/hermes_fleet_bridge.py b/agent/hermes_fleet_bridge.py" integrations/hermes/hermes-fleet-bridge.patch
grep -qF "__OPEN_CLOUD_HOME__" integrations/vellum/mcp-managed-blocks.py

echo "BRAIN_REFERENCE_SMOKE: PASS"
