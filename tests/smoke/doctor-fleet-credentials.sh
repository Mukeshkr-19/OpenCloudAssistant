#!/usr/bin/env bash
# Doctor must treat Hermes .env provider keys as configured (gateway/Fleet
# resolve credentials there even when ~/.opencloud/config.env lacks them).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

echo "Open Cloud Assistant doctor-fleet credential source smoke"

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

H="$TMP/home"
mkdir -p "$H/.opencloud" "$H/.hermes" "$H/.local/share/hermes-fleet/registry"
chmod 700 "$H/.opencloud"

# Empty OpenCloud provider file (mode 600) — previously caused false SKIP.
: > "$H/.opencloud/config.env"
chmod 600 "$H/.opencloud/config.env"

# Minimal fleet artifacts so doctor-fleet progresses past install checks.
printf '%s\n' '{"version":1}' > "$H/.local/share/hermes-fleet/fleet.json"
printf '%s\n' '{"version":1,"productionModels":{"nvidia":["meta/example"],"zen":[]},"providerStatus":{"openrouter":{"ok":true}},"lastVerificationRunMs":1}' \
    > "$H/.local/share/hermes-fleet/registry/models.json"
# session-pin.key must be >= 32 bytes for doctor-fleet
python3 -c "open('$H/.local/share/hermes-fleet/session-pin.key','wb').write(b'0'*32)"
chmod 600 "$H/.local/share/hermes-fleet/session-pin.key"
install -m 755 /dev/null "$H/.local/share/hermes-fleet/dispatcher.py" 2>/dev/null || {
    printf '%s\n' 'pass' > "$H/.local/share/hermes-fleet/dispatcher.py"
}
mkdir -p "$H/.local/share/hermes-fleet/registry"
printf '%s\n' 'pass' > "$H/.local/share/hermes-fleet/registry/refresh.py"
printf '%s\n' 'pass' > "$H/.local/share/hermes-fleet/registry/verify.py"

# Hermes runtime keys present (values are synthetic placeholders).
printf '%s\n' \
    'NVIDIA_API_KEY=synthetic-nvidia-key-for-doctor-smoke' \
    'OPENROUTER_API_KEY=synthetic-openrouter-key-for-doctor-smoke' \
    > "$H/.hermes/.env"
chmod 600 "$H/.hermes/.env"

set +e
OUT="$(
    HOME="$H" \
    OPEN_CLOUD_CONFIG="$H/.opencloud/config.env" \
    OPEN_CLOUD_FLEET_HOME="$H/.local/share/hermes-fleet" \
    HERMES_HOME="$H/.hermes" \
        bash "$ROOT/scripts/doctor-fleet.sh" 2>&1
)"
RC=$?
set -e

printf '%s\n' "$OUT"
[ "$RC" -eq 0 ]
echo "$OUT" | grep -q 'PASS  NVIDIA provider'
echo "$OUT" | grep -q 'PASS  OpenRouter provider'
echo "$OUT" | grep -vq 'SKIP  NVIDIA provider'
echo "$OUT" | grep -vq 'SKIP  OpenRouter provider'

# providers status should agree
PSTATUS="$(
    HOME="$H" \
    OPEN_CLOUD_CONFIG="$H/.opencloud/config.env" \
    HERMES_HOME="$H/.hermes" \
        bash "$ROOT/scripts/providers.sh" status 2>&1
)"
printf '%s\n' "$PSTATUS"
echo "$PSTATUS" | grep -q 'NVIDIA:      CONFIGURED'
echo "$PSTATUS" | grep -q 'OpenRouter:   CONFIGURED'

echo "DOCTOR_FLEET_CREDENTIALS_SMOKE: PASS"
