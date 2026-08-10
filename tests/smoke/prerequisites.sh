#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

echo "Open Cloud Assistant prerequisite bootstrap smoke test"

install/00-preflight.sh --check

OUT="$(
    OPEN_CLOUD_PREFLIGHT_TEST_MISSING="rsync python3-yaml" \
        install/00-preflight.sh --dry-run
)"

printf "%s\n" "$OUT"

[[ "$OUT" == *"WOULD_INSTALL prerequisite: rsync"* ]]
[[ "$OUT" == *"WOULD_INSTALL prerequisite: python3-yaml"* ]]
[[ "$OUT" == *"PREFLIGHT_STATUS: PASS"* ]]

OUT="$(
    OPEN_CLOUD_PREFLIGHT_TEST_MODE=1 \
    OPEN_CLOUD_PREFLIGHT_TEST_MISSING="rsync python3-yaml" \
        install/00-preflight.sh --install
)"

printf "%s\n" "$OUT"

[[ "$OUT" == *"PREFLIGHT_TEST_INSTALL:"* ]]
[[ "$OUT" == *"rsync"* ]]
[[ "$OUT" == *"python3-yaml"* ]]
[[ "$OUT" == *"PREFLIGHT_STATUS: PASS"* ]]

grep -qF "\"\$ROOT/install/00-preflight.sh\" --dry-run" setup.sh
grep -qF "\"\$ROOT/install/00-preflight.sh\" --install" setup.sh

echo "PREREQUISITE_BOOTSTRAP_SMOKE: PASS"
