# AI Provider Setup

Open Cloud Assistant uses a **dynamic Fleet**. Permanent source policy describes provider pools and fallback order; the runtime discovers and verifies currently usable model IDs.

Do not "fix" a temporary provider outage by permanently hard-coding whichever NVIDIA or OpenCode Zen model happens to work today.

## Recommended free-first setup

For v0.1.0, configure:

1. **NVIDIA** — dynamic primary/reviewer capacity when verified;
2. **OpenRouter** — stable `openrouter/free` fallback route.

OpenCode Zen is optional. Gemini remains blocked by the public Hermes integration until it is separately configured and independently verified.

## Runtime credential file

Managed secrets live at:

```text
~/.opencloud/config.env
```

The file should be mode `600`.

The repository's `.env.example` is only a key-name reference. Do not store real credentials in the repository.

## Recommended configuration command

From the repository:

```bash
./bin/opencloud providers configure
```

The wizard asks for NVIDIA and OpenRouter keys without echoing them.

Check the result without printing credential values:

```bash
./bin/opencloud providers status
```

## NVIDIA

NVIDIA Build currently advertises a **Get API Key** flow and free serverless APIs for development.

Official entry points:

- https://build.nvidia.com/explore
- https://build.nvidia.com/settings/keys

Typical setup:

1. Sign in to NVIDIA Build / the NVIDIA Developer Program.
2. Choose **Get API Key** / create a key.
3. Copy the key once and store it securely.
4. Run `./bin/opencloud providers configure` and paste it at the hidden NVIDIA prompt.
5. Run `./bin/opencloud fleet refresh`.

NVIDIA free capacity and specific model availability can change. The Fleet registry is designed to absorb that churn by rediscovering candidates instead of pinning a transient model ID.

## OpenRouter

Create an API key from the OpenRouter dashboard, then store it through the project provider wizard.

Open Cloud Assistant's stable public policy route is:

```text
openrouter/free
```

OpenRouter documents `openrouter/free` as a router that selects from currently available free models and filters by request capabilities such as tool calling or structured output.

Official references:

- https://openrouter.ai/docs/guides/routing/routers/free-router
- https://openrouter.ai/openrouter/free

Free models have lower/variable rate limits and availability. They are useful for a personal free-first deployment, but they should not be confused with an uptime or privacy guarantee.

### Privacy note for free routing

OpenRouter routes requests to underlying model providers. Provider logging/retention policies vary, and some free endpoints may explicitly log prompts for evaluation or training. Do not assume "free" means "local" or "private." Avoid putting secrets in prompts and review provider policies before routing highly sensitive data.

## OpenCode Zen

OpenCode is installed by the self-repair stage and may expose free Zen capacity depending on the user's account/access and the live catalog.

The public Fleet treats Zen as **optional dynamic capacity**:

- if verified free capacity exists, workers may use it;
- if the client is missing or no verified capacity exists, doctor reports `SKIP` rather than breaking the core assistant.

There is intentionally no permanent Zen model ID in public architecture.

## Gemini

The public Fleet policy contains a Gemini emergency lane for architecture compatibility, but the Hermes integration retains an explicit safety guard that blocks it until independently verified.

Expected doctor output is:

```text
SKIP  Gemini lane  blocked until independently verified
```

Do not add a Gemini key expecting the v0.1.0 public router to start using it automatically.

## Refresh and verify Fleet

After adding/changing provider credentials:

```bash
./bin/opencloud fleet refresh
```

Inspect runtime proof:

```bash
./bin/opencloud fleet proof
```

Inspect overall Fleet state:

```bash
./bin/opencloud fleet status
```

Show paths without dumping the database/registry contents:

```bash
./bin/opencloud fleet paths
```

You can ask the dispatcher which candidate it would select for a role:

```bash
./bin/opencloud fleet select main
./bin/opencloud fleet select worker
./bin/opencloud fleet select reviewer
```

Do not paste runtime registry content into public issues if it includes deployment-specific provider state you do not intend to share.

## Role order

The permanent public policy is conceptually:

| Role | Pool order |
|---|---|
| Main | NVIDIA dynamic → OpenRouter free → Gemini emergency (blocked) |
| Worker | Zen free dynamic → NVIDIA dynamic → OpenRouter free → Gemini emergency (blocked) |
| Reviewer | NVIDIA dynamic → OpenRouter free → Gemini emergency (blocked) |

Failure handling can quarantine/cool down models or providers for model-unavailable, quota, rate-limit, server, network, auth, or account-access failures. The user conversation should not be filled with this internal failover chatter.

## Manual credential editing

If the interactive command is unavailable, edit only the managed runtime file:

```bash
mkdir -p ~/.opencloud
chmod 700 ~/.opencloud
nano ~/.opencloud/config.env
```

Example key names:

```text
NVIDIA_API_KEY=YOUR_KEY
OPENROUTER_API_KEY=YOUR_KEY
```

Then:

```bash
chmod 600 ~/.opencloud/config.env
./bin/opencloud fleet refresh
./bin/opencloud doctor
```

Never paste real keys into documentation, source, screenshots, Git history, or support issues.
