#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

echo "Open Cloud Assistant self-repair smoke test"

test -x integrations/self-repair/hermes-code-repair
test -f integrations/self-repair/hermes-repair-agent.md
test -x install/60-self-repair.sh

bash -n integrations/self-repair/hermes-code-repair
bash -n install/60-self-repair.sh

grep -qF '"*": deny' integrations/self-repair/hermes-repair-agent.md
grep -qF "read: allow" integrations/self-repair/hermes-repair-agent.md
grep -qF "edit: allow" integrations/self-repair/hermes-repair-agent.md
grep -qF "bash: deny" integrations/self-repair/hermes-repair-agent.md
grep -qF "task: deny" integrations/self-repair/hermes-repair-agent.md
grep -qF "webfetch: deny" integrations/self-repair/hermes-repair-agent.md
grep -qF "websearch: deny" integrations/self-repair/hermes-repair-agent.md
grep -qF "external_directory: deny" integrations/self-repair/hermes-repair-agent.md

grep -qF "opencode run" integrations/self-repair/hermes-code-repair
grep -qF -- "--agent" integrations/self-repair/hermes-code-repair
grep -qF -- "--dir" integrations/self-repair/hermes-code-repair

if grep -RInEi "/home/ubuntu|cloud-assistant-core|assistant-core|(^|[^A-Za-z0-9_])sanju([^A-Za-z0-9_]|$)|100\.[0-9]+\.[0-9]+\.[0-9]+" \
    integrations/self-repair install/60-self-repair.sh
then
    echo "SELF_REPAIR_PUBLIC_SMOKE: FAIL private deployment reference"
    exit 1
fi

echo "SMOKE: repair bridge contract"

REPAIR_HELP="$(integrations/self-repair/hermes-code-repair --help)"

[[ "$REPAIR_HELP" == *'--task "describe the repair"'* ]]

for bridge in \
    integrations/vellum/server.py \
    integrations/vellum/mcp-managed-blocks.py
do
    test "$(grep -c '^def repair_code' "$bridge")" -eq 1

    grep -qF 'if target != "hermes":' "$bridge"
    grep -qF '"--task",' "$bridge"

    if grep -qF "input=task" "$bridge"; then
        echo "SELF_REPAIR_PUBLIC_SMOKE: FAIL stale stdin repair contract in $bridge"
        exit 1
    fi
done

integrations/self-repair/hermes-code-repair --self-test

echo "SELF_REPAIR_PUBLIC_SMOKE: PASS"
