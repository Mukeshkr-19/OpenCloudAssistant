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

if grep -RInEi "meta/llama-[A-Za-z0-9._:-]+|nvidia/[A-Za-z0-9._:-]+|qwen/[A-Za-z0-9._:-]+|deepseek/[A-Za-z0-9._:-]+|mistral[A-Za-z0-9._:/-]*" config/fleet integrations/hermes integrations/vellum; then
    echo "BRAIN_REFERENCE_SMOKE: FAIL concrete model"
    exit 1
fi

grep -qF "__OPEN_CLOUD_HOME__" integrations/hermes/hermes-fleet-bridge.patch
grep -qF "__OPEN_CLOUD_HOME__" integrations/vellum/mcp-managed-blocks.py

echo "BRAIN_REFERENCE_SMOKE: PASS"
