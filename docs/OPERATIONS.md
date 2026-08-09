# Operations

Use this guide after installation to keep Open Cloud Assistant healthy without digging through implementation files.

All project wrapper examples assume you are in the repository directory:

```bash
cd ~/OpenCloudAssistant
```

## Health check

```bash
./bin/opencloud doctor
```

Interpretation:

- `PASS` — required/selected component is healthy;
- `FAIL` — selected/required component needs attention;
- `SKIP` — optional component is not configured or intentionally disabled.

Do not "fix" a normal SKIP by adding credentials you do not want to use.

## Provider/Fleet status

```bash
./bin/opencloud providers status
./bin/opencloud fleet status
./bin/opencloud fleet proof
```

After changing NVIDIA/OpenRouter credentials or when provider capacity changes:

```bash
./bin/opencloud fleet refresh
```

Manual verification only:

```bash
./bin/opencloud fleet verify
```

## Channels

```bash
./bin/opencloud channels status
./bin/opencloud channels configure
```

After changing a messaging selection, re-apply services so the gateway requirement matches the new selection:

```bash
./bin/opencloud services plan
./bin/opencloud services install
```

## Services

```bash
./bin/opencloud services status
```

The managed Fleet timers are:

```text
hermes-fleet-registry.timer
hermes-fleet-verifier.timer
```

A messaging deployment may also run:

```text
hermes-gateway.service
```

CLI-only intentionally skips the gateway requirement.

## Logs

Gateway logs:

```bash
./bin/opencloud services logs
```

Equivalent systemd command:

```bash
journalctl --user -u hermes-gateway.service -n 100 --no-pager
```

Fleet timer/service logs can be inspected with:

```bash
journalctl --user -u hermes-fleet-registry.service -n 100 --no-pager
journalctl --user -u hermes-fleet-verifier.service -n 100 --no-pager
```

Do not paste raw logs publicly before checking for private prompts, identifiers, or provider error payloads.

## Restart gateway

```bash
./bin/opencloud services restart-gateway
```

Routine gateway lifecycle notifications are suppressed from user conversations by default. A deliberate restart should still appear in systemd logs, and internal recovery behavior remains enabled.

## Boot persistence

Check linger:

```bash
loginctl show-user "$USER" -p Linger
```

Enable if needed:

```bash
sudo loginctl enable-linger "$USER"
```

Then verify:

```bash
./bin/opencloud services status
./bin/opencloud doctor
```

## Re-run installation safely

v0.1.0 was validated with a complete second install. Before re-running after source changes:

```bash
./setup.sh --dry-run
```

If it passes:

```bash
./setup.sh --install
```

The live Hermes integration installer validates compatibility, creates local backups before deployment, and recognizes already-applied valid integration state.

## Updating the public repository

There is no dedicated `opencloud upgrade` command in v0.1.0. Use normal Git carefully:

```bash
cd ~/OpenCloudAssistant
git status --short
git pull --ff-only
./setup.sh --dry-run
```

If the working tree is not clean, understand your local changes before pulling. Do not use destructive reset commands just to make Git quiet.

After a compatible update:

```bash
./setup.sh --install
./bin/opencloud doctor
```

## Public-source validation

Before committing project changes:

```bash
./scripts/public-audit.sh
./tests/smoke/run.sh
```

The public audit checks tracked source for forbidden runtime/secret-shaped artifacts. It is not a substitute for reviewing the diff yourself.

## Configuration permissions

Check:

```bash
stat -c '%a %n' ~/.opencloud/config.env ~/.opencloud/channels.json 2>/dev/null
```

Expected project-managed config files are mode `600`.

Fix a config file if necessary:

```bash
chmod 600 ~/.opencloud/config.env
chmod 600 ~/.opencloud/channels.json 2>/dev/null || true
```

## Backup status in v0.1.0

Open Cloud Assistant has targeted rollback backups for integration/repair workflows, but it does **not** yet ship a complete user-facing backup/restore product.

Do not treat a Git clone as a backup of your assistant; private memory and runtime state are deliberately outside Git.

Until a supported backup command exists, use your cloud provider's VM/block-volume snapshot facilities and separately protect the credentials needed to rebuild integrations. Before copying Vellum/Hermes runtime directories manually, review their upstream backup/export guidance so you do not create inconsistent live-state copies.

## Browser preview

If Browser is selected, service doctor intentionally reports the release-validation gate. This is not fixed by exposing port 8642 to the internet. Either keep Browser unselected for the v0.1.0 stable path or treat it as experimental localhost-only functionality.
