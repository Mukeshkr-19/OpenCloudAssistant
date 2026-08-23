# Guarded Autonomous Self-Healing

Extends the existing Hermes self-repair stack (`hermes-code-repair`, P8
`opencloud_self_repair`) with a **source-controlled** control plane for Open
Cloud Assistant itself.

Flow: **DETECT → CAPTURE → CLASSIFY → RECOVER → VERIFY → PROMOTE → DEPLOY →
POST-DEPLOY CANARY → RECOVERED** (or truthful stop / rollback).

**Invariant:** unknown ≠ success; eligible ≠ promoted; validated ≠ deployed;
preflight failure ≠ rollback; timeout ≠ success; missing operation ≠ success.

This is **not** unrestricted self-modification of production.

## What already existed (reused)

| Piece | Role |
|-------|------|
| `integrations/self-repair/hermes-code-repair` | OS-sandboxed OpenCode repair of a **staged Hermes** tree with backup/rollback |
| P8 `agent/opencloud_self_repair.py` | Auto-trigger for **internal** TypeError/AttributeError/metadata leaks only |
| `scripts/runtime-update.sh` | Tooling updates (OpenCode), not Hermes source repair |
| `install/60-self-repair.sh` | Installs harness + restricted agent |

## Why the “Hi bro” incident was not auto-recovered

1. Greeting classifier missed colloquial vocatives → clarify tool / provider timeout.
2. `ReadTimeout` and similar markers are **external** → P8 never repairs them.
3. Clarify `ValueError` (“choices must be a list of strings”) is not a P8
   TypeError/AttributeError class.
4. Even when P8 fires, it edits **live Hermes** via `hermes-code-repair`, not
   `integrations/hermes/*.patch` in this repo.
5. No incident lifecycle, GitHub promotion, or synthetic canary existed for
   OpenCloud **source** defects.

## Truthful states

`REPAIR_VALIDATED` → `READY_FOR_PROMOTION` → `PR_OPEN` → `CI_RUNNING` →
`PROMOTED` → `DEPLOYING` → `DEPLOYED` → `POST_DEPLOY_CANARY` → `RECOVERED`

Stops / failure: `CANARY_FAILED`, `VALIDATION_FAILED`, `QUARANTINED`,
`ROLLBACK_REQUIRED`, `ROLLED_BACK`, `HUMAN_REQUIRED`, `HUMAN_REQUIRED_SECURITY`,
`REPAIR_ENGINE_UNAVAILABLE`, `NO_ACTION_TRANSIENT`, `FAILED`.

- **READY ≠ PROMOTED.** Promotion only after an accepted public GitHub merge
  (merged HEAD+TREE recorded).
- **DEPLOYED** only after exact merged SHA is installed.
- **RECOVERED** only after post-deploy canary PASS.
- **Pre-promotion canary fail → `CANARY_FAILED`**, never `ROLLED_BACK`.
- **`ROLLED_BACK`** only after a real deploy + restore of previous SHA;
  failed SHA is **quarantined** and never redeployed.
- **`gh auth` ≠ promotion.** Auth capability is tracked separately from
  pushed / PR / CI / merged.

## New control plane

Package: `integrations/self-repair/guarded_heal/`

- **SQLite** incident store under `~/.opencloud/self-repair/` (override with
  `OPEN_CLOUD_SELF_HEAL_STATE`)
- **Tiers**: `0` none → `1` runtime/P8 (real invoke + verify, else
  `HUMAN_REQUIRED`) → `2` provider/Fleet (`NO_ACTION_TRANSIENT` unless
  verified; never source-edit quota/timeout; preserve `openrouter/free`;
  Gemini stays blocked) → `3` source repair
- **OpenCode CLI** directly (`opencode run`), fixed argv, `shell=False`,
  nonzero ≠ success; models from `opencode models opencode`. **No fake model
  in production** — empty discovery → `REPAIR_ENGINE_UNAVAILABLE`. Fake only
  with `OPEN_CLOUD_SELF_HEAL_TEST_MODE=1` or the `fake-opencode` fixture.
- **Isolated worktrees** only: `git worktree` or genuine `git clone --local`
  under `~/.opencloud/self-repair/worktrees/<id>/`. **No rsync fallback.**
  Fail closed. Workdir guard: ≠ canonical repo, ≠ live Hermes, beneath
  worktrees root, outside canonical; symlink escapes rejected.
- **Immutable** `base_head` / `base_tree`; also records `repair_branch`,
  `repair_commit`, `pr_number`, `merged_head`/`merged_tree`,
  `previous_deployed_head`/`tree`
- **Deny** terraform, `.env`, credentials, keys, gh/oci/ssh auth paths,
  private, benchmarks → `HUMAN_REQUIRED`
- **Diff secret scan** before artifact save / promotion →
  `HUMAN_REQUIRED_SECURITY` (no raw secret diff persisted)
- **Size limits**: max 12 files / 1500 changed lines (env-overridable)
- **Validation gate** (unattended): `git diff --check`, focused tests,
  `public-audit`, smoke/services, materialize if Hermes patches, installer
  `--check` if units. Bounded timeouts. No `|| true`.
- **Multi-model review**: &lt;2 distinct models → `UNCERTAIN_SINGLE_REVIEWER`,
  no unattended promote. Repair model cannot approve itself.
- **Promotion**: push branch → open PR → wait required checks → merge via
  normal policy. `shell=False`. No force-push / CI bypass. No invented
  tokens. If no secure `gh` write auth → `GITHUB_PROMOTION_UNAVAILABLE` /
  `READY_FOR_PROMOTION` (not PROMOTED/RECOVERED).
- **Severity**: HIGH/CRITICAL never auto-promote. MEDIUM → `HUMAN_REQUIRED`
  for auto-merge unless `OPEN_CLOUD_SELF_HEAL_ALLOW_MEDIUM_AUTOMERGE=1`.
  LOW may auto-merge through all gates.
- **Canary**: synthetic/local (product UX greeting + gateway health), **not**
  user iMessage. Injected adapters for deterministic CI.
- **Deploy / rollback** adapters after merge; separate circuits for
  `repair_attempts`, `promotions`, `deploys`, `rollbacks`
- **Dedup / reoccurrence**: `occurrence_count`; reopen window; escalate
  repeats → `HUMAN_REQUIRED`; never retry quarantined SHA
- **Auto-ingest**: timer tick drains `~/.opencloud/self-repair/inbox/*.json`
  (production-style clarify/errors) without manual `self-heal ingest`
- **Never** runs `hermes update` as unattended repair
- **Private sync** recorded as `PRIVATE_SYNC_ELIGIBLE` only after post-deploy
  canary PASS — **no private write** from this public controller

## Operator CLI

```bash
opencloud self-heal status
opencloud self-heal incidents
opencloud self-heal show <id>
opencloud self-heal retry <id>
opencloud self-heal disable
opencloud self-heal enable
opencloud self-heal ingest --exc-type ValueError --message '...'
opencloud self-heal run   # timer: auto-ingest inbox + status
```

## systemd

Optional user units `opencloud-self-heal.service` / `.timer` (tick runs inbox
auto-ingest). Installed alongside existing fleet/runtime-update timers; does
not replace `opencloud-runtime-update` or Hermes gateway.

## Tests

```bash
python3 tests/reliability/guarded-self-heal.py
./tests/reliability/guarded-self-heal-e2e.sh
```

E2E uses `tests/reliability/fixtures/fake-opencode` plus fake GitHub/deploy/
canary adapters (no live model, no real GH push, no Hermes restart). Shell
safety asserts `hello; touch /tmp/SHOULD_NOT_EXIST` does not create the
sentinel when invoked via argv list.
