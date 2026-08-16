#!/usr/bin/env python3

from __future__ import annotations

import os
from pathlib import Path


HERMES = Path(
    os.environ.get(
        "OPEN_CLOUD_HERMES_ROOT",
        Path.home() / ".hermes/hermes-agent",
    )
)

scheduler = HERMES / "cron/scheduler.py"

if not scheduler.is_file():
    raise SystemExit(
        "ERROR: Hermes cron scheduler unavailable"
    )

source = scheduler.read_text()


def require(condition, message):
    if not condition:
        raise AssertionError(message)


prelude = source.find(
    "HERMES_ROUTING_V1_CRON_PROFILE_PRELUDE"
)

profile = source.find(
    "HERMES_ROUTING_V1_CRON_PROFILE",
    prelude + 1,
)

guard = source.find(
    "_guard_job_credential_exfil(job)",
    profile,
)

drift = source.find(
    "cron_model_drift_guard_enabled(_cfg)",
    profile,
)

agent = source.find(
    "agent = AIAgent(",
    profile,
)

request_overrides = source.find(
    "request_overrides=_cron_fleet_request_overrides",
    agent,
)

silence = source.find(
    "HERMES_CRON_STRICT_SILENT_DELIVERY_V1",
    agent,
)


require(
    prelude >= 0,
    "Routing V1 cron prelude marker missing",
)

require(
    profile > prelude,
    "Routing V1 cron profile branch missing",
)

require(
    guard > profile,
    "legacy credential guard must occur after profile branch",
)

require(
    drift > profile,
    "legacy drift guard must occur after profile branch",
)

require(
    agent > profile,
    "AIAgent construction must occur after profile resolution",
)

require(
    request_overrides > agent,
    "Fleet request overrides must reach cron AIAgent",
)

require(
    silence > agent,
    "strict cron silence gate missing",
)


# The legacy inference path must now be inside the profile branch's else.
segment = source[
    profile:guard + len(
        "_guard_job_credential_exfil(job)"
    )
]

require(
    "\n        else:\n" in segment,
    "legacy inference must live under Routing V1 else branch",
)

require(
    "\n            _guard_job_credential_exfil(job)"
    in segment,
    "legacy credential guard must be indented under else branch",
)


# The old mixed-content silence matcher must not remain in the
# final delivery block after the new strict boundary.
delivery_segment = source[
    silence:
    source.find(
        "finally:",
        silence,
    )
]

require(
    "_is_cron_silence_response(deliver_content)"
    not in delivery_segment,
    "legacy silence matcher still active after strict gate",
)

require(
    "sanitize_cron_delivery_content"
    in delivery_segment,
    "strict silence sanitizer not used by scheduler",
)


print(
    "CRON_ROUTING_V1_RELIABILITY: PASS"
)
