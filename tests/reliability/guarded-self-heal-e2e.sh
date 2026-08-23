#!/usr/bin/env bash
# Sandbox E2E: success → RECOVERED; post-deploy canary-fail → ROLLED_BACK+QUARANTINED.
# Uses fake OpenCode + fake GitHub/deploy/canary adapters only. Never touches real GH/Hermes.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

FAKE="$ROOT/tests/reliability/fixtures/fake-opencode"
chmod +x "$FAKE"
PKG="$ROOT/integrations/self-repair"

echo "Open Cloud Assistant guarded self-heal E2E"

TMP="$(mktemp -d "$ROOT/.tmp-self-heal-e2e-XXXXXX")"
trap 'rm -rf "$TMP"' EXIT

rm -f /tmp/SHOULD_NOT_EXIST
STAGE="$TMP/shell-safety-stage"
mkdir -p "$STAGE/integrations/hermes" "$STAGE/tests/reliability"
cp "$ROOT/integrations/hermes/hermes-product-reliability-ux.patch" \
    "$STAGE/integrations/hermes/"
cp "$ROOT/tests/reliability/product-reliability-ux.py" \
    "$STAGE/tests/reliability/"
PYTHONPATH="$PKG" FAKE="$FAKE" STAGE="$STAGE" python3 <<'PY'
import os
from pathlib import Path
from guarded_heal.controller import OpenCodeRunner
fake = os.environ["FAKE"]
stage = Path(os.environ["STAGE"])
oc = OpenCodeRunner(opencode_bin=fake, timeout=30)
ok, out = oc.run(
    workdir=stage,
    prompt="hello; touch /tmp/SHOULD_NOT_EXIST",
    model="opencode/fake",
)
assert ok, out
assert not Path("/tmp/SHOULD_NOT_EXIST").exists(), "shell metacharacters executed"
print("PASS shell-safety (hello; touch sentinel)")
PY

break_greeting() {
    local repo="$1"
    python3 - "$repo" <<'PY'
from pathlib import Path
import sys

NARROW_PATCH = r'''+def _opencloud_is_conversational_greeting(text: str) -> bool:
+    import re
+    raw = (text or "").strip()
+    if not raw or len(raw) > 80:
+        return False
+    if re.search(r"https?://|/\S|\b(search|find|browse|open|run|fix|deploy|cron)\b", raw, re.I):
+        return False
+    if re.fullmatch(
+        r"(?i)(hi|hello|hey|yo|sup|howdy|hiya|good\s*(morning|afternoon|evening))"
+        r"([,.!]+\s*[A-Za-z]{0,24})?[.!]?",
+        raw,
+    ):
+        return True
+    if re.fullmatch(r"(?i)(hi|hello|hey)[.!]?\s+(there|hermes|assistant|friend)[.!]?", raw):
+        return True
+    return False'''

NARROW_TEST = r'''    def _opencloud_is_conversational_greeting(raw_text: str) -> bool:
        raw = (raw_text or "").strip()
        if not raw or len(raw) > 80:
            return False
        if re.search(r"https?://|/\S|\b(search|find|browse|open|run|fix|deploy|cron)\b", raw, re.I):
            return False
        if re.fullmatch(
            r"(?i)(hi|hello|hey|yo|sup|howdy|hiya|good\s*(morning|afternoon|evening))"
            r"([,.!]+\s*[A-Za-z]{0,24})?[.!]?",
            raw,
        ):
            return True
        if re.fullmatch(r"(?i)(hi|hello|hey)[.!]?\s+(there|hermes|assistant|friend)[.!]?", raw):
            return True
        return False'''

NARROW_ASSERT = '''    fn = _opencloud_is_conversational_greeting
    require(fn("Hi") is True, "Hi should match")
    require(fn("hello!") is True, "hello! should match")
    require(fn("Hey there") is True, "Hey there should match")
    require(fn("Good morning") is True, "Good morning should match")
    require(fn("Hi, Mukesh") is True, "Hi, name should match")
    require(fn("Hi, search for jobs") is False, "taskful greeting must not match")
    require(fn("browse https://example.com") is False, "URL task must not match")
    require(fn("find internships in NYC") is False, "search intent must not match")
    require(fn("x" * 100) is False, "long text must not match")'''


def replace_between(text, start, end_re, body):
    import re
    a = text.find(start)
    if a < 0:
        raise SystemExit(f"missing {start!r}")
    m = re.search(end_re, text[a:])
    if not m:
        raise SystemExit(f"missing end {end_re!r}")
    b = a + m.start()
    return text[:a] + body + text[b:]

root = Path(sys.argv[1])
patch = root / "integrations/hermes/hermes-product-reliability-ux.patch"
p = patch.read_text(encoding="utf-8")
p = replace_between(
    p,
    "+def _opencloud_is_conversational_greeting(text: str) -> bool:",
    r"\n\+def _opencloud_restore_tools\(agent\) -> None:",
    NARROW_PATCH,
)
patch.write_text(p, encoding="utf-8")

test = root / "tests/reliability/product-reliability-ux.py"
t = test.read_text(encoding="utf-8")
t = replace_between(
    t,
    "    def _opencloud_is_conversational_greeting(raw_text: str) -> bool:",
    r"\n\n    fn = _opencloud_is_conversational_greeting",
    NARROW_TEST,
)
start = t.find("    fn = _opencloud_is_conversational_greeting")
end = t.find("    # Installer wiring")
t = t[:start] + NARROW_ASSERT + "\n\n" + t[end:]
test.write_text(t, encoding="utf-8")
PY
    git -C "$repo" -c core.hooksPath=/dev/null add -A
    git -C "$repo" -c core.hooksPath=/dev/null -c user.email=test@example.com -c user.name=test \
        commit -m "test: narrow greeting for self-heal e2e" >/dev/null
}

# Prefer a plain copy so CI sandboxes that block git-hook writes still work.
# E2E seed uses rsync only to *build a disposable test repo*, then git init —
# production controller never uses rsync (worktree/clone only).
seed_repo() {
    local dest="$1"
    mkdir -p "$dest"
    rsync -a \
        --exclude '.git/' \
        --exclude '.tmp-self-heal-e2e-*/' \
        --exclude '.claw-workflow/' \
        --exclude '__pycache__/' \
        "$ROOT/" "$dest/"
    git -C "$dest" init -q --template=
    git -C "$dest" -c core.hooksPath=/dev/null add -A
    git -C "$dest" -c core.hooksPath=/dev/null -c user.email=test@example.com -c user.name=test \
        commit -m "seed" >/dev/null
}

# ── Success path → RECOVERED (fake promote + deploy + canary) ──
STATE="$TMP/state"
BROKEN="$TMP/broken-repo"
seed_repo "$BROKEN"
break_greeting "$BROKEN"
BASE_HEAD="$(git -C "$BROKEN" rev-parse HEAD)"

OPEN_CLOUD_SELF_HEAL_FAKE_REVIEW=APPROVE \
OPEN_CLOUD_SELF_HEAL_MODELS=opencode/fake,opencode/fake2 \
OPEN_CLOUD_SELF_HEAL_ALLOW_MEDIUM_AUTOMERGE=1 \
OPEN_CLOUD_SELF_HEAL_TEST_MODE=1 \
PYTHONPATH="$PKG" \
python3 - "$BROKEN" "$STATE" "$FAKE" "$BASE_HEAD" <<'PY'
import sys
from pathlib import Path
from guarded_heal.adapters import (
    CanaryAdapter,
    DeployAdapter,
    DeployResult,
    GitHubPromoter,
    PromoteResult,
)
from guarded_heal.controller import SelfHealController, OpenCodeRunner

root, state, fake, base = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3], sys.argv[4]

def promote_ok(*_a, **_k):
    return PromoteResult(
        status="PROMOTED",
        detail="fake merge",
        gh_auth_ok=True,
        pushed=True,
        checks_passed=True,
        merged=True,
        merged_head="e2emerge01",
        merged_tree="e2etree01",
        repair_branch="self-heal/e2e",
        repair_commit="e2erepair",
        pr_number=7,
    )

def deploy_ok(sha, *, quarantined):
    assert not quarantined
    return DeployResult(
        status="DEPLOYED",
        detail="fake deploy",
        previous_head=base,
        previous_tree="prev",
        deployed_head=sha,
        deployed_tree="e2etree01",
    )

ctrl = SelfHealController(
    repo_root=root,
    state_root=state,
    opencode=OpenCodeRunner(opencode_bin=fake, timeout=120),
    validate_cmd=lambda _w: (True, "focused-ok"),
    promoter=GitHubPromoter(dry_invoke=promote_ok),
    deployer=DeployAdapter(repo_root=root, dry_invoke=deploy_ok),
    canary=CanaryAdapter(dry_invoke=lambda _k: (True, "ok")),
    test_mode=True,
)
row = ctrl.ingest(
    "ValueError",
    "clarify tool choices must be a list of strings",
    module="tools.clarify",
    auto_run=True,
)
assert row is not None, row
assert row["state"] == "RECOVERED", row
assert row["meta"].get("base_head") == base, row
assert row["meta"].get("merged_head") == "e2emerge01", row
assert row["meta"].get("private_sync") == "PRIVATE_SYNC_ELIGIBLE", row
print(f"PASS E2E success → RECOVERED")
PY

# ── Post-deploy canary fail → ROLLED_BACK + QUARANTINED ──
STATE2="$TMP/state2"
GOOD="$TMP/good-repo"
seed_repo "$GOOD"
BASE2="$(git -C "$GOOD" rev-parse HEAD)"

OPEN_CLOUD_SELF_HEAL_FAKE_REVIEW=APPROVE \
OPEN_CLOUD_SELF_HEAL_MODELS=opencode/fake,opencode/fake2 \
OPEN_CLOUD_SELF_HEAL_ALLOW_MEDIUM_AUTOMERGE=1 \
OPEN_CLOUD_SELF_HEAL_TEST_MODE=1 \
PYTHONPATH="$PKG" \
python3 - "$GOOD" "$STATE2" "$FAKE" "$BASE2" <<'PY'
import sys
from pathlib import Path
from guarded_heal.adapters import (
    CanaryAdapter,
    DeployAdapter,
    DeployResult,
    GitHubPromoter,
    PromoteResult,
)
from guarded_heal.controller import SelfHealController, OpenCodeRunner

root, state, fake, base = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3], sys.argv[4]
calls = {"n": 0}

def promote_ok(*_a, **_k):
    return PromoteResult(
        status="PROMOTED",
        detail="fake merge",
        gh_auth_ok=True,
        pushed=True,
        checks_passed=True,
        merged=True,
        merged_head="e2efailsha",
        merged_tree="e2etreefail",
        repair_branch="self-heal/e2e",
        repair_commit="e2erepair",
        pr_number=8,
    )

def dry_deploy(sha, *, quarantined):
    calls["n"] += 1
    if calls["n"] == 1:
        return DeployResult(
            status="DEPLOYED",
            detail="fake deploy",
            previous_head=base,
            previous_tree="prev",
            deployed_head=sha,
            deployed_tree="e2etreefail",
        )
    return DeployResult(
        status="ROLLED_BACK",
        detail="fake rollback",
        previous_head=sha,
        deployed_head=base,
    )

ctrl = SelfHealController(
    repo_root=root,
    state_root=state,
    opencode=OpenCodeRunner(opencode_bin=fake, timeout=120),
    validate_cmd=lambda _w: (True, "focused-ok"),
    promoter=GitHubPromoter(dry_invoke=promote_ok),
    deployer=DeployAdapter(repo_root=root, dry_invoke=dry_deploy),
    canary=CanaryAdapter(
        dry_invoke=lambda k: (False, "post fail") if k == "post" else (True, "pre ok")
    ),
    test_mode=True,
)
row = ctrl.ingest(
    "ValueError",
    "clarify tool choices must be a list of strings",
    module="tools.clarify",
    auto_run=True,
)
assert row is not None, row
assert row["state"] == "ROLLED_BACK", row
assert ctrl.store.is_quarantined("e2efailsha"), "must quarantine failed sha"
print("PASS E2E post-deploy canary-fail → ROLLED_BACK + QUARANTINED")
PY

# ── Pre-promotion canary fail ≠ ROLLED_BACK ──
STATE3="$TMP/state3"
PRE="$TMP/pre-repo"
seed_repo "$PRE"
break_greeting "$PRE"

OPEN_CLOUD_SELF_HEAL_FAKE_REVIEW=APPROVE \
OPEN_CLOUD_SELF_HEAL_MODELS=opencode/fake,opencode/fake2 \
OPEN_CLOUD_SELF_HEAL_FORCE_CANARY_FAIL=1 \
OPEN_CLOUD_SELF_HEAL_TEST_MODE=1 \
PYTHONPATH="$PKG" \
python3 - "$PRE" "$STATE3" "$FAKE" <<'PY'
import sys
from pathlib import Path
from guarded_heal.controller import SelfHealController, OpenCodeRunner

root, state, fake = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3]
ctrl = SelfHealController(
    repo_root=root,
    state_root=state,
    opencode=OpenCodeRunner(opencode_bin=fake, timeout=120),
    validate_cmd=lambda _w: (True, "ok"),
    test_mode=True,
)
row = ctrl.ingest(
    "ValueError",
    "clarify tool choices must be a list of strings",
    module="tools.clarify",
    auto_run=True,
)
assert row is not None, row
assert row["state"] == "CANARY_FAILED", row
assert row["state"] != "ROLLED_BACK", "preflight failure is not rollback"
print("PASS E2E pre-canary-fail → CANARY_FAILED (not ROLLED_BACK)")
PY

echo "GUARDED_SELF_HEAL_E2E: PASS"
