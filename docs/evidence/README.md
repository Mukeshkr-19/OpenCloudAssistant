# Operational Evidence

This directory contains sanitized engineering evidence produced from validated
Open Cloud Assistant environments.

The goal is to distinguish three different things clearly:

1. deterministic test evidence;
2. point-in-time observations from real hosts;
3. long-running operational history accumulated over time.

None of these files should be presented as an SLA or SLO unless a future
version defines and measures one explicitly.

## Current evidence

- `self-repair-sandbox-arm64.md` documents real ARM64 OS-sandbox acceptance.
- `operational-snapshot-arm64.md` is the latest sanitized real-host snapshot.
- `operational-history.md` accumulates point-in-time observations.

## Collect another observation

Run:

    ./scripts/collect-operational-evidence.sh \
        --output docs/evidence/operational-snapshot-arm64.md \
        --append-history docs/evidence/operational-history.md

Then inspect the generated diff before committing it.

Git history preserves previous snapshots while `operational-history.md`
provides a compact longitudinal view.

## Privacy policy

Never publish raw Hermes logs, prompts, responses, session identifiers,
personal Vellum memory, cron payloads, credentials, IP addresses, private
provider state, or runtime databases as evidence.

The collector outputs aggregate operational facts only.
