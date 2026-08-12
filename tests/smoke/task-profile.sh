#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
H="$TMP/home"
HERMES_ROOT="${OPEN_CLOUD_HERMES_ROOT:-$HOME/.hermes/hermes-agent}"
PY="${OPEN_CLOUD_HERMES_PYTHON:-$HOME/.hermes/hermes-agent/venv/bin/python}"
[ -x "$PY" ] || PY="$(command -v python3)"
PROFILE="$H/.opencloud/task-profiles/example-research.json"
CONFIG="$H/.hermes/profiles/example-research/config.yaml"
mkdir -p "$(dirname "$PROFILE")" "$(dirname "$CONFIG")" "$H/.config/hermes-vellum/mcp"
chmod 700 "$H/.opencloud" "$H/.opencloud/task-profiles"
printf '%s\n' '{"version":1,"mode":"read-only-research","parent_max_turns":15,"max_concurrent_children":4,"child_max_iterations":12,"child_timeout_seconds":120,"max_spawn_depth":2,"enabled_toolsets":["web","delegation","vellum-bridge"],"mcp_tools":{"vellum-bridge":{"include":["get_user_context"]}},"task":{"name":"Example Research Profile","schedule":"every 1d","prompt":"Research the synthetic example.","research_topics":["Example Project"],"use_vellum_context":true,"output_policy":{"format":"summary"},"scoring_policy":{"scale":"example"},"deliver":"local"}}' > "$PROFILE"
chmod 600 "$PROFILE"
printf '%s\n' 'model: {default: example-model}' > "$CONFIG"
printf '%s\n' 'def main(): return 0' > "$H/.config/hermes-vellum/mcp/server.py"

verify_profile_config() {
    "$PY" - "$CONFIG" "$H/.hermes/config.yaml" <<'PY'
import sys, yaml
profile, default = map(lambda path: yaml.safe_load(open(path)), sys.argv[1:])
assert profile["agent"]["max_turns"] == 15
assert profile["delegation"]["max_concurrent_children"] == 4
assert profile["mcp_servers"]["vellum-bridge"]["tools"]["include"] == ["get_user_context"]
assert "repair_code" not in profile["mcp_servers"]["vellum-bridge"]["tools"]["include"]
assert profile["gateway"]["multiplex_profiles"] is True
assert default["gateway"]["multiplex_profiles"] is True
PY
    grep -qxF 'OPEN_CLOUD_RESTRICTIVE_PROFILE=1' "$(dirname "$CONFIG")/.env"
}

if [ ! -f "$HERMES_ROOT/cron/jobs.py" ] || \
   [ ! -f "$HERMES_ROOT/cron/scheduler_provider.py" ] || \
   [ ! -f "$HERMES_ROOT/gateway/run.py" ]; then
    set +e
    PYTHONDONTWRITEBYTECODE=1 OPEN_CLOUD_HOME="$H" OPEN_CLOUD_HERMES_ROOT="$HERMES_ROOT" OPEN_CLOUD_HERMES_PYTHON="$PY" \
        scripts/task-profile.py apply --name example-research > "$TMP/missing-runtime.log" 2>&1
    RC=$?
    set -e
    [ "$RC" -ne 0 ]
    grep -Eq 'ERROR: Hermes (cron runtime is missing|runtime cannot tick profile-scoped cron jobs)' "$TMP/missing-runtime.log"
    verify_profile_config

    if OPEN_CLOUD_HOME="$H" scripts/task-profile.py verify --name ../escape >/dev/null 2>&1; then
        echo "FAIL: traversal profile name accepted" >&2; exit 1
    fi
    ln -s "$PROFILE" "$H/.opencloud/task-profiles/linked-profile.json"
    mkdir -p "$H/.hermes/profiles/linked-profile"
    printf '{}\n' > "$H/.hermes/profiles/linked-profile/config.yaml"
    if OPEN_CLOUD_HOME="$H" scripts/task-profile.py verify --name linked-profile >/dev/null 2>&1; then
        echo "FAIL: symlink profile accepted" >&2; exit 1
    fi

    echo "PASS task-profile config materialization and missing-runtime fail-closed behavior"
    echo "PASS traversal and symlink profile inputs fail closed"
    echo "TASK_PROFILE_CRON_MATERIALIZATION_SMOKE: SKIP (Hermes source unavailable; required pinned-runtime coverage is tests/reliability/task-profile-cron.py)"
    echo "TASK_PROFILE_SMOKE: PASS"
    exit 0
fi

PYTHONDONTWRITEBYTECODE=1 OPEN_CLOUD_HOME="$H" OPEN_CLOUD_HERMES_ROOT="$HERMES_ROOT" OPEN_CLOUD_HERMES_PYTHON="$PY" scripts/task-profile.py apply --name example-research > "$TMP/apply-1.log" 2>&1 &
P1=$!
PYTHONDONTWRITEBYTECODE=1 OPEN_CLOUD_HOME="$H" OPEN_CLOUD_HERMES_ROOT="$HERMES_ROOT" OPEN_CLOUD_HERMES_PYTHON="$PY" scripts/task-profile.py apply --name example-research > "$TMP/apply-2.log" 2>&1 &
P2=$!
wait "$P1" || { cat "$TMP/apply-1.log" >&2; exit 1; }
wait "$P2" || { cat "$TMP/apply-2.log" >&2; exit 1; }
PYTHONDONTWRITEBYTECODE=1 OPEN_CLOUD_HOME="$H" OPEN_CLOUD_HERMES_ROOT="$HERMES_ROOT" OPEN_CLOUD_HERMES_PYTHON="$PY" scripts/task-profile.py verify --name example-research
verify_profile_config
"$PY" - "$PROFILE" "$(dirname "$CONFIG")/cron/jobs.json" <<'PY'
import json, stat, sys
profile, store = map(lambda p: json.load(open(p)), sys.argv[1:])
jobs = store["jobs"]
assert len(jobs) == 1
job = jobs[0]
assert job["name"] == "Example Research Profile"
assert job["enabled_toolsets"] == profile["enabled_toolsets"]
assert "Example Project" in job["prompt"]
assert stat.S_IMODE(__import__("os").stat(sys.argv[1].replace(".json", ".state.json")).st_mode) == 0o600
assert stat.S_IMODE(__import__("os").stat(sys.argv[2]).st_mode) == 0o600
assert stat.S_IMODE(__import__("os").stat(__import__("os").path.dirname(sys.argv[2])).st_mode) == 0o700
PY
echo "PASS concurrent first apply creates exactly one managed job"

STATE_FILE="${PROFILE%.json}.state.json"
cp "$PROFILE" "$TMP/profile-good.json"
printf '{' > "$PROFILE"
if PYTHONDONTWRITEBYTECODE=1 OPEN_CLOUD_HOME="$H" OPEN_CLOUD_HERMES_ROOT="$HERMES_ROOT" OPEN_CLOUD_HERMES_PYTHON="$PY" scripts/task-profile.py verify --name example-research >/dev/null 2>&1; then
    echo "FAIL: malformed profile verified" >&2; exit 1
fi
cp "$TMP/profile-good.json" "$PROFILE"

printf '{' > "$STATE_FILE"
if PYTHONDONTWRITEBYTECODE=1 OPEN_CLOUD_HOME="$H" OPEN_CLOUD_HERMES_ROOT="$HERMES_ROOT" OPEN_CLOUD_HERMES_PYTHON="$PY" scripts/task-profile.py verify --name example-research >/dev/null 2>&1; then
    echo "FAIL: partial state verified" >&2; exit 1
fi
rm "$STATE_FILE"
PYTHONDONTWRITEBYTECODE=1 OPEN_CLOUD_HOME="$H" OPEN_CLOUD_HERMES_ROOT="$HERMES_ROOT" OPEN_CLOUD_HERMES_PYTHON="$PY" scripts/task-profile.py apply --name example-research >/dev/null
"$PY" - "$(dirname "$CONFIG")/cron/jobs.json" <<'PY'
import json, sys
assert len(json.load(open(sys.argv[1]))["jobs"]) == 1
PY
echo "PASS missing state rediscovers the existing managed job without duplication"

"$PY" - "$(dirname "$CONFIG")/cron/jobs.json" <<'PY'
import json, sys
p=sys.argv[1]; d=json.load(open(p)); d["jobs"]=[]; open(p,"w").write(json.dumps(d)+"\n")
PY
if PYTHONDONTWRITEBYTECODE=1 OPEN_CLOUD_HOME="$H" OPEN_CLOUD_HERMES_ROOT="$HERMES_ROOT" OPEN_CLOUD_HERMES_PYTHON="$PY" scripts/task-profile.py verify --name example-research >/dev/null 2>&1; then
    echo "FAIL: missing cron job verified" >&2; exit 1
fi
PYTHONDONTWRITEBYTECODE=1 OPEN_CLOUD_HOME="$H" OPEN_CLOUD_HERMES_ROOT="$HERMES_ROOT" OPEN_CLOUD_HERMES_PYTHON="$PY" scripts/task-profile.py apply --name example-research >/dev/null
echo "PASS stale state with missing cron job is repaired idempotently"

"$PY" - "$(dirname "$CONFIG")/cron/jobs.json" <<'PY'
import copy, json, sys, uuid
p=sys.argv[1]; d=json.load(open(p)); duplicate=copy.deepcopy(d["jobs"][0])
duplicate["id"]=str(uuid.uuid4()); d["jobs"].append(duplicate)
open(p,"w").write(json.dumps(d)+"\n")
PY
if PYTHONDONTWRITEBYTECODE=1 OPEN_CLOUD_HOME="$H" OPEN_CLOUD_HERMES_ROOT="$HERMES_ROOT" OPEN_CLOUD_HERMES_PYTHON="$PY" scripts/task-profile.py apply --name example-research >/dev/null 2>&1; then
    echo "FAIL: duplicate managed cron jobs accepted" >&2; exit 1
fi
"$PY" - "$(dirname "$CONFIG")/cron/jobs.json" <<'PY'
import json, sys
p=sys.argv[1]; d=json.load(open(p)); d["jobs"]=d["jobs"][:1]
open(p,"w").write(json.dumps(d)+"\n")
PY
echo "PASS duplicate managed cron jobs fail closed"

if OPEN_CLOUD_HOME="$H" scripts/task-profile.py verify --name ../escape >/dev/null 2>&1; then
    echo "FAIL: traversal profile name accepted" >&2; exit 1
fi
LINK_NAME="linked-profile"
ln -s "$PROFILE" "$H/.opencloud/task-profiles/$LINK_NAME.json"
mkdir -p "$H/.hermes/profiles/$LINK_NAME"
printf '{}\n' > "$H/.hermes/profiles/$LINK_NAME/config.yaml"
if OPEN_CLOUD_HOME="$H" scripts/task-profile.py verify --name "$LINK_NAME" >/dev/null 2>&1; then
    echo "FAIL: symlink profile accepted" >&2; exit 1
fi
mv "$STATE_FILE" "$TMP/state-real.json"
ln -s "$TMP/state-real.json" "$STATE_FILE"
if OPEN_CLOUD_HOME="$H" OPEN_CLOUD_HERMES_ROOT="$HERMES_ROOT" OPEN_CLOUD_HERMES_PYTHON="$PY" scripts/task-profile.py verify --name example-research >/dev/null 2>&1; then
    echo "FAIL: symlink state accepted" >&2; exit 1
fi
unlink "$STATE_FILE"
mv "$TMP/state-real.json" "$STATE_FILE"
PROFILE_DIR="$(dirname "$PROFILE")"
mv "$PROFILE_DIR" "$TMP/profile-dir-real"
ln -s "$TMP/profile-dir-real" "$PROFILE_DIR"
if OPEN_CLOUD_HOME="$H" scripts/task-profile.py verify --name example-research >/dev/null 2>&1; then
    echo "FAIL: symlink profile directory accepted" >&2; exit 1
fi
unlink "$PROFILE_DIR"
mv "$TMP/profile-dir-real" "$PROFILE_DIR"
echo "PASS traversal, malformed JSON, partial state, and symlink paths fail closed"
"$PY" - "$PROFILE" <<'PY'
import json, sys
p = sys.argv[1]
d = json.load(open(p))
d.pop("task")
open(p, "w").write(json.dumps(d) + "\n")
PY
if PYTHONDONTWRITEBYTECODE=1 OPEN_CLOUD_HOME="$H" OPEN_CLOUD_HERMES_ROOT="$HERMES_ROOT" OPEN_CLOUD_HERMES_PYTHON="$PY" scripts/task-profile.py verify --name example-research >/dev/null 2>&1; then
    echo "FAIL: removed private task left an unattended job enabled" >&2
    exit 1
fi
PYTHONDONTWRITEBYTECODE=1 OPEN_CLOUD_HOME="$H" OPEN_CLOUD_HERMES_ROOT="$HERMES_ROOT" OPEN_CLOUD_HERMES_PYTHON="$PY" scripts/task-profile.py apply --name example-research
PYTHONDONTWRITEBYTECODE=1 OPEN_CLOUD_HOME="$H" OPEN_CLOUD_HERMES_ROOT="$HERMES_ROOT" OPEN_CLOUD_HERMES_PYTHON="$PY" scripts/task-profile.py verify --name example-research
"$PY" - "$(dirname "$CONFIG")/cron/jobs.json" <<'PY'
import json, sys
jobs = json.load(open(sys.argv[1]))["jobs"]
assert len(jobs) == 1 and jobs[0]["enabled"] is False
PY
echo "TASK_PROFILE_SMOKE: PASS"
