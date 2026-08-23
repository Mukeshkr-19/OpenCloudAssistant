#!/usr/bin/env bash
# Canonical Hermes preparation for deterministic reliability.
# Matches .github/workflows/reliability.yml: pinned baseline + install/35 patches.
#
# Usage (sourced by run.sh):
#   source tests/reliability/prepare-hermes.sh
#   prepare_opencloud_reliability_hermes
#
# Rules:
#   - If OPEN_CLOUD_HERMES_ROOT is set → use it (must contain pinned commit object).
#   - Else → materialize a temp tree from the pin + install/35 --install.
#   - NEVER silently use $HOME/.hermes/hermes-agent as the reliability root.
#   - Prep failure is fatal (no personal-Hermes fallback).
#   - Temp trees are cleaned via OPEN_CLOUD_RELIABILITY_HERMES_TMP; never delete ~/.hermes.
set -euo pipefail

_opencloud_reliability_pin() {
    local pin
    pin="$(
        sed -n 's/^HERMES_BASELINE_REV="\([^"]*\)"/\1/p' \
            "$ROOT/install/35-hermes-live.sh" | head -n1
    )"
    if [ -z "$pin" ]; then
        echo "ERROR: HERMES_BASELINE_REV missing from install/35-hermes-live.sh" >&2
        return 1
    fi
    printf '%s\n' "$pin"
}

_opencloud_reliability_cleanup() {
    # Bash inherits EXIT traps into $(...) subshells; only the top-level
    # reliability shell may delete the temp tree mid-suite.
    if [ "${BASH_SUBSHELL:-0}" -ne 0 ]; then
        return 0
    fi
    if [ -n "${OPEN_CLOUD_RELIABILITY_HERMES_TMP:-}" ] && \
       [ -d "$OPEN_CLOUD_RELIABILITY_HERMES_TMP" ]; then
        # ponytail: shutil beats rm -rf edge cases on partial clones
        python3 -c \
            "import shutil,sys; shutil.rmtree(sys.argv[1], ignore_errors=True)" \
            "$OPEN_CLOUD_RELIABILITY_HERMES_TMP"
    fi
}

_opencloud_reliability_python() {
    # Match CI: ephemeral venv + Hermes-pinned httpx. Prefer explicit override.
    # Do not default to ~/.hermes/hermes-agent/venv.
    if [ -n "${OPEN_CLOUD_HERMES_PYTHON:-}" ]; then
        if [ ! -x "$OPEN_CLOUD_HERMES_PYTHON" ]; then
            echo "ERROR: OPEN_CLOUD_HERMES_PYTHON is not executable: $OPEN_CLOUD_HERMES_PYTHON" >&2
            return 1
        fi
        HERMES_PYTHON="$OPEN_CLOUD_HERMES_PYTHON"
        export OPEN_CLOUD_HERMES_PYTHON HERMES_PYTHON
        return 0
    fi

    local venv_parent venv_dir reliability_python hermes_httpx
    if [ -n "${OPEN_CLOUD_RELIABILITY_HERMES_TMP:-}" ]; then
        venv_parent="$OPEN_CLOUD_RELIABILITY_HERMES_TMP"
    else
        OPEN_CLOUD_RELIABILITY_HERMES_TMP="$(
            mktemp -d "${TMPDIR:-/tmp}/oca-reliability-hermes.XXXXXX"
        )"
        export OPEN_CLOUD_RELIABILITY_HERMES_TMP
        trap _opencloud_reliability_cleanup EXIT
        venv_parent="$OPEN_CLOUD_RELIABILITY_HERMES_TMP"
    fi
    venv_dir="$venv_parent/reliability-venv"
    python3 -m venv --system-site-packages "$venv_dir"
    reliability_python="$venv_dir/bin/python"

    # CI installs httpx from the captured Hermes pyproject; YAML comes from
    # apt on Ubuntu. Locally ensure both so plain scripts do not need a
    # developer live venv.
    if ! "$reliability_python" -c "import yaml" 2>/dev/null; then
        "$reliability_python" -m pip -q install "pyyaml>=6"
    fi

    hermes_httpx="$(
        "$reliability_python" -c '
import sys, tomllib
dependencies = tomllib.load(open(sys.argv[1], "rb"))["project"]["dependencies"]
matches = [
    item for item in dependencies
    if item.lower().startswith(("httpx==", "httpx["))
]
assert len(matches) == 1 and "==" in matches[0], "captured Hermes must exactly pin httpx"
print(matches[0])
' "$OPEN_CLOUD_HERMES_ROOT/pyproject.toml"
    )"
    "$reliability_python" -m pip -q install "$hermes_httpx"
    "$reliability_python" -c '
import importlib.metadata, sys
expected = sys.argv[1].split("==", 1)[1].split(";", 1)[0].strip()
assert importlib.metadata.version("httpx") == expected
' "$hermes_httpx"

    HERMES_PYTHON="$reliability_python"
    OPEN_CLOUD_HERMES_PYTHON="$reliability_python"
    export OPEN_CLOUD_HERMES_PYTHON HERMES_PYTHON
}

prepare_opencloud_reliability_hermes() {
    local pin hermes_tmp object_src candidate actual

    test -n "${ROOT:-}" || {
        echo "ERROR: ROOT must be set before prepare_opencloud_reliability_hermes" >&2
        return 1
    }

    pin="$(_opencloud_reliability_pin)"

    if [ -n "${OPEN_CLOUD_HERMES_ROOT:-}" ]; then
        if [ ! -d "$OPEN_CLOUD_HERMES_ROOT/.git" ]; then
            echo "ERROR: OPEN_CLOUD_HERMES_ROOT is not a Hermes Git tree: $OPEN_CLOUD_HERMES_ROOT" >&2
            return 1
        fi
        if ! git -C "$OPEN_CLOUD_HERMES_ROOT" cat-file -e "${pin}^{commit}" 2>/dev/null; then
            echo "ERROR: pinned Hermes baseline $pin missing under OPEN_CLOUD_HERMES_ROOT=$OPEN_CLOUD_HERMES_ROOT" >&2
            return 1
        fi
        OPEN_CLOUD_HERMES_ROOT="$(cd "$OPEN_CLOUD_HERMES_ROOT" && pwd)"
        export OPEN_CLOUD_HERMES_ROOT
        echo "RELIABILITY_HERMES: using explicit OPEN_CLOUD_HERMES_ROOT=$OPEN_CLOUD_HERMES_ROOT"
    else
        OPEN_CLOUD_RELIABILITY_HERMES_TMP="$(
            mktemp -d "${TMPDIR:-/tmp}/oca-reliability-hermes.XXXXXX"
        )"
        export OPEN_CLOUD_RELIABILITY_HERMES_TMP
        trap _opencloud_reliability_cleanup EXIT

        hermes_tmp="$OPEN_CLOUD_RELIABILITY_HERMES_TMP/hermes-agent"
        object_src=""

        # Optional object store only (clone + detach pin). Never use as root.
        for candidate in \
            "${OPEN_CLOUD_HERMES_OBJECT_SOURCE:-}" \
            "${HOME}/.hermes/hermes-agent"; do
            if [ -n "$candidate" ] && \
               [ -d "$candidate/.git" ] && \
               git -C "$candidate" cat-file -e "${pin}^{commit}" 2>/dev/null; then
                object_src="$candidate"
                break
            fi
        done

        if [ -n "$object_src" ]; then
            echo "RELIABILITY_HERMES: cloning pin $pin from local object store"
            git clone -q --no-checkout "$object_src" "$hermes_tmp"
            git -C "$hermes_tmp" config maintenance.auto false
            git -C "$hermes_tmp" checkout -q --detach "$pin"
        else
            echo "RELIABILITY_HERMES: fetching pin $pin from upstream (no local object store)"
            git init -q "$hermes_tmp"
            git -C "$hermes_tmp" remote add origin \
                https://github.com/NousResearch/hermes-agent.git
            if ! git -C "$hermes_tmp" fetch --depth=1 origin "$pin"; then
                echo "ERROR: failed to fetch Hermes baseline $pin; cannot run deterministic reliability" >&2
                return 1
            fi
            git -C "$hermes_tmp" checkout -q --detach FETCH_HEAD
        fi

        actual="$(git -C "$hermes_tmp" rev-parse HEAD)"
        if [ "$actual" != "$pin" ]; then
            echo "ERROR: checked out $actual but pinned baseline is $pin" >&2
            return 1
        fi

        # Reuse the existing ordered patch path — do not reimplement patches.
        OPEN_CLOUD_HOME="$OPEN_CLOUD_RELIABILITY_HERMES_TMP/home" \
        OPEN_CLOUD_HERMES_ROOT="$hermes_tmp" \
            "$ROOT/install/35-hermes-live.sh" --install

        # Guard: prepared root must never resolve to the personal live tree.
        if [ "$(cd "$hermes_tmp" && pwd)" = "$(cd "${HOME}/.hermes/hermes-agent" 2>/dev/null && pwd)" ]; then
            echo "ERROR: reliability Hermes root resolved to personal ~/.hermes/hermes-agent" >&2
            return 1
        fi

        OPEN_CLOUD_HERMES_ROOT="$hermes_tmp"
        export OPEN_CLOUD_HERMES_ROOT
        echo "RELIABILITY_HERMES: prepared OPEN_CLOUD_HERMES_ROOT=$OPEN_CLOUD_HERMES_ROOT"
    fi

    _opencloud_reliability_python

    "$HERMES_PYTHON" -c "import yaml"

    # Loud marker check so a silent unpatched tree cannot continue.
    if [ ! -f "$OPEN_CLOUD_HERMES_ROOT/cron/scheduler.py" ]; then
        echo "ERROR: cron/scheduler.py missing under $OPEN_CLOUD_HERMES_ROOT" >&2
        return 1
    fi
    if ! grep -qF "HERMES_ROUTING_V1_CRON_PROFILE_PRELUDE" \
            "$OPEN_CLOUD_HERMES_ROOT/cron/scheduler.py" || \
       ! grep -qF "HERMES_ROUTING_V1_CRON_PROFILE" \
            "$OPEN_CLOUD_HERMES_ROOT/cron/scheduler.py"; then
        echo "ERROR: Routing V1 cron markers missing under $OPEN_CLOUD_HERMES_ROOT (unpatched Hermes tree)" >&2
        return 1
    fi
}
