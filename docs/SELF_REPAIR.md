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

## Safety boundary

OpenCode permissions are used to restrict agent tools. They are not treated as
an operating-system sandbox. The staging-copy workflow therefore remains a
separate boundary between agent edits and the live Hermes tree.

## Validation

Run:

    integrations/self-repair/hermes-code-repair --self-test

The self-test does not invoke an AI model and does not modify the live Hermes
installation. It verifies staging, syntax validation, change detection, and
backup/restore integrity with temporary fixture files.
