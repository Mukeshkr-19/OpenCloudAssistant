#!/usr/bin/env bash
# Regression: reliability Hermes prep must be hermetic.
# HOME gets an intentionally UNPATCHED fake Hermes; OPEN_CLOUD_HERMES_ROOT unset.
# After prep, root is NOT $HOME/.hermes/hermes-agent and carries Routing V1 markers.
# Explicit OPEN_CLOUD_HERMES_ROOT override is respected.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

PIN="$(
    sed -n 's/^HERMES_BASELINE_REV="\([^"]*\)"/\1/p' \
        install/35-hermes-live.sh | head -n1
)"
test -n "$PIN"

FAKE_HOME="$(mktemp -d "${TMPDIR:-/tmp}/oca-hermes-fixture-home.XXXXXX")"
cleanup_fake() {
    python3 -c \
        "import shutil,sys; shutil.rmtree(sys.argv[1], ignore_errors=True)" \
        "$FAKE_HOME"
}
trap cleanup_fake EXIT

# Intentionally unpatched stub — no Routing V1 markers, no pin object.
mkdir -p "$FAKE_HOME/.hermes/hermes-agent/cron"
printf '%s\n' '# unpatched fake hermes for reliability fixture regression' \
    > "$FAKE_HOME/.hermes/hermes-agent/cron/scheduler.py"
git init -q "$FAKE_HOME/.hermes/hermes-agent"
git -C "$FAKE_HOME/.hermes/hermes-agent" \
    -c user.email=fixture@example.com \
    -c user.name=fixture \
    commit --allow-empty -q -m "fake unpatched hermes"

# Object store: real pin objects (not used as reliability root). Prefer a local
# store so the regression stays offline when the developer tree has the pin.
OBJECT_SOURCE=""
if [ -d "${HOME}/.hermes/hermes-agent/.git" ] && \
   git -C "${HOME}/.hermes/hermes-agent" cat-file -e "${PIN}^{commit}" 2>/dev/null; then
    OBJECT_SOURCE="${HOME}/.hermes/hermes-agent"
fi

(
    unset OPEN_CLOUD_HERMES_ROOT OPEN_CLOUD_HERMES_PYTHON
    export HOME="$FAKE_HOME"
    export ROOT
    # Real object store path (outside fake HOME) so prep can clone the pin.
    if [ -n "$OBJECT_SOURCE" ]; then
        export OPEN_CLOUD_HERMES_OBJECT_SOURCE="$OBJECT_SOURCE"
    fi
    # shellcheck source=tests/reliability/prepare-hermes.sh
    source "$ROOT/tests/reliability/prepare-hermes.sh"
    prepare_opencloud_reliability_hermes

    personal="$FAKE_HOME/.hermes/hermes-agent"
    if [ "$(cd "$OPEN_CLOUD_HERMES_ROOT" && pwd)" = "$(cd "$personal" && pwd)" ]; then
        echo "ERROR: prepared root is personal Hermes under fake HOME" >&2
        exit 1
    fi
    case "$OPEN_CLOUD_HERMES_ROOT" in
        "$personal"|"$personal"/*)
            echo "ERROR: prepared root nested under personal Hermes path" >&2
            exit 1
            ;;
    esac

    grep -qF "HERMES_ROUTING_V1_CRON_PROFILE_PRELUDE" \
        "$OPEN_CLOUD_HERMES_ROOT/cron/scheduler.py"
    grep -qF "HERMES_ROUTING_V1_CRON_PROFILE" \
        "$OPEN_CLOUD_HERMES_ROOT/cron/scheduler.py"

    echo "HERMES_FIXTURE_PREP: auto-root ok ($OPEN_CLOUD_HERMES_ROOT)"
)

# Explicit override must be respected (already-prepared tree).
OVERRIDE="$(mktemp -d "${TMPDIR:-/tmp}/oca-hermes-override.XXXXXX")"
cleanup_override() {
    cleanup_fake
    python3 -c \
        "import shutil,sys; shutil.rmtree(sys.argv[1], ignore_errors=True)" \
        "$OVERRIDE"
}
trap cleanup_override EXIT

if [ -z "$OBJECT_SOURCE" ]; then
    echo "HERMES_FIXTURE_PREP: SKIP explicit-override clone (no local pin object store; auto-root already covered)"
else
    git clone -q --no-checkout "$OBJECT_SOURCE" "$OVERRIDE/hermes"
    git -C "$OVERRIDE/hermes" config maintenance.auto false
    git -C "$OVERRIDE/hermes" checkout -q --detach "$PIN"
    OPEN_CLOUD_HOME="$OVERRIDE/home" OPEN_CLOUD_HERMES_ROOT="$OVERRIDE/hermes" \
        ./install/35-hermes-live.sh --install >/dev/null

    (
        export HOME="$FAKE_HOME"
        export ROOT
        export OPEN_CLOUD_HERMES_ROOT="$OVERRIDE/hermes"
        unset OPEN_CLOUD_HERMES_PYTHON
        # shellcheck source=tests/reliability/prepare-hermes.sh
        source "$ROOT/tests/reliability/prepare-hermes.sh"
        prepare_opencloud_reliability_hermes
        if [ "$(cd "$OPEN_CLOUD_HERMES_ROOT" && pwd)" != "$(cd "$OVERRIDE/hermes" && pwd)" ]; then
            echo "ERROR: explicit OPEN_CLOUD_HERMES_ROOT was not respected" >&2
            exit 1
        fi
        echo "HERMES_FIXTURE_PREP: explicit override ok"
    )
fi

echo "HERMES_FIXTURE_PREP: PASS"
