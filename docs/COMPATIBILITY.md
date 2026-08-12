# Open Cloud Assistant Live Compatibility

Captured UTC: 2026-08-09T03:02:02Z

## Platform

Architecture: aarch64
Kernel: Linux 6.17.0-1019-oracle

Operating system: Ubuntu 24.04.4 LTS

## Hermes

Hermes Git HEAD: 3fa318a50c02df8dbd2c55499f5f73d51ad77188
Hermes branch: main

## Runtime

Python: Python 3.12.3
Node: v22.23.2
npm: 10.9.8
Bun: 1.3.14
OpenCode: 1.18.15
Freebuff: 0.0.142

## Safety

Gemini unverified lane: blocked
OpenRouter stable free lane: required
Runtime model discovery: enabled
Private memory included: no
Runtime health databases included: no
Credentials included: no

## Additional local runtime validation

On 2026-08-12, the pinned Hermes source above was also materialized and
exercised in a disposable clone on macOS ARM64 with CPython 3.14. OpenCloud applies a narrow
compatibility patch to Hermes' daemon thread-pool construction when the newer
CPython worker signature is detected. The reliability gate tests observable
behavior—bounded concurrency, real overlap, one worker for a one-task batch,
cleanup, and depth controls—without requiring a particular executor class.

This does not expand the deployment support claim beyond Ubuntu. It is a local
compatibility test for OpenCloud's materialized Hermes integration.

The separate local Mac checkout observed during that run (`67783ad4e...`) was
stale relative to its recorded upstream and was test input only, not a supported
deployment target. Compatibility remains pinned and fails closed on patch drift.
