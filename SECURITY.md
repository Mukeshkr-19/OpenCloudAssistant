# Security Policy

Open Cloud Assistant connects AI agents to real services, files, personal memory, messaging platforms, and coding tools.

Treat deployment credentials and personal runtime state as production secrets.

## Never commit

- API keys or provider tokens
- OAuth credentials
- bearer tokens or JWTs
- SSH private keys
- `.env` files
- messaging credentials
- personal memory or private context
- conversation history
- runtime databases
- authentication state
- session identifiers
- production logs containing private prompts or secrets

## Coding-agent boundaries

Automated coding agents should operate with restricted permissions.

They should not receive unrestricted access to:

- Git push credentials
- secret files
- personal-memory stores
- unrelated filesystem locations
- production databases
- system service control
- arbitrary network commands

A trusted outer workflow should create backups, validate changes, and perform privileged repository operations.

## Public-release policy

The public repository must be built from reusable source, templates, documentation, and sanitized integration logic.

Personal deployments must keep private runtime state outside Git.

Before a public release:

1. run the public credential/privacy audit,
2. run smoke and integration tests,
3. verify third-party licenses and notices,
4. test a clean supported installation.

## Reporting a security issue

Do not open a public issue containing a real credential, private memory, or authentication artifact.

If a credential was exposed, revoke or rotate it immediately before reporting the issue.
