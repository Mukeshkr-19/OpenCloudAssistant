#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

MODE="${1:---check}"
ALLOW_DIRTY="${OPEN_CLOUD_RELEASE_ALLOW_DIRTY:-0}"

static_checks() {

    echo "Open Cloud Assistant release static gate"

    ./scripts/public-audit.sh

    while IFS= read -r file; do
        bash -n "$file"
    done < <(
        {
            git ls-files "*.sh"
            git ls-files "bin/opencloud"
        } | sort -u
    )

    python3 -c "
import json
from pathlib import Path

for p in Path(\"config\").rglob(\"*.json\"):
    json.loads(p.read_text())

for p in Path(\".\").rglob(\"*.py\"):
    if \".git\" in p.parts:
        continue
    compile(p.read_text(), str(p), \"exec\")

print(\"SOURCE_SYNTAX: PASS\")
"

    python3 -c "
import yaml
from pathlib import Path

for p in Path(\".github/workflows\").glob(\"*.yml\"):
    value = yaml.safe_load(p.read_text())
    assert isinstance(value, dict)

print(\"WORKFLOW_YAML: PASS\")
"

    for file in \
        LICENSE \
        THIRD_PARTY_NOTICES.md \
        licenses/HERMES_AGENT_LICENSE.txt \
        licenses/VELLUM_ASSISTANT_LICENSE.txt \
        licenses/OPENCODE_LICENSE.txt
    do
        test -s "$file"
    done

    grep -qF "Hermes Git HEAD:" docs/COMPATIBILITY.md

    echo "LICENSE_AND_COMPATIBILITY: PASS"
    echo "RELEASE_STATIC_GATE: PASS"
}

case "$MODE" in

    --static)

        static_checks
        ;;

    --check)

        if [ "$ALLOW_DIRTY" != "1" ] && \
           [ -n "$(git status --porcelain)" ]; then

            echo "RELEASE_GATE: FAIL"
            echo "Repository must be clean before release validation."
            exit 1
        fi

        static_checks

        ./tests/smoke/run.sh

        OPEN_CLOUD_HERMES_ROOT="${OPEN_CLOUD_HERMES_ROOT:-$HOME/.hermes/hermes-agent}" \
            ./tests/reliability/run.sh

        TMP="$(mktemp -d)"
        trap "rm -rf \"$TMP\"" EXIT

        mkdir -p "$TMP/home"

        BASE_PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"

        HOME="$TMP/home" \
        PATH="$BASE_PATH" \
            ./setup.sh --dry-run \
            > "$TMP/setup.log" 2>&1

        grep -qF "SETUP_DRY_RUN: PASS" "$TMP/setup.log"

        echo "CLEAN_HOME_DRY_RUN_GATE: PASS"

        if [ "${OPEN_CLOUD_RELEASE_LIVE:-0}" = "1" ]; then
            ./bin/opencloud doctor
            echo "LIVE_DOCTOR_GATE: PASS"
        else
            echo "LIVE_DOCTOR_GATE: SKIP"
        fi

        echo
        echo "Validated release baseline:"
        echo "  Ubuntu 24.04 ARM64 real clean-install path"
        echo "  x86_64 CI/source compatibility"
        echo "  CLI operator path"
        echo "  deterministic Fleet reliability tests"
        echo "  bounded Hermes worker concurrency"
        echo "  self-repair validation and rollback"
        echo "  service persistence configuration"
        echo
        echo "Explicit limitations:"
        echo "  x86_64 real-machine acceptance is deferred"
        echo "  optional messaging adapters are not implied to be E2E validated"
        echo "  browser integration remains preview unless separately validated"

        echo
        echo "RELEASE_GATE: PASS"
        ;;

    -h|--help|help)

        echo "Usage:"
        echo "  opencloud release check"
        echo "  scripts/release-check.sh --static"
        ;;

    *)

        echo "ERROR: unsupported release-check mode: $MODE" >&2
        exit 2
        ;;
esac
