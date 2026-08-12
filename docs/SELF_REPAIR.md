# Self-Repair Architecture

Open Cloud Assistant includes an optional restricted code-repair mechanism for
Hermes source defects.

The repair system separates the AI editing layer from the trusted deployment
layer.

## Restricted AI layer

OpenCode receives a temporary staging copy of the Hermes source tree. The
restricted agent may read, search, and edit source code inside that worktree.
It may not run shell commands, use Git, use web tools, launch subagents, or
access external directories.

The agent is also instructed not to access environment files, credentials,
auth state, personal memory, runtime databases, or other secret material.

## Trusted outer harness

The outer harness performs the privileged workflow:

1. validate the current source tree;
2. copy source code into an isolated staging tree;
3. exclude Git metadata and environment files;
4. run the restricted agent against only the staging tree;
5. reject sensitive artifacts;
6. validate Python and shell syntax;
7. verify that a real source change occurred;
8. create a trusted pre-deployment backup;
9. copy the validated stage into the target tree;
10. validate the deployed result;
11. roll back if deployment validation fails;
12. retain a bounded number of backups.

The public repair harness does not commit or push Git history and does not
restart Hermes automatically. Restart and post-repair health verification are
owned by the deployment/service layer.

## Public/private separation

The public implementation contains no private backup repository, private host,
personal deployment path, or private checkpoint dependency.

Deployments may add their own trusted backup workflow outside the restricted
repair agent.

## OS-level sandbox boundary

The restricted OpenCode process now runs through Bubblewrap on supported Ubuntu
hosts.

The sandbox adds an operating-system boundary around the AI editing process:

- mount, user, PID, IPC, and UTS namespaces are isolated;
- the host filesystem is mounted read-only by default, so paths outside the
  replaced host HOME may remain readable to the child;
- the normal user home is replaced with an isolated temporary home;
- the staged source tree and ephemeral sandbox home are the only controlled writable host-backed mounts;
- the live Hermes target is masked from the sandbox;
- temporary and runtime directories are isolated;
- Git metadata is still excluded before the agent starts;
- the trusted outer harness remains the only component allowed to create the
  production backup or deploy into the live target.

OpenCode configuration required for the restricted repair agent is exposed only
as narrowly scoped read-only mounts.

When OpenCode provider authentication exists at its standard runtime location,
that authentication file is mounted read-only because the OpenCode client needs
it to call the configured model provider. The complete host home is not
mounted into the sandbox. OpenCode external-directory permissions remain an
additional control around agent file access.

Production repair keeps host networking available because OpenCode must reach a
remote model provider. The agent itself still has shell, web search, web fetch,
Git, subagent, and external-directory capabilities denied. The deterministic sandbox reliability test uses the same shared-network mode as production and makes no provider calls.

This boundary prevents host writes outside the staged tree and ephemeral HOME,
masks the production target, and hides the original host HOME at its normal
path. It does not claim that every host-readable secret outside HOME is
invisible: the remaining host filesystem is intentionally present read-only so
the OpenCode runtime and system libraries work. Shared-network mode also allows
provider traffic. There is no seccomp claim. The restricted OpenCode policy and
prompt prohibit external-directory and secret access in addition to these OS
write boundaries.

Within the staged tree, authorized OpenCode repair retains full edit, replace,
and rewrite capability. Only the trusted outer harness validates, backs up,
deploys, verifies, and rolls back production code.

## Recovery marker

Immediately before deployment, the trusted harness writes a fixed
`repair-in-progress` marker beneath its owner-only state directory. It writes a
temporary file, fsyncs it, atomically renames it, and fsyncs the parent
directory. Recovery accepts only one validated backup directory directly under
the managed backup root; corrupt, out-of-root, or symlink markers fail closed.
The marker is data and is never sourced as shell code. It is removed only after
the deployed tree validates or a rollback validates. These steps improve crash
recovery durability but do not claim perfect consistency across arbitrary
SIGKILL or power-loss timing.

### Ubuntu AppArmor user-namespace policy

Ubuntu 24.04 restricts unprivileged user namespaces through AppArmor.

On systems where that restriction is active, Open Cloud Assistant uses the
Ubuntu-provided `bwrap-userns-restrict` AppArmor profile so Bubblewrap can
construct the repair sandbox without globally disabling the operating-system
user-namespace restriction.

The installer does not set
`kernel.apparmor_restrict_unprivileged_userns=0`.

This preserves the host security policy while granting Bubblewrap the
narrowly defined namespace capability required for the sandbox.

## Validation

Run:

    integrations/self-repair/hermes-code-repair --self-test
    tests/reliability/self-repair-sandbox.sh
    tests/reliability/self-repair-rollback.sh

The self-test does not invoke an AI model and does not modify the live Hermes
installation. It verifies staging, syntax validation, change detection, and
backup/restore integrity with temporary fixture files.
