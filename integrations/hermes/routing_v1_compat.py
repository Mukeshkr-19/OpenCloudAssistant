#!/usr/bin/env python3

from pathlib import Path
import shutil
import sys


MARKER = "HERMES_ROUTING_V1_WORKLOAD_PROFILE"


def replace_once(
    text,
    old,
    new,
    label,
):
    count = text.count(old)

    if count != 1:
        raise SystemExit(
            f"ERROR: {label}: expected 1 anchor, found {count}"
        )

    return text.replace(
        old,
        new,
        1,
    )


def replace_region(
    text,
    start_marker,
    end_marker,
    replacement,
    label,
):
    start = text.find(
        start_marker
    )

    if start < 0:
        raise SystemExit(
            f"ERROR: {label}: start marker not found"
        )

    end = text.find(
        end_marker,
        start,
    )

    if end < 0:
        raise SystemExit(
            f"ERROR: {label}: end marker not found"
        )

    return (
        text[:start]
        + replacement
        + text[end:]
    )


def gateway_router():
    return '''    def _resolve_turn_agent_config(self, user_message: str, model: str, runtime_kwargs: dict) -> dict:
        """Build the effective model/runtime config for a single turn."""

        from hermes_cli.models import resolve_fast_mode_overrides
        from agent.opencloud_routing_v1 import classify_workload_profile

        # HERMES_ROUTING_V1_WORKLOAD_PROFILE
        routing_profile = classify_workload_profile(
            user_message
        )

        runtime = {
            "api_key": runtime_kwargs.get("api_key"),
            "base_url": runtime_kwargs.get("base_url"),
            "provider": runtime_kwargs.get("provider"),
            "requested_provider": runtime_kwargs.get("requested_provider"),
            "api_mode": runtime_kwargs.get("api_mode"),
            "command": runtime_kwargs.get("command"),
            "args": list(runtime_kwargs.get("args") or []),
            "credential_pool": runtime_kwargs.get("credential_pool"),
            "max_tokens": runtime_kwargs.get("max_tokens"),
        }

        route = {
            "model": model,
            "runtime": runtime,
            "routing_profile": routing_profile,
            "signature": (
                model,
                runtime["provider"],
                runtime["requested_provider"],
                runtime["base_url"],
                runtime["api_mode"],
                runtime["command"],
                tuple(runtime["args"]),
                routing_profile,
            ),
        }

        service_tier = getattr(
            self,
            "_service_tier",
            None,
        )

        if service_tier:
            try:
                overrides = (
                    resolve_fast_mode_overrides(
                        route["model"]
                    )
                )
            except Exception:
                overrides = None
        else:
            overrides = None

        request_overrides = dict(
            overrides
            or {}
        )

        # Internal OpenCloud control metadata. agent_init consumes and removes
        # this key before any provider request is constructed.
        request_overrides[
            "_opencloud_routing_profile"
        ] = routing_profile

        route[
            "request_overrides"
        ] = request_overrides

        return route

'''


def cli_router():
    return '''    def _resolve_turn_agent_config(self, user_message: str) -> dict:
        """Build the effective model/runtime config for a single user turn."""

        from hermes_cli.models import resolve_fast_mode_overrides
        from agent.opencloud_routing_v1 import classify_workload_profile

        # HERMES_ROUTING_V1_WORKLOAD_PROFILE
        routing_profile = classify_workload_profile(
            user_message
        )

        runtime = {
            "api_key": self.api_key,
            "base_url": self.base_url,
            "provider": self.provider,
            "requested_provider": getattr(
                self,
                "requested_provider",
                self.provider,
            ),
            "api_mode": self.api_mode,
            "command": self.acp_command,
            "args": list(
                self.acp_args
                or []
            ),
            "credential_pool": getattr(
                self,
                "_credential_pool",
                None,
            ),
        }

        route = {
            "model": self.model,
            "runtime": runtime,
            "routing_profile": routing_profile,
            "signature": (
                self.model,
                runtime["provider"],
                runtime["requested_provider"],
                runtime["base_url"],
                runtime["api_mode"],
                runtime["command"],
                tuple(runtime["args"]),
                routing_profile,
            ),
        }

        service_tier = getattr(
            self,
            "service_tier",
            None,
        )

        if service_tier:
            try:
                overrides = (
                    resolve_fast_mode_overrides(
                        route["model"]
                    )
                )
            except Exception:
                overrides = None
        else:
            overrides = None

        request_overrides = dict(
            overrides
            or {}
        )

        request_overrides[
            "_opencloud_routing_profile"
        ] = routing_profile

        route[
            "request_overrides"
        ] = request_overrides

        return route

'''


def main():
    if len(sys.argv) != 2:
        raise SystemExit(
            "usage: routing_v1_compat.py HERMES_TREE"
        )

    tree = Path(
        sys.argv[1]
    )

    gateway = (
        tree
        / "gateway/run.py"
    )

    cli_setup = (
        tree
        / "hermes_cli/cli_agent_setup_mixin.py"
    )

    agent_init = (
        tree
        / "agent/agent_init.py"
    )

    helper_src = (
        Path(__file__).resolve().parent
        / "opencloud_routing_v1.py"
    )

    helper_dst = (
        tree
        / "agent/opencloud_routing_v1.py"
    )

    for path in (
        gateway,
        cli_setup,
        agent_init,
        helper_src,
    ):
        if not path.is_file():
            raise SystemExit(
                f"ERROR: missing Routing V1 input: {path}"
            )

    helper_dst.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    shutil.copy2(
        helper_src,
        helper_dst,
    )

    # --------------------------------------------------------
    # Gateway per-turn router.
    # --------------------------------------------------------

    text = gateway.read_text()

    if MARKER not in text:
        text = replace_region(
            text,
            "    def _resolve_turn_agent_config(self, user_message: str, model: str, runtime_kwargs: dict) -> dict:\n",
            "    def _sync_session_model_from_agent(",
            gateway_router(),
            "gateway turn router",
        )

    # Gateway cache signature must vary with Routing V1 profile.
    cache_anchor = '''        agent = None
        reused_cached_agent = False
'''

    cache_replacement = '''        # HERMES_ROUTING_V1_AGENT_CACHE_PROFILE
        # The upstream cache signature does not include request_overrides.
        # Extend it with the workload profile so FAST/BALANCED/DEEP changes
        # rebuild the AIAgent instead of reusing the previous profile.
        _sig = (
            _sig,
            turn_route.get(
                "routing_profile"
            ),
        )

        agent = None
        reused_cached_agent = False
'''

    if "HERMES_ROUTING_V1_AGENT_CACHE_PROFILE" not in text:
        text = replace_once(
            text,
            cache_anchor,
            cache_replacement,
            "gateway agent-cache profile",
        )

    gateway.write_text(
        text
    )

    # --------------------------------------------------------
    # CLI per-turn router parity.
    # --------------------------------------------------------

    text = cli_setup.read_text()

    if MARKER not in text:
        text = replace_region(
            text,
            "    def _resolve_turn_agent_config(self, user_message: str) -> dict:\n",
            "    def _init_agent(",
            cli_router(),
            "CLI turn router",
        )

    cli_setup.write_text(
        text
    )

    # --------------------------------------------------------
    # Consume the internal profile in agent_init and pass it to
    # Fleet. Remove it before provider request_overrides exist.
    # --------------------------------------------------------

    text = agent_init.read_text()

    profile_anchor = '''    _hermes_fleet_bootstrap = None
    _hermes_fleet_session_key = gateway_session_key

    try:
'''

    profile_replacement = '''    _hermes_fleet_bootstrap = None
    _hermes_fleet_session_key = gateway_session_key

    # HERMES_ROUTING_V1_AGENT_PROFILE
    _hermes_fleet_profile = None

    if isinstance(
        request_overrides,
        dict,
    ):
        request_overrides = dict(
            request_overrides
        )

        _hermes_fleet_profile = (
            request_overrides.pop(
                "_opencloud_routing_profile",
                None,
            )
        )

    try:
'''

    if "HERMES_ROUTING_V1_AGENT_PROFILE" not in text:
        text = replace_once(
            text,
            profile_anchor,
            profile_replacement,
            "agent routing profile extraction",
        )

    resolve_anchor = '''            _hermes_fleet_bootstrap = _fleet_resolve(
                "main",
                session_key=_hermes_fleet_session_key,
            )
'''

    resolve_replacement = '''            _hermes_fleet_bootstrap = _fleet_resolve(
                "main",
                session_key=_hermes_fleet_session_key,
                profile=_hermes_fleet_profile,
            )
'''

    if "profile=_hermes_fleet_profile" not in text:
        text = replace_once(
            text,
            resolve_anchor,
            resolve_replacement,
            "Fleet profile propagation",
        )

    agent_init.write_text(
        text
    )

    # --------------------------------------------------------
    # Cron jobs do not have a gateway_session_key, so the
    # normal main-agent Fleet attachment intentionally does not
    # manage them. A job with routing_profile therefore resolves
    # Fleet explicitly before AIAgent construction.
    # --------------------------------------------------------

    scheduler = (
        tree
        / "cron/scheduler.py"
    )

    if scheduler.is_file():
        text = scheduler.read_text()

        # --------------------------------------------------------
        # Routing V1 cron inference.
        #
        # A cron routing_profile is authoritative. The native Hermes
        # model/provider resolution and drift guard must execute only
        # for legacy jobs without a Routing V1 profile.
        # --------------------------------------------------------

        profile_prelude_anchor = """        if not (isinstance(model, str) and model.strip()):
"""

        profile_prelude = """        # HERMES_ROUTING_V1_CRON_PROFILE_PRELUDE
        _cron_fleet_route = None
        _cron_fleet_request_overrides = None

        _raw_cron_routing_profile = str(
            job.get("routing_profile")
            or ""
        ).strip()

        if not (isinstance(model, str) and model.strip()):
"""

        if "HERMES_ROUTING_V1_CRON_PROFILE_PRELUDE" not in text:
            text = replace_once(
                text,
                profile_prelude_anchor,
                profile_prelude,
                "cron Routing V1 profile prelude",
            )

        no_model_anchor = """        if not (isinstance(model, str) and model.strip()):
"""

        no_model_replacement = """        if (
            not _raw_cron_routing_profile
            and not (
                isinstance(model, str)
                and model.strip()
            )
        ):
"""

        if (
            "not _raw_cron_routing_profile"
            not in text[
                text.find(
                    "HERMES_ROUTING_V1_CRON_PROFILE_PRELUDE"
                ):
                text.find(
                    "HERMES_ROUTING_V1_CRON_PROFILE_PRELUDE"
                )
                + 2500
            ]
        ):
            text = replace_once(
                text,
                no_model_anchor,
                no_model_replacement,
                "cron Routing V1 no-model bypass",
            )

        legacy_start = """        _guard_job_credential_exfil(job)
"""

        legacy_end = """        fallback_model = get_fallback_chain(_cfg) or None
"""

        legacy_start_pos = text.find(
            legacy_start
        )

        if legacy_start_pos < 0:
            raise SystemExit(
                "ERROR: cron legacy inference start missing"
            )

        legacy_end_pos = text.find(
            legacy_end,
            legacy_start_pos,
        )

        if legacy_end_pos < 0:
            raise SystemExit(
                "ERROR: cron legacy inference end missing"
            )

        legacy_end_pos += len(
            legacy_end
        )

        legacy_block = text[
            legacy_start_pos:
            legacy_end_pos
        ]

        indented_legacy = "".join(
            (
                "    " + line
                if line.strip()
                else line
            )
            for line
            in legacy_block.splitlines(
                keepends=True
            )
        )

        fleet_branch = """        # HERMES_ROUTING_V1_CRON_PROFILE
        if _raw_cron_routing_profile:
            from agent.opencloud_routing_v1 import (
                normalize_routing_profile,
            )

            _cron_routing_profile = (
                normalize_routing_profile(
                    _raw_cron_routing_profile
                )
            )

            try:
                from agent.hermes_fleet_bridge import (
                    resolve_role as _fleet_resolve,
                )

                _cron_fleet_route = (
                    _fleet_resolve(
                        "main",
                        profile=_cron_routing_profile,
                    )
                )

                _fleet_candidate = (
                    _cron_fleet_route[
                        "candidate"
                    ]
                )

                _fleet_runtime = dict(
                    _cron_fleet_route[
                        "runtime"
                    ]
                )

                model = str(
                    _fleet_candidate[
                        "model"
                    ]
                ).strip()

                if not model:
                    raise RuntimeError(
                        "Fleet returned an empty model"
                    )

                if not _fleet_runtime.get(
                    "provider"
                ):
                    _fleet_runtime[
                        "provider"
                    ] = _fleet_candidate[
                        "provider"
                    ]

                if not _fleet_runtime.get(
                    "requested_provider"
                ):
                    _fleet_runtime[
                        "requested_provider"
                    ] = _fleet_candidate[
                        "provider"
                    ]

                runtime = _fleet_runtime

                fallback_model = list(
                    _cron_fleet_route.get(
                        "fallback_chain"
                    )
                    or []
                )

                _fleet_overrides = (
                    _fleet_runtime.get(
                        "request_overrides"
                    )
                )

                if isinstance(
                    _fleet_overrides,
                    dict,
                ):
                    _cron_fleet_request_overrides = dict(
                        _fleet_overrides
                    )

                reasoning_config = (
                    resolve_reasoning_config(
                        _cfg
                        if isinstance(
                            _cfg,
                            dict,
                        )
                        else {},
                        str(model),
                    )
                )

                logger.info(
                    "Job '%s': Routing V1 profile=%s selected %s/%s",
                    job_id,
                    _cron_routing_profile,
                    runtime.get(
                        "provider"
                    ),
                    model,
                )

            except Exception as _fleet_exc:
                raise RuntimeError(
                    "Cron Routing V1 profile "
                    f"{_cron_routing_profile!r} "
                    "could not resolve a healthy Fleet route"
                ) from _fleet_exc

        else:
"""

        replacement_block = (
            fleet_branch
            + indented_legacy
        )

        text = (
            text[:legacy_start_pos]
            + replacement_block
            + text[legacy_end_pos:]
        )

        cron_request_anchor = """            fallback_model=fallback_model,
            credential_pool=credential_pool,
"""

        cron_request_replacement = """            fallback_model=fallback_model,
            credential_pool=credential_pool,
            request_overrides=_cron_fleet_request_overrides,
"""

        if (
            "request_overrides=_cron_fleet_request_overrides"
            not in text
        ):
            text = replace_once(
                text,
                cron_request_anchor,
                cron_request_replacement,
                "cron Fleet request overrides",
            )

        cron_stamp_anchor = """        _raw_cron_timeout = os.getenv("HERMES_CRON_TIMEOUT", "").strip()
"""

        cron_stamp_replacement = """        if _cron_fleet_route is not None:
            agent._hermes_fleet_role = "main"
            agent._hermes_fleet_candidate = dict(
                _cron_fleet_route[
                    "candidate"
                ]
            )
            agent._hermes_fleet_session_key = None

        _raw_cron_timeout = os.getenv("HERMES_CRON_TIMEOUT", "").strip()
"""

        if (
            "agent._hermes_fleet_candidate = dict("
            not in text[
                text.find(
                    "HERMES_ROUTING_V1_CRON_PROFILE"
                ):
            ]
        ):
            text = replace_once(
                text,
                cron_stamp_anchor,
                cron_stamp_replacement,
                "cron Fleet candidate stamp",
            )

        silence_start = """            # Cron silence suppression — see _is_cron_silence_response."""

        silence_end = """            if should_deliver:
"""

        silence_replacement = """            # HERMES_CRON_STRICT_SILENT_DELIVERY_V1
            # Only an exact normalized [SILENT] response suppresses delivery.
            # If a model appends/prepends the marker around real content,
            # strip the standalone marker and preserve the actual report.
            if should_deliver and success:
                from agent.opencloud_routing_v1 import (
                    sanitize_cron_delivery_content,
                )

                (
                    _cron_suppress_delivery,
                    _cron_clean_delivery,
                ) = sanitize_cron_delivery_content(
                    deliver_content
                )

                if _cron_suppress_delivery:
                    logger.info(
                        "Job '%s': agent returned exact %s — skipping delivery",
                        job["id"],
                        SILENT_MARKER,
                    )
                    should_deliver = False

                elif (
                    _cron_clean_delivery
                    != deliver_content.strip()
                ):
                    logger.warning(
                        "Job '%s': stripped standalone %s from mixed cron response",
                        job["id"],
                        SILENT_MARKER,
                    )

                    deliver_content = (
                        _cron_clean_delivery
                    )

                    should_deliver = bool(
                        deliver_content
                    )

"""

        if "HERMES_CRON_STRICT_SILENT_DELIVERY_V1" not in text:
            text = replace_region(
                text,
                silence_start,
                silence_end,
                silence_replacement,
                "strict cron silence delivery",
            )

        scheduler.write_text(
            text
        )

    print(
        "HERMES_ROUTING_V1_COMPAT: PASS"
    )


if __name__ == "__main__":
    main()
