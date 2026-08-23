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

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
FAKE_HOME="$TMP/home"
FLEET="$TMP/non-default-fleet"
# Resolve yaml-capable Python before faking HOME (install/70 defaults to
# $HOME/.hermes/.../venv which would miss under FAKE_HOME). Prefer explicit
# override, then system python3, then a real-home Hermes venv if present.
SMOKE_PY="${OPEN_CLOUD_HERMES_PYTHON:-}"
if [ -z "$SMOKE_PY" ] && python3 -c 'import yaml' 2>/dev/null; then
    SMOKE_PY="$(command -v python3)"
fi
if [ -z "$SMOKE_PY" ] && [ -x "${HOME}/.hermes/hermes-agent/venv/bin/python" ]; then
    SMOKE_PY="${HOME}/.hermes/hermes-agent/venv/bin/python"
fi
if [ -z "$SMOKE_PY" ]; then
    SMOKE_PY="$(command -v python3)"
fi
HOME="$FAKE_HOME" OPEN_CLOUD_FLEET_HOME="$FLEET" OPEN_CLOUD_HERMES_PYTHON="$SMOKE_PY" \
    install/70-fleet-runtime.sh --install
KEY="$FLEET/session-pin.key"
test -f "$KEY"
test "$(wc -c < "$KEY")" -ge 32
MODE="$(stat -c %a "$KEY" 2>/dev/null || stat -f %Lp "$KEY")"
test "$MODE" = 600
BEFORE="$(shasum -a 256 "$KEY")"
HOME="$FAKE_HOME" OPEN_CLOUD_FLEET_HOME="$FLEET" OPEN_CLOUD_HERMES_PYTHON="$SMOKE_PY" \
    install/70-fleet-runtime.sh --install
test "$BEFORE" = "$(shasum -a 256 "$KEY")"
echo "PASS Fleet session pin key is secure and idempotent"

HOME="$FAKE_HOME" OPEN_CLOUD_FLEET_HOME="$FLEET" python3 - "$ROOT/integrations/hermes/hermes-fleet-bridge.patch" "$FLEET" <<'PY'
import importlib.util, sqlite3, sys, tempfile
from pathlib import Path
lines=Path(sys.argv[1]).read_text().splitlines(); start=next(i for i,x in enumerate(lines) if x.startswith("@@"))+1
source="\n".join(x[1:] for x in lines[start:] if x.startswith("+") and not x.startswith("+++"))+"\n"
with tempfile.TemporaryDirectory() as tmp:
    path=Path(tmp)/"bridge.py"; path.write_text(source)
    spec=importlib.util.spec_from_file_location("bridge", path); bridge=importlib.util.module_from_spec(spec); spec.loader.exec_module(bridge)
    assert bridge.ROOT == Path(sys.argv[2]).resolve()
    class Fleet:
        db=sqlite3.connect(":memory:")
    fleet=Fleet(); candidate={"candidateKey":"example:model","providerGroup":"example","provider":"example","model":"model"}
    bridge._set_pin(fleet, "main", "synthetic-session", candidate)
    assert bridge._get_pin(fleet, "main", "synthetic-session") == bridge._key(candidate)
    bridge._clear_pin(fleet, "main", "synthetic-session")
    assert bridge._get_pin(fleet, "main", "synthetic-session") is None
PY
echo "PASS Fleet pin set/get/clear uses provisioned key"
test -f "$FLEET/fleet_runtime.py"
echo "PASS non-default OPEN_CLOUD_FLEET_HOME is shared by runtime and bridge"

HELP_OUTPUT="$(bin/opencloud help)"
[[ "$HELP_OUTPUT" == *"opencloud fleet status"* ]]
bin/opencloud fleet paths >/dev/null

echo "FLEET_INSTALL_SMOKE: PASS"
