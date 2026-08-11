#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

echo "Open Cloud Assistant operational evidence smoke test"

test -x scripts/collect-operational-evidence.sh
test -f docs/evidence/README.md

bash -n scripts/collect-operational-evidence.sh

TMP="$(mktemp -d)"
trap "rm -rf \"$TMP\"" EXIT

OPEN_CLOUD_EVIDENCE_TEST_MODE=1     ./scripts/collect-operational-evidence.sh         --output "$TMP/snapshot.md"         --append-history "$TMP/history.md"

test -s "$TMP/snapshot.md"
test -s "$TMP/history.md"

grep -qF "Operational Evidence Snapshot" "$TMP/snapshot.md"
grep -qF "Synthetic smoke-test fixture" "$TMP/snapshot.md"
grep -qF "OpenCloud doctor: PASS" "$TMP/snapshot.md"
grep -qF "Successful scheduled-job completions observed" "$TMP/snapshot.md"
grep -qF "These values are observations, not an uptime SLA or SLO." "$TMP/snapshot.md"

grep -qF "Operational Evidence History" "$TMP/history.md"
grep -qF "aarch64" "$TMP/history.md"

if grep -Eq     "(/home/|ocid1\.|100\.[0-9]+\.[0-9]+\.[0-9]+|([0-9]{1,3}\.){3}[0-9]{1,3}|API[_-]?KEY|TOKEN=|SECRET=|PRIVATE KEY|cron_[A-Za-z0-9]|CloudMind|Carbontrace|H1KARI)"     "$TMP/snapshot.md" "$TMP/history.md"
then
    echo "OPERATIONAL_EVIDENCE_SMOKE: FAIL privacy guard" >&2
    exit 1
fi

echo "OPERATIONAL_EVIDENCE_SMOKE: PASS"
