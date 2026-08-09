#!/usr/bin/env bash
set -euo pipefail

export PATH="$HOME/.local/bin:$HOME/.bun/bin:$HOME/bin:$PATH"

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SOURCE_DISPATCHER="$ROOT/integrations/fleet/dispatcher.py"
SOURCE_POLICY="$ROOT/config/fleet/hermes-fleet-policy.json"

RUNTIME_BASE="${OPEN_CLOUD_FLEET_HOME:-$HOME/.local/share/hermes-fleet}"
RUNTIME_DISPATCHER="$RUNTIME_BASE/dispatcher.py"
RUNTIME_POLICY="$RUNTIME_BASE/fleet.json"
RUNTIME_REGISTRY="$RUNTIME_BASE/registry/models.json"
RUNTIME_HEALTH="${HERMES_FLEET_HEALTH_DB:-$RUNTIME_BASE/health.sqlite}"

MODE="${1:---help}"

die() {
    echo "ERROR: $*" >&2
    exit 1
}

validate_sources() {
    [ -f "$SOURCE_DISPATCHER" ] || die "missing $SOURCE_DISPATCHER"
    [ -f "$SOURCE_POLICY" ] || die "missing $SOURCE_POLICY"
}

syntax_check() {
    python3 -c 'import ast,sys; ast.parse(open(sys.argv[1], encoding="utf-8").read())' "$SOURCE_DISPATCHER"
    python3 -m json.tool "$SOURCE_POLICY" >/dev/null
    python3 -c 'import yaml' >/dev/null 2>&1 || die "Python module yaml is required"
    python3 -c 'import json,sys; d=json.load(open(sys.argv[1])); p=d.get("pools",{}).get("openrouter-free",{}); assert p.get("type")=="stable-route"; assert p.get("provider")=="openrouter"; assert p.get("route")=="openrouter/free"' "$SOURCE_POLICY" || die "OpenRouter policy must remain openrouter/free"
}

isolated_check() {
    local tmp fake_home staged_base result
    tmp="$(mktemp -d)"
    fake_home="$tmp/home"
    staged_base="$fake_home/.local/share/hermes-fleet"

    mkdir -p "$staged_base/registry"

    sed "s#__OPEN_CLOUD_HOME__#$fake_home#g" "$SOURCE_DISPATCHER" > "$staged_base/dispatcher.py"
    cp "$SOURCE_POLICY" "$staged_base/fleet.json"
    printf '{}\n' > "$staged_base/registry/models.json"

    chmod 700 "$fake_home" "$staged_base" "$staged_base/registry"
    chmod 755 "$staged_base/dispatcher.py"
    chmod 644 "$staged_base/fleet.json"
    chmod 600 "$staged_base/registry/models.json"

    result="$(
        HOME="$fake_home" \
        HERMES_FLEET_HEALTH_DB="$tmp/health.sqlite" \
        python3 "$staged_base/dispatcher.py" select main
    )"

    python3 -c 'import json,sys; d=json.loads(sys.argv[1]); assert d.get("providerGroup")=="openrouter"; assert d.get("provider")=="openrouter"; assert d.get("model")=="openrouter/free"' "$result" || {
        rm -rf "$tmp"
        die "isolated bootstrap did not select OpenRouter free route"
    }

    HOME="$fake_home" \
    HERMES_FLEET_HEALTH_DB="$tmp/health.sqlite" \
    python3 "$staged_base/dispatcher.py" status >/dev/null

    [ -s "$tmp/health.sqlite" ] || {
        rm -rf "$tmp"
        die "isolated health DB was not created"
    }

    rm -rf "$tmp"
    echo "FLEET_RUNTIME_INSTALL_CHECK: PASS"
}

install_runtime() {
    install -d -m 700 "$RUNTIME_BASE" "$RUNTIME_BASE/registry"

    local tmp_dispatcher
    tmp_dispatcher="$(mktemp)"
    sed "s#__OPEN_CLOUD_HOME__#$HOME#g" "$SOURCE_DISPATCHER" > "$tmp_dispatcher"

    install -m 755 "$tmp_dispatcher" "$RUNTIME_DISPATCHER"
    install -m 644 "$SOURCE_POLICY" "$RUNTIME_POLICY"
    rm -f "$tmp_dispatcher"

    if [ ! -f "$RUNTIME_REGISTRY" ]; then
        printf '{}\n' > "$RUNTIME_REGISTRY"
    fi
    chmod 600 "$RUNTIME_REGISTRY"

    HERMES_FLEET_HEALTH_DB="$RUNTIME_HEALTH" python3 "$RUNTIME_DISPATCHER" status >/dev/null
    chmod 600 "$RUNTIME_HEALTH" 2>/dev/null || true

    echo "FLEET_RUNTIME_INSTALL: PASS"
    echo "Runtime: $RUNTIME_DISPATCHER"
    echo "Policy:  $RUNTIME_POLICY"
    echo "Registry: preserved at $RUNTIME_REGISTRY"
    echo "Health:   preserved at $RUNTIME_HEALTH"
}

validate_sources
syntax_check

case "$MODE" in
    --dry-run)
        echo "Open Cloud Assistant Fleet runtime dry run"
        echo "  source dispatcher: integrations/fleet/dispatcher.py"
        echo "  source policy:     config/fleet/hermes-fleet-policy.json"
        echo "  runtime base:      $RUNTIME_BASE"
        echo "  would install dispatcher and free-first policy"
        echo "  would create an empty registry only when no registry exists"
        echo "  would preserve existing runtime registry and health database"
        echo "  would not copy credentials"
        echo "  would not pin NVIDIA or Zen model IDs"
        echo "  Gemini remains blocked by the Hermes integration guard"
        echo "FLEET_RUNTIME_DRY_RUN: PASS"
        ;;
    --check)
        isolated_check
        ;;
    --install)
        install_runtime
        ;;
    -h|--help|help)
        echo "Usage:"
        echo "  install/70-fleet-runtime.sh --dry-run"
        echo "  install/70-fleet-runtime.sh --check"
        echo "  install/70-fleet-runtime.sh --install"
        ;;
    *)
        die "unknown option: $MODE"
        ;;
esac
