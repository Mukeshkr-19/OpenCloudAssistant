#!/usr/bin/env bash
set -euo pipefail

export PATH="$HOME/.local/bin:$HOME/.bun/bin:$HOME/bin:$PATH"

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BASE="${OPEN_CLOUD_FLEET_HOME:-$HOME/.local/share/hermes-fleet}"
REGISTRY="$BASE/registry"
SRC_REFRESH="$ROOT/integrations/fleet/registry/refresh.py"
SRC_VERIFY="$ROOT/integrations/fleet/registry/verify.py"
SRC_RUNTIME="$ROOT/integrations/fleet/fleet_runtime.py"
MODE="${1:---help}"

render_file() {
    local src="$1"
    local dst="$2"

    python3 -c "from pathlib import Path; import os,sys; p=Path(sys.argv[1]); q=Path(sys.argv[2]); q.write_text(p.read_text().replace(\"__OPEN_CLOUD_HOME__\", os.path.expanduser(\"~\")))" "$src" "$dst"
}

check_sources() {
    test -f "$SRC_REFRESH"
    test -f "$SRC_VERIFY"
    test -f "$SRC_RUNTIME"

    local tmp
    tmp="$(mktemp -d)"

    render_file "$SRC_REFRESH" "$tmp/refresh.py"
    render_file "$SRC_VERIFY" "$tmp/verify.py"
    cp "$SRC_RUNTIME" "$tmp/fleet_runtime.py"

    python3 -m py_compile "$tmp/refresh.py" "$tmp/verify.py" "$tmp/fleet_runtime.py"

    rm -rf "$tmp"

    echo "FLEET_REGISTRY_INSTALL_CHECK: PASS"
}

case "$MODE" in
    --check)
        check_sources
        ;;
    --install)
        check_sources

        mkdir -p "$REGISTRY"

        tmp="$(mktemp -d)"

        render_file "$SRC_REFRESH" "$tmp/refresh.py"
        render_file "$SRC_VERIFY" "$tmp/verify.py"

        install -m 755 "$tmp/refresh.py" "$REGISTRY/refresh.py"
        install -m 755 "$tmp/verify.py" "$REGISTRY/verify.py"
        install -m 644 "$SRC_RUNTIME" "$BASE/fleet_runtime.py"

        rm -rf "$tmp"

        echo "FLEET_REGISTRY_INSTALL: PASS"
        echo "Existing models.json is preserved."
        ;;
    -h|--help|help)
        echo "Usage:"
        echo "  $0 --check"
        echo "  $0 --install"
        ;;
    *)
        echo "ERROR: unknown mode: $MODE" >&2
        exit 2
        ;;
esac
