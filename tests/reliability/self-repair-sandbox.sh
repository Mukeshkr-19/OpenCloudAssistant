#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

HARNESS="$ROOT/integrations/self-repair/hermes-code-repair"
REAL_PATH="$PATH"

echo "Open Cloud Assistant self-repair OS sandbox reliability test"

if ! command -v bwrap >/dev/null 2>&1; then
    echo "SELF_REPAIR_SANDBOX_RELIABILITY: SKIP bubblewrap unavailable on test host"
    exit 0
fi

if ! bwrap \
    --unshare-all \
    --share-net \
    --die-with-parent \
    --new-session \
    --ro-bind / / \
    --proc /proc \
    --dev /dev \
    --tmpfs /tmp \
    -- \
    /bin/true \
    >/dev/null 2>&1
then
    echo "SELF_REPAIR_SANDBOX_RELIABILITY: SKIP host policy does not permit Bubblewrap user namespaces"
    exit 0
fi

TMP="$(mktemp -d)"
trap "rm -rf \"$TMP\"" EXIT

HOME_FIX="$TMP/home"
TARGET="$TMP/target"
STATE="$TMP/state"
BIN="$HOME_FIX/.local/bin"
AGENT_DIR="$HOME_FIX/.config/opencode/agents"
LOG="$TMP/sandbox.log"

mkdir -p \
    "$BIN" \
    "$AGENT_DIR" \
    "$TARGET" \
    "$STATE"

printf "%s\n" \
    "PRIVATE_SENTINEL" \
    > "$HOME_FIX/private-secret.txt"

printf "%s\n" \
    "def answer():" \
    "    return 42" \
    > "$TARGET/example.py"

printf "%s\n" \
    "#!/usr/bin/env bash" \
    "echo fixture-ok" \
    > "$TARGET/check.sh"

chmod 755 "$TARGET/check.sh"

printf "%s\n" \
    "---" \
    "description: deterministic sandbox fixture" \
    "---" \
    > "$AGENT_DIR/hermes-repair.md"

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
    "if [ -e \"\$HOME/private-secret.txt\" ]; then" \
    "    echo \"SANDBOX_FAKE: secret_visible\"" \
    "    exit 90" \
    "fi" \
    "echo \"SANDBOX_FAKE: secret_hidden\"" \
    "if [ -e \"$TARGET/example.py\" ]; then" \
    "    echo \"SANDBOX_FAKE: production_target_visible\"" \
    "    exit 91" \
    "fi" \
    "echo \"SANDBOX_FAKE: production_target_hidden\"" \
    "printf \"%s\\n\" \"sandbox-home-write\" > \"\$HOME/escape-write.txt\"" \
    "echo \"SANDBOX_FAKE: home_write_isolated\"" \
    "if printf \"%s\\n\" \"sandbox-target-write\" > \"$TARGET/escape-write.txt\" 2>/dev/null; then" \
    "    echo \"SANDBOX_FAKE: target_write_isolated\"" \
    "else" \
    "    echo \"SANDBOX_FAKE: target_write_blocked\"" \
    "fi" \
    "printf \"%s\\n\" \"def answer():\" \"    return 43\" > \"\$stage/example.py\"" \
    "printf \"%s\\n\" \"STAGED_ONLY_PROOF\" > \"\$stage/sandbox-deploy-proof.txt\"" \
    "exit 0" \
    > "$BIN/opencode"

chmod 755 "$BIN/opencode"

set +e

HOME="$HOME_FIX" \
PATH="$BIN:$REAL_PATH" \
OPEN_CLOUD_HERMES_ROOT="$TARGET" \
OPEN_CLOUD_REPAIR_STATE="$STATE" \
OPEN_CLOUD_REPAIR_TIMEOUT=20 \
OPEN_CLOUD_REPAIR_SANDBOX_NETWORK=shared \
"$HARNESS" \
    --task \
    "make one deterministic fixture change" \
    > "$LOG" 2>&1

RC=$?

set -e

cat "$LOG"

if [ "$RC" -ne 0 ]; then
    echo "SELF_REPAIR_SANDBOX_RELIABILITY: FAIL harness returned $RC" >&2
    exit 1
fi

grep -qF \
    "REPAIR_SANDBOX: bubblewrap network=shared" \
    "$LOG"

grep -qF \
    "REPAIR_SANDBOX: isolated-home" \
    "$LOG"

grep -qF \
    "REPAIR_SANDBOX: production-target-masked" \
    "$LOG"

grep -qF \
    "REPAIR_SANDBOX: controlled-host-writes=stage,sandbox-home" \
    "$LOG"

grep -qF \
    "SANDBOX_FAKE: secret_hidden" \
    "$LOG"

grep -qF \
    "SANDBOX_FAKE: production_target_hidden" \
    "$LOG"

grep -qF \
    "SANDBOX_FAKE: home_write_isolated" \
    "$LOG"

test ! -e "$HOME_FIX/escape-write.txt"
test ! -e "$TARGET/escape-write.txt"

test "$(cat "$HOME_FIX/private-secret.txt")" = "PRIVATE_SENTINEL"

test -f "$TARGET/sandbox-deploy-proof.txt"

test "$(
    cat "$TARGET/sandbox-deploy-proof.txt"
)" = "STAGED_ONLY_PROOF"

echo "PASS host-home secret hidden from sandboxed child"
echo "PASS production target contents hidden from sandboxed child"
echo "PASS sandbox HOME writes do not escape to host HOME"
echo "PASS sandbox target writes do not escape to production target"
echo "PASS staged-only deployment marker reaches target through trusted deployment"
echo "PASS production-compatible shared-network sandbox mode"
echo "SELF_REPAIR_SANDBOX_RELIABILITY: PASS"
