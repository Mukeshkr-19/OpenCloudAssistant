#!/usr/bin/env python3
"""Deterministic, network-free tests for the career search reliability layer."""

import asyncio
import concurrent.futures
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HERMES_ROOT = Path(os.environ.get("OPEN_CLOUD_HERMES_ROOT", Path.home() / ".hermes/hermes-agent"))


def materialize(out: Path) -> bool:
    result = subprocess.run(
        ["bash", str(ROOT / "install/30-brain-materialize.sh"), "--stage", str(out)],
        env={**os.environ, "OPEN_CLOUD_HERMES_ROOT": str(HERMES_ROOT)},
        capture_output=True,
        text=True,
    )
    if result.returncode:
        print("CRON_SEARCH_RELIABILITY: SKIP (materialization failed): " + (result.stderr or result.stdout).strip())
        return False
    return True


def main() -> None:
    if not (HERMES_ROOT / ".git").is_dir():
        print("CRON_SEARCH_RELIABILITY: SKIP (Hermes Git source unavailable)")
        return
    with tempfile.TemporaryDirectory(prefix="opencloud-search-reliability-") as tmp:
        tree = Path(tmp) / "hermes"
        if not materialize(tree):
            return
        sys.path.insert(0, str(tree))
        try:
            from cron.search_reliability import (
                CareerSearchController,
                SEARCH_AUTH_FAILURE,
                SEARCH_RATE_LIMIT,
                SEARCH_SUCCESS_EMPTY,
                SEARCH_TIMEOUT,
                SEARCH_UNAVAILABLE,
                classify_search_error,
                classify_search_response,
                bind_controller,
                reset_controller,
            )
            from cron import output_contract
            from tools import web_tools
        finally:
            sys.path.pop(0)

        # Empty results are success, while actual failure classes remain typed.
        assert classify_search_error("No results found") == SEARCH_SUCCESS_EMPTY
        assert classify_search_response({"success": True, "data": {"web": []}}) == SEARCH_SUCCESS_EMPTY
        assert classify_search_error("HTTP 429 Too Many Requests") == SEARCH_RATE_LIMIT
        assert classify_search_error("request timed out") == SEARCH_TIMEOUT
        assert classify_search_error("HTTP 403 forbidden") == SEARCH_AUTH_FAILURE
        assert classify_search_response({"success": False, "error": "provider exploded"}) != SEARCH_SUCCESS_EMPTY

        # Empty success neither increments failures nor opens a circuit.
        controller = CareerSearchController()
        for query in ("empty one", "empty two", "empty three"):
            assert controller.allow_search_attempt(query, "ddgs")[0]
            assert controller.observe_search(query, "ddgs", {"success": True, "data": {"web": []}}) == SEARCH_SUCCESS_EMPTY
        state = controller.states["ddgs"]
        assert state.empty_successes == 3
        assert state.failures == 0 and not state.circuit_open

        # One failure continues; two distinct failures open the run-local circuit.
        controller = CareerSearchController()
        assert controller.allow_search_attempt("q1", "ddgs")[0]
        controller.observe_search("q1", "ddgs", {"success": False, "error": "HTTP 503"})
        assert not controller.states["ddgs"].circuit_open
        assert controller.allow_search_attempt("q2", "ddgs")[0]
        controller.observe_search("q2", "ddgs", {"success": False, "error": "HTTP 503"})
        assert controller.states["ddgs"].circuit_open
        assert controller.allow_search_attempt("q3", "ddgs")[0] is False
        assert controller.allow_search_attempt("q3", "ddgs")[1] == SEARCH_UNAVAILABLE

        # Same query repeated does not inflate distinct-failure health.
        controller = CareerSearchController()
        for _ in range(4):
            assert controller.allow_search_attempt("same", "ddgs")[0]
            controller.observe_search("same", "ddgs", {"success": False, "error": "network down"})
        assert not controller.states["ddgs"].circuit_open

        # Hard call bound is deterministic and future runs start clean.
        controller = CareerSearchController()
        controller.search_calls = 18
        assert controller.allow_search_attempt("bounded", "ddgs") == (False, SEARCH_UNAVAILABLE)
        fresh = CareerSearchController()
        assert fresh.search_calls == 0 and not fresh.states

        # Parallel tool-call execution cannot race past the run budget.
        concurrent_controller = CareerSearchController()
        with concurrent.futures.ThreadPoolExecutor(max_workers=32) as pool:
            decisions = list(pool.map(
                lambda i: concurrent_controller.allow_search_attempt(
                    f"parallel-{i}", "ddgs"
                )[0],
                range(64),
            ))
        assert sum(decisions) == 18
        assert concurrent_controller.search_calls == 18

        fallback_controller = CareerSearchController()
        with concurrent.futures.ThreadPoolExecutor(max_workers=16) as pool:
            claims = list(pool.map(
                lambda _: fallback_controller.note_fallback("ddgs", "firecrawl"),
                range(32),
            ))
        assert sum(claims) == 1
        assert fallback_controller.provider_switches == 1

        # Query policy removes placeholders and supplies role/stage/geography.
        query, error = fresh.normalize_query("Cloud Engineering internships near me companycareers.com")
        assert error is None
        assert "companycareers.com" not in query
        assert "United States" in query
        assert "internship" in query
        assert "Cloud" in query or "cloud" in query

        # The supported alternate is wired in the career path; no invented
        # backend is used.
        web_tools_source = (tree / "tools/web_tools.py").read_text()
        assert '"firecrawl"' in web_tools_source
        assert "CAREER_SEARCH_FALLBACK" in (tree / "cron/search_reliability.py").read_text()

        # Extraction binds its own run-local controller. The production bug
        # referenced web_search_tool's local variable and raised NameError.
        controller = CareerSearchController()
        token = bind_controller(controller)
        try:
            extract_result = json.loads(asyncio.run(web_tools.web_extract_tool([])))
        finally:
            reset_controller(token)
        assert "NameError" not in json.dumps(extract_result)
        assert controller.extract_calls == 1

        # Empty status is not a coverage failure; an explicit provider failure is.
        empty_coverage = output_contract._compute_search_coverage({
            "search_queries": ["DevOps internship United States"],
            "search_statuses": [SEARCH_SUCCESS_EMPTY],
            "search_failures": 0,
            "extracted": {"https://example.test/job": "posting"},
            "extract_failures": 0,
        })
        assert empty_coverage["state"] == "Complete"
        failed_coverage = output_contract._compute_search_coverage({
            "search_queries": ["DevOps internship United States"],
            "search_statuses": ["SEARCH_PROVIDER_FAILURE"],
            "search_failures": 0,
            "extracted": {"https://example.test/job": "posting"},
            "extract_failures": 0,
        })
        assert failed_coverage["state"] == "Partial"

    print("PASS empty search is successful and typed")
    print("PASS provider failure classes remain distinct")
    print("PASS one failure continues")
    print("PASS distinct failures open a run-local circuit")
    print("PASS repeated same-query failures do not open the circuit")
    print("PASS open circuit prevents further provider calls")
    print("PASS hard search bound and per-run reset")
    print("PASS parallel search calls cannot exceed the run budget")
    print("PASS parallel provider fallback remains single-switch")
    print("PASS deterministic query normalization and geography")
    print("PASS web extraction binds the run-local career controller")
    print("PASS empty results do not degrade coverage")
    print("PASS provider failures degrade coverage")
    print("CRON_SEARCH_RELIABILITY: PASS")


if __name__ == "__main__":
    main()
