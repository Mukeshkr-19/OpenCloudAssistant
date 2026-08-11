# Self-Repair OS Sandbox Acceptance

Date: 2026-08-11

## Validation host

- Operating system: Ubuntu 24.04.4 LTS
- Architecture: aarch64
- Kernel: Linux 6.17.0-1019-oracle
- Bubblewrap: bubblewrap 0.9.0
- AppArmor package: 4.0.1really4.0.1-0ubuntu0.24.04.7
- Environment: real Ubuntu cloud host
- Provider/model calls during deterministic acceptance: none
- Production Hermes modification during deterministic acceptance: none

## Result

`SELF_REPAIR_SANDBOX_RELIABILITY: PASS`

The production self-repair harness was exercised through the real Bubblewrap
boundary using a deterministic synthetic OpenCode executable.

## Proven isolation

The acceptance test proved that:

- a host-home secret fixture was not visible to the sandboxed child;
- live target contents were hidden behind the production-target mask;
- writes to the isolated sandbox HOME did not modify the normal host HOME;
- writes attempted through the masked target did not modify the real target;
- the staged source tree remained writable;
- an artifact created only inside staging reached the target only through the
  trusted outer deployment path;
- the existing trusted backup and rollback behavior continued to pass.

## Ubuntu AppArmor boundary

Ubuntu AppArmor unprivileged-user-namespace restrictions remained enabled.

The Ubuntu-provided `bwrap-userns-restrict` policy was enabled instead of
globally disabling the host user-namespace security restriction.

## Network boundary

Production OpenCode requires outbound connectivity to its configured model
provider.

The production repair sandbox therefore retains the host network namespace
while filesystem and process namespace isolation remain active.

The deterministic acceptance test performs no provider or external network
calls.

## Writable boundary

The controlled writable host-backed paths are:

1. the temporary staged source tree;
2. the ephemeral sandbox HOME within the trusted repair work directory.

The live production source is masked from the sandbox.

## Defense in depth

OS-level Bubblewrap isolation is combined with the restricted OpenCode agent
policy.

The repair agent continues to deny shell execution, Git operations, web
search, web fetch, subagents, and external-directory access.

## Validation commands

    ./tests/reliability/self-repair-sandbox.sh
    ./tests/reliability/self-repair-rollback.sh
    ./tests/reliability/run.sh

Hosted CI may skip the real Bubblewrap execution when the runner host does not
permit unprivileged user namespaces. The real ARM64 cloud-host acceptance
above is the OS-sandbox execution proof.
