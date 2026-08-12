#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

if [ "$(uname -s)" != "Linux" ]; then
    echo "SELF_REPAIR_ROLLBACK_RELIABILITY: SKIP (Ubuntu/Linux bubblewrap and flock required)"
    exit 0
fi

HARNESS="$ROOT/integrations/self-repair/hermes-code-repair"
REAL_PYTHON="$(command -v python3)"
REAL_PATH="$PATH"

test -x "$HARNESS"
command -v rsync >/dev/null 2>&1
command -v timeout >/dev/null 2>&1
command -v sha256sum >/dev/null 2>&1
command -v flock >/dev/null 2>&1

TMP="$(mktemp -d)"
trap "rm -rf \"$TMP\"" EXIT

make_fixture() {
    local target="$1"

    mkdir -p "$target"

    printf "%s\n" \
        "def answer():" \
        "    return 42" \
        > "$target/example.py"

    printf "%s\n" \
        "#!/usr/bin/env bash" \
        "echo fixture-ok" \
        > "$target/check.sh"

    chmod 755 "$target/check.sh"
}

fixture_unchanged() {
    local target="$1"
    local expected="$2"

    cmp -s "$target/example.py" "$expected/example.py"
    cmp -s "$target/check.sh" "$expected/check.sh"
}

make_fake_tools() {
    local home="$1"
    local bin="$home/.local/bin"

    mkdir -p "$bin" "$home/.config/opencode/agents"

    printf "%s\n" \
        "---" \
        "description: deterministic repair fixture" \
        "---" \
        > "$home/.config/opencode/agents/hermes-repair.md"

    printf "%s\n" \
        "#!/usr/bin/env bash" \
        "set -euo pipefail" \
        "stage=\"\"" \
        "while [ \"\$#\" -gt 0 ]; do" \
        "    case \"\$1\" in" \
        "        --dir)" \
        "            shift" \
        "            stage=\"\${1:-}\"" \
        "            ;;" \
        "    esac" \
        "    shift || true" \
        "done" \
        "[ -n \"\$stage\" ] || exit 2" \
        "case \"\${OPEN_CLOUD_FAULT_MODE:-}\" in" \
        "    stage-invalid)" \
        "        printf \"%s\\n\" \"def broken(\" > \"\$stage/example.py\"" \
        "        ;;" \
        "    deploy-validation|stage-valid)" \
        "        printf \"%s\\n\" \"def answer():\" \"    return 99\" > \"\$stage/example.py\"" \
        "        ;;" \
        "    *)" \
        "        exit 2" \
        "        ;;" \
        "esac" \
        "exit 0" \
        > "$bin/opencode"

    chmod 755 "$bin/opencode"

    printf "%s\n" \
        "#!/usr/bin/env bash" \
        "set -euo pipefail" \
        "real=\"\${OPEN_CLOUD_REAL_PYTHON:?}\"" \
        "mode=\"\${OPEN_CLOUD_FAULT_MODE:-}\"" \
        "target=\"\${OPEN_CLOUD_HERMES_ROOT:-}\"" \
        "state=\"\${OPEN_CLOUD_REPAIR_STATE:-}\"" \
        "last=\"\"" \
        "for arg in \"\$@\"; do" \
        "    last=\"\$arg\"" \
        "done" \
        "if [ \"\$mode\" = \"deploy-validation\" ] && [ \"\$last\" = \"\$target/example.py\" ]; then" \
        "    counter=\"\$state/target-validation-count\"" \
        "    count=0" \
        "    if [ -f \"\$counter\" ]; then" \
        "        count=\"\$(cat \"\$counter\")\"" \
        "    fi" \
        "    count=\$((count + 1))" \
        "    printf \"%s\\n\" \"\$count\" > \"\$counter\"" \
        "    if [ \"\$count\" -eq 2 ]; then" \
        "        exit 1" \
        "    fi" \
        "fi" \
        "exec \"\$real\" \"\$@\"" \
        > "$bin/python3"

    chmod 755 "$bin/python3"
}

run_stage_validation_case() {
    local case_root="$TMP/stage-invalid"
    local home="$case_root/home"
    local target="$case_root/target"
    local expected="$case_root/expected"
    local state="$case_root/state"
    local log="$case_root/repair.log"

    mkdir -p "$home" "$expected" "$state"

    make_fixture "$target"
    cp -a "$target/." "$expected/"
    make_fake_tools "$home"

    set +e

    HOME="$home" \
    PATH="$REAL_PATH" \
    OPEN_CLOUD_HERMES_ROOT="$target" \
    OPEN_CLOUD_REPAIR_STATE="$state" \
    OPEN_CLOUD_REPAIR_TIMEOUT=10 \
    OPEN_CLOUD_REPAIR_SANDBOX_NETWORK=shared \
    OPEN_CLOUD_FAULT_MODE=stage-invalid \
    OPEN_CLOUD_REAL_PYTHON="$REAL_PYTHON" \
    "$HARNESS" \
        --task \
        "inject deterministic invalid staged Python" \
        > "$log" 2>&1

    local rc=$?

    set -e

    cat "$log"

    [ "$rc" -ne 0 ]

    grep -qF \
        "REPAIR: validating staged result" \
        "$log"

    grep -qF \
        "ERROR: staged repair failed validation" \
        "$log"

    fixture_unchanged \
        "$target" \
        "$expected"

    if find "$state/backups" \
        -mindepth 1 \
        -maxdepth 1 \
        -type d \
        -print -quit 2>/dev/null \
        | grep -q .
    then
        echo "FAIL: backup created before staged validation passed" >&2
        return 1
    fi

    echo "PASS staged validation rejects invalid repair"
    echo "PASS live target preserved before deployment"
}

run_deploy_rollback_case() {
    local case_root="$TMP/deploy-rollback"
    local home="$case_root/home"
    local target="$case_root/target"
    local expected="$case_root/expected"
    local state="$case_root/state"
    local log="$case_root/repair.log"
    local backup=""

    mkdir -p "$home" "$expected" "$state"

    make_fixture "$target"
    cp -a "$target/." "$expected/"
    make_fake_tools "$home"

    set +e

    HOME="$home" \
    PATH="$REAL_PATH" \
    OPEN_CLOUD_HERMES_ROOT="$target" \
    OPEN_CLOUD_REPAIR_STATE="$state" \
    OPEN_CLOUD_REPAIR_TIMEOUT=10 \
    OPEN_CLOUD_REPAIR_SANDBOX_NETWORK=shared \
    OPEN_CLOUD_FAULT_MODE=deploy-validation \
    OPEN_CLOUD_REAL_PYTHON="$REAL_PYTHON" \
    "$HARNESS" \
        --task \
        "inject deterministic deployment validation failure" \
        > "$log" 2>&1

    local rc=$?

    set -e

    cat "$log"

    [ "$rc" -ne 0 ]

    grep -qF \
        "REPAIR: creating trusted pre-deployment backup" \
        "$log"

    grep -qF \
        "REPAIR: deploying validated staged tree" \
        "$log"

    grep -qF \
        "ERROR: deployed tree failed validation" \
        "$log"

    grep -qF \
        "ROLLBACK: restoring pre-repair snapshot" \
        "$log"

    grep -qF \
        "ROLLBACK: PASS" \
        "$log"

    fixture_unchanged \
        "$target" \
        "$expected"

    backup="$(find "$state/backups" \
        -mindepth 1 \
        -maxdepth 1 \
        -type d \
        -print -quit)"

    [ -n "$backup" ]

    fixture_unchanged \
        "$backup" \
        "$expected"

    [ "$(cat "$state/target-validation-count")" -ge 3 ]

    echo "PASS trusted pre-deployment backup created"
    echo "PASS post-deployment validation failure injected"
    echo "PASS rollback path executed"
    echo "PASS original target restored byte-for-byte"
    echo "PASS backup matches original target"
}

run_lock_case() {
    local case_root="$TMP/lock"
    local state="$case_root/state"
    local target="$case_root/target"
    local home="$case_root/home"
    mkdir -p "$state" "$home"
    make_fixture "$target"
    make_fake_tools "$home"
    exec 8>"$state/repair.lock"
    flock -n 8
    set +e
    HOME="$home" PATH="$REAL_PATH" OPEN_CLOUD_HERMES_ROOT="$target" OPEN_CLOUD_REPAIR_STATE="$state" \
        OPEN_CLOUD_REAL_PYTHON="$REAL_PYTHON" \
        "$HARNESS" --task synthetic > "$case_root/log" 2>&1
    local rc=$?
    set -e
    [ "$rc" -ne 0 ]
    grep -qF "another repair transaction is already running" "$case_root/log"
    flock -u 8
    echo "PASS concurrent repair is rejected by host lock"
}

run_signal_case() {
    local case_root="$TMP/signal"
    local home="$case_root/home"
    local target="$case_root/target"
    local expected="$case_root/expected"
    local state="$case_root/state"
    local log="$case_root/log"
    mkdir -p "$home" "$expected" "$state"
    make_fixture "$target"
    cp -a "$target/." "$expected/"
    make_fake_tools "$home"
    HOME="$home" PATH="$REAL_PATH" OPEN_CLOUD_HERMES_ROOT="$target" \
        OPEN_CLOUD_REPAIR_STATE="$state" OPEN_CLOUD_REPAIR_TIMEOUT=10 \
        OPEN_CLOUD_REPAIR_SANDBOX_NETWORK=shared OPEN_CLOUD_FAULT_MODE=stage-valid \
        OPEN_CLOUD_REPAIR_DEPLOY_DELAY=30 OPEN_CLOUD_REAL_PYTHON="$REAL_PYTHON" \
        "$HARNESS" --task "synthetic signal repair" > "$log" 2>&1 &
    local pid=$!
    local tries=0
    while [ ! -f "$state/repair-in-progress" ] && [ "$tries" -lt 200 ]; do
        sleep 0.05
        tries=$((tries + 1))
    done
    test -f "$state/repair-in-progress"
    kill -TERM "$pid"
    set +e
    wait "$pid"
    local rc=$?
    set -e
    [ "$rc" -ne 0 ]
    fixture_unchanged "$target" "$expected"
    test ! -e "$state/repair-in-progress"
    grep -qF "repair interrupted" "$log"
    grep -qF "ROLLBACK: PASS" "$log"
    echo "PASS SIGTERM during deployment window restores original target"
}

run_recovery_case() {
    local case_root="$TMP/recovery"
    local home="$case_root/home"
    local target="$case_root/target"
    local backup="$case_root/state/backups/prior"
    local state="$case_root/state"
    mkdir -p "$home" "$backup"
    make_fixture "$target"
    cp -a "$target/." "$backup/"
    printf '%s\n' 'def answer():' '    return 999' > "$target/example.py"
    printf '%s\n' "$backup" > "$state/repair-in-progress"
    make_fake_tools "$home"
    set +e
    HOME="$home" PATH="$REAL_PATH" OPEN_CLOUD_HERMES_ROOT="$target" \
        OPEN_CLOUD_REPAIR_STATE="$state" OPEN_CLOUD_REPAIR_TIMEOUT=10 \
        OPEN_CLOUD_REPAIR_SANDBOX_NETWORK=shared OPEN_CLOUD_FAULT_MODE=stage-invalid \
        OPEN_CLOUD_REAL_PYTHON="$REAL_PYTHON" \
        "$HARNESS" --task synthetic > "$case_root/log" 2>&1
    local rc=$?
    set -e
    [ "$rc" -ne 0 ]
    fixture_unchanged "$target" "$backup"
    test ! -e "$state/repair-in-progress"
    grep -qF "RECOVERY: restoring interrupted repair" "$case_root/log"
    echo "PASS durable marker recovers an abandoned transaction"
}

run_corrupt_marker_case() {
    local case_root="$TMP/corrupt-marker"
    local home="$case_root/home"
    local target="$case_root/target"
    local state="$case_root/state"
    mkdir -p "$home" "$state/backups"
    make_fixture "$target"
    make_fake_tools "$home"
    printf '%s\n' '/tmp/not-an-opencloud-backup' > "$state/repair-in-progress"
    set +e
    HOME="$home" PATH="$REAL_PATH" OPEN_CLOUD_HERMES_ROOT="$target" \
        OPEN_CLOUD_REPAIR_STATE="$state" OPEN_CLOUD_REAL_PYTHON="$REAL_PYTHON" \
        "$HARNESS" --task synthetic > "$case_root/log" 2>&1
    local rc=$?
    set -e
    [ "$rc" -ne 0 ]
    grep -Eq "backup is (missing|outside)" "$case_root/log"
    test -f "$state/repair-in-progress"
    echo "PASS corrupt/out-of-root durable marker fails safely without deletion"
}

run_symlink_marker_case() {
    local case_root="$TMP/symlink-marker"
    local home="$case_root/home"
    local target="$case_root/target"
    local state="$case_root/state"
    local outside="$case_root/outside-marker"
    mkdir -p "$home" "$state/backups"
    make_fixture "$target"
    make_fake_tools "$home"
    printf '%s\n' 'do-not-delete' > "$outside"
    ln -s "$outside" "$state/repair-in-progress"
    set +e
    HOME="$home" PATH="$REAL_PATH" OPEN_CLOUD_HERMES_ROOT="$target" \
        OPEN_CLOUD_REPAIR_STATE="$state" OPEN_CLOUD_REAL_PYTHON="$REAL_PYTHON" \
        "$HARNESS" --task synthetic > "$case_root/log" 2>&1
    local rc=$?
    set -e
    [ "$rc" -ne 0 ]
    grep -qF "marker is not a regular file" "$case_root/log"
    grep -qxF 'do-not-delete' "$outside"
    test -L "$state/repair-in-progress"
    echo "PASS symlink transaction marker is rejected without touching its target"
}

echo "Open Cloud Assistant self-repair rollback reliability test"
echo "Isolation: temporary HOME, target tree, state root, and fake OpenCode"
echo "Model/provider calls: none"
echo "Production Hermes modification: none"

run_stage_validation_case

run_deploy_rollback_case

run_lock_case

run_signal_case

run_recovery_case

run_corrupt_marker_case

run_symlink_marker_case

echo "SELF_REPAIR_ROLLBACK_RELIABILITY: PASS"
