#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

echo "Open Cloud Assistant Fleet installer smoke test"

test -x install/70-fleet-runtime.sh
test -x scripts/fleet-status.sh
test -x scripts/doctor-fleet.sh

bash -n install/70-fleet-runtime.sh
bash -n scripts/fleet-status.sh
bash -n scripts/doctor-fleet.sh

install/70-fleet-runtime.sh --check

bin/opencloud help | grep -qF "opencloud fleet status"
bin/opencloud fleet paths >/dev/null

echo "FLEET_INSTALL_SMOKE: PASS"
