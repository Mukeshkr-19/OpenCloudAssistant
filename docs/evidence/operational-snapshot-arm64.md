# Operational Evidence Snapshot

Collected: 2026-08-11T05:07:29Z

## Host

- Operating system: Ubuntu 24.04.4 LTS
- Architecture: aarch64
- Kernel: 6.17.0-1019-oracle
- Host uptime at observation: 482378 seconds (133 hours / 5 full days)
- Open Cloud Assistant commit: 6f7b70e

## Runtime health

- OpenCloud doctor: PASS
- Fleet registry timer: active=active enabled=enabled
- Fleet verifier timer: active=active enabled=enabled
- Hermes gateway service: active=active enabled=enabled
- User linger: yes

## Observed operational counters

- Successful scheduled-job completions observed in current Hermes log: 10
- Failed scheduled-job completions observed in current Hermes log: 0
- Trusted self-repair backup directories currently retained: 0

## Evidence scope

Real local runtime state plus aggregate counters from the current Hermes log.

These values are observations, not an uptime SLA or SLO.

The collector intentionally does not publish raw application logs.

## Privacy boundary

The collector does not emit:

- prompts or assistant responses;
- job names or job payloads;
- Vellum personal memory;
- career or identity data;
- API keys or provider credentials;
- IP addresses;
- usernames or home-directory paths;
- session identifiers;
- raw Fleet databases;
- raw Hermes logs.
