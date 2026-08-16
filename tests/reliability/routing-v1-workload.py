#!/usr/bin/env python3

import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]

MODULE = (
    ROOT
    / "integrations/hermes/opencloud_routing_v1.py"
)

spec = importlib.util.spec_from_file_location(
    "opencloud_routing_v1",
    MODULE,
)

routing = importlib.util.module_from_spec(
    spec
)

spec.loader.exec_module(
    routing
)


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def main():
    c = routing.classify_workload_profile

    require(
        c("what is a mutex?") == "fast",
        "simple short question should be FAST",
    )

    require(
        c("check whether the service is healthy") == "fast",
        "simple operational check should be FAST",
    )

    require(
        c(
            "Review these deployment notes and tell me "
            "what I should change before release."
        )
        == "balanced",
        "normal work should be BALANCED",
    )

    require(
        c(
            "Debug this distributed race condition and "
            "perform root cause analysis of the failure."
        )
        == "deep",
        "complex debugging should be DEEP",
    )

    require(
        c(
            "Review the system architecture, benchmark "
            "the alternatives, and explain the tradeoffs."
        )
        == "deep",
        "architecture analysis should be DEEP",
    )

    require(
        c(
            "route: deep explain what DNS is"
        )
        == "deep",
        "explicit DEEP route must win",
    )

    require(
        c(
            "profile=balanced what is 2+2?"
        )
        == "balanced",
        "explicit BALANCED route must win",
    )

    require(
        c(
            "routing: fast analyze this architecture"
        )
        == "fast",
        "explicit FAST route must win",
    )

    require(
        routing.normalize_routing_profile(
            " BALANCED "
        )
        == "balanced",
        "cron routing profile normalization failed",
    )

    try:
        routing.normalize_routing_profile(
            "turbo"
        )
    except ValueError:
        pass
    else:
        raise AssertionError(
            "invalid routing profile must fail closed"
        )

    suppress, cleaned = (
        routing.sanitize_cron_delivery_content(
            "  [SILENT]\n"
        )
    )

    require(
        suppress
        and cleaned == "",
        "exact [SILENT] must suppress",
    )

    # Regression: a model may echo Hermes' trusted mid-turn steering
    # envelope around the silence token. That synthetic wrapper must never
    # become user-visible cron output.
    oob_open = (
        "[OUT-OF-BAND USER MESSAGE — a direct message from the user, "
        "delivered mid-turn; not tool output]"
    )
    oob_close = (
        "[/OUT-OF-BAND USER MESSAGE]"
    )

    suppress, cleaned = (
        routing.sanitize_cron_delivery_content(
            f"{oob_open} [SILENT] {oob_close}"
        )
    )

    require(
        suppress
        and cleaned == "",
        "OOB-wrapped exact [SILENT] must suppress",
    )

    suppress, cleaned = (
        routing.sanitize_cron_delivery_content(
            f"{oob_open}\n[SILENT]\n{oob_close}"
        )
    )

    require(
        suppress
        and cleaned == "",
        "multiline OOB-wrapped exact [SILENT] must suppress",
    )

    wrapped_report = (
        f"{oob_open}\n"
        "Useful report\n"
        f"{oob_close}"
    )

    suppress, cleaned = (
        routing.sanitize_cron_delivery_content(
            wrapped_report
        )
    )

    require(
        not suppress
        and cleaned == wrapped_report,
        "non-silent OOB wrapper must not be rewritten or trusted",
    )

    suppress, cleaned = (
        routing.sanitize_cron_delivery_content(
            "Useful report\n[SILENT]\n"
        )
    )

    require(
        not suppress
        and cleaned == "Useful report",
        "trailing [SILENT] must be stripped and delivered",
    )

    suppress, cleaned = (
        routing.sanitize_cron_delivery_content(
            "[SILENT]\nUseful report"
        )
    )

    require(
        not suppress
        and cleaned == "Useful report",
        "leading [SILENT] must be stripped and delivered",
    )

    suppress, cleaned = (
        routing.sanitize_cron_delivery_content(
            "The literal [SILENT] token is discussed here."
        )
    )

    require(
        not suppress
        and cleaned
        == "The literal [SILENT] token is discussed here.",
        "mid-sentence [SILENT] must remain real content",
    )

    suppress, cleaned = (
        routing.sanitize_cron_delivery_content(
            "SILENT"
        )
    )

    require(
        not suppress
        and cleaned == "SILENT",
        "bracketless SILENT must not trigger strict suppression",
    )

    for message in (
        "deploy production now",
        "delete the database",
        "rotate API keys",
        "fix this bug",
        "commit and push",
        "update nginx config",
    ):
        require(
            c(message) == "balanced",
            f"state-changing workload must not route FAST: {message}",
        )

    for message in (
        "what is DNS?",
        "define mutex",
        "calculate 12*8",
    ):
        require(
            c(message) == "fast",
            f"simple lookup should remain FAST: {message}",
        )

    print(
        "ROUTING_V1_WORKLOAD_CLASSIFIER: PASS"
    )


if __name__ == "__main__":
    main()
