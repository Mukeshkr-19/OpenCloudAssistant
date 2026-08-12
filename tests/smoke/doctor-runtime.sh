#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

echo "Open Cloud Assistant runtime doctor smoke test"

TMP="$(mktemp -d)"
trap "rm -rf \"$TMP\"" EXIT

H="$TMP/home"

mkdir -p \
    "$H/.opencloud" \
    "$H/.local/share/hermes-fleet/registry" \
    "$H/.hermes" \
    "$H/.config/hermes-vellum/mcp" \
    "$H/.local/bin"

chmod 700 "$H/.opencloud"

printf "%s\n" "NVIDIA_API_KEY=" \
    > "$H/.opencloud/config.env"

chmod 600 "$H/.opencloud/config.env"

printf "%s\n" "{\"version\":1,\"selected\":[\"cli\"]}" \
    > "$H/.opencloud/channels.json"

printf "%s\n" "{\"version\":1}" \
    > "$H/.local/share/hermes-fleet/fleet.json"

printf "%s\n" "{}" \
    > "$H/.local/share/hermes-fleet/registry/models.json"

python3 -c "import sqlite3,sys; c=sqlite3.connect(sys.argv[1]); c.execute(\"create table if not exists health(x integer)\"); c.commit(); c.close()" \
    "$H/.local/share/hermes-fleet/health.sqlite"

printf "%s\n" "delegation:" "  max_concurrent_children: 3" \
    > "$H/.hermes/config.yaml"

printf "%s\n" "def main():" "    return 0" \
    > "$H/.config/hermes-vellum/mcp/server.py"

printf "%s\n" "#!/usr/bin/env python3" "raise SystemExit(0)" \
    > "$H/.config/hermes-vellum/mcp/worker.py"
chmod 755 "$H/.config/hermes-vellum/mcp/worker.py"

printf "%s\n" "#!/usr/bin/env bash" "exit 0" \
    > "$H/.local/bin/hermes-code-repair"

chmod 755 "$H/.local/bin/hermes-code-repair"

OPEN_CLOUD_HOME="$H" \
    scripts/doctor-runtime.sh

chmod 644 "$H/.opencloud/config.env"

set +e

OUT="$(
    OPEN_CLOUD_HOME="$H" \
        scripts/doctor-runtime.sh 2>&1
)"

RC=$?

set -e

printf "%s\n" "$OUT"

[ "$RC" -ne 0 ]
[[ "$OUT" == *"expected mode 600"* ]]
[[ "$OUT" == *"RUNTIME_DOCTOR: FAIL"* ]]

echo "DOCTOR_RUNTIME_SMOKE: PASS"
