# Private Task Profiles

OpenCloudAssistant provides reusable execution controls; private local profiles
provide task-specific prompts, schedules, research topics, and output policy.
Private profiles are not installed from or stored in this repository.

Create matching files outside Git:

- `~/.opencloud/task-profiles/NAME.json` — private OpenCloud capability policy;
- `~/.hermes/profiles/NAME/config.yaml` — the matching Hermes profile.

Then run `opencloud task-profile apply --name NAME` and
`opencloud task-profile verify --name NAME`. Missing or malformed profiles fail
closed. Verification reports structural and capability errors without printing
prompts or private values.

A restrictive `read-only-research` profile configures parent turns, child
iterations, timeout, concurrency, depth, platform toolsets, and explicit MCP
`tools.include` lists. Vellum read-only research may include only
`get_user_context`; it does not receive `repair_code`. Children inherit the
parent's MCP toolsets by intersection.

Final-only BlueBubbles presentation disables progress, reasoning, streaming,
interim messages, and configured long-running notices while preserving the final
message for the adapter's ordered chunking behavior.

An optional `task` object materializes one idempotent cron job inside the
matching private Hermes profile. It accepts `name`, `schedule`, `prompt`,
`research_topics`, `use_vellum_context`, `output_policy`, `scoring_policy`, and
`deliver`. OpenCloud records only the managed job ID beside the private JSON in
`NAME.state.json` (mode `0600`); neither file belongs in Git. The job receives
the profile's restricted toolsets, and `use_vellum_context` is rejected unless
the MCP allowlist contains `get_user_context`.

OpenCloud enables Hermes' upstream `gateway.multiplex_profiles` mechanism on
the default gateway. The pinned gateway enumerates named profile homes and its
built-in scheduler ticks every profile's isolated cron store under that
profile's `HERMES_HOME`, configuration, environment, tools, and secrets. No
parallel scheduler or permanent worker-agent pool is added. A materialized job
therefore requires the ordinary `hermes-gateway.service` to be active; doctor
reports a configured job without that ticker as a failure.

Synthetic shape (values are examples, not an installed profile):

```json
{
  "version": 1,
  "mode": "read-only-research",
  "parent_max_turns": 15,
  "max_concurrent_children": 4,
  "child_max_iterations": 12,
  "child_timeout_seconds": 120,
  "max_spawn_depth": 2,
  "enabled_toolsets": ["web", "delegation", "vellum-bridge"],
  "mcp_tools": {"vellum-bridge": {"include": ["get_user_context"]}},
  "task": {
    "name": "Example Research Profile",
    "schedule": "every 1d",
    "prompt": "Research the synthetic example.",
    "research_topics": ["Example Project"],
    "use_vellum_context": true,
    "output_policy": {"format": "summary"},
    "scoring_policy": {"scale": "example"},
    "deliver": "local"
  }
}
```
