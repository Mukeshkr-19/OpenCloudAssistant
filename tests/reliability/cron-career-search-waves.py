#!/usr/bin/env python3
"""Deterministic regression coverage for code-owned career search waves (P15).

Materializes the fully-patched Hermes tree (baseline + P1..P15 via
``install/30-brain-materialize.sh``) and exercises the career-job pipeline's:

  * code-owned wave policy — which waves must run and when the next wave is
    required (the model can never stop after one candidate);
  * deterministic valid-candidate counting (only candidates that survive the
    geography/evidence validator count);
  * the bounded Wave 2 (U.S. adjacent) / Wave 3 (Tier-2) progression;
  * early-finalization blocking (Wave 2 required after a sub-target Wave 1,
    Wave 3 required after a sub-target Wave 2, and a hard stop once both have
    been driven — never an infinite loop);
  * deterministic SEARCH COVERAGE + SEARCH WAVES header rendering on every
    report (non-zero and zero-match).

Kept provider-independent and network-free: every message is synthetic.
"""

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
HERMES_ROOT = Path(os.environ.get("OPEN_CLOUD_HERMES_ROOT", Path.home() / ".hermes/hermes-agent"))

CONTRACT = "career_job_match_v1"
US_URL = "https://boards.greenhouse.io/acme/jobs/100"
IN_URL = "https://jobs.lever.co/mactores/1cdd600a-1289-4c4d-b245-9a23df9bd17c"


def materialize(out: Path) -> bool:
    result = subprocess.run(
        ["bash", str(ROOT / "install/30-brain-materialize.sh"), "--stage", str(out)],
        env={**os.environ, "OPEN_CLOUD_HERMES_ROOT": str(HERMES_ROOT)},
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        print(
            "CRON_CAREER_SEARCH_WAVES: SKIP (materialization failed): "
            + (result.stderr or result.stdout or "").strip()
        )
        return False
    return True


def _wrap(content):
    return (
        '<untrusted_tool_result source="tool">\n'
        "The following content was retrieved from an external source.\n\n"
        f"{content}\n</untrusted_tool_result>"
    )


def _extract_messages(url, content):
    """One web_search + one successful web_extract + one Vellum result."""
    search_json = json.dumps({"data": {"web": [{"url": url, "title": "Job", "content": "search result"}]}})
    extract_json = json.dumps({"results": [{"url": url, "title": "Job", "content": content, "error": None}]})
    vellum = "confirmed: AWS certified, Kubernetes experience"
    return [
        {
            "role": "assistant",
            "tool_calls": [{
                "id": "0",
                "function": {"name": "web_search",
                             "arguments": json.dumps({"query": "DevOps internship United States"})},
            }],
        },
        {"role": "tool", "name": "web_search", "tool_name": "web_search",
         "tool_call_id": "0", "content": _wrap(search_json)},
        {
            "role": "assistant",
            "tool_calls": [{
                "id": "1",
                "function": {"name": "web_extract", "arguments": json.dumps({"urls": [url]})},
            }],
        },
        {"role": "tool", "name": "web_extract", "tool_name": "web_extract",
         "tool_call_id": "1", "content": _wrap(extract_json)},
        {
            "role": "assistant",
            "tool_calls": [{
                "id": "2",
                "function": {"name": "mcp__vellum_bridge__get_user_context",
                             "arguments": json.dumps({"query": "career profile", "max_results": 12})},
            }],
        },
        {"role": "tool", "name": "mcp__vellum_bridge__get_user_context",
         "tool_name": "mcp__vellum_bridge__get_user_context", "tool_call_id": "2",
         "content": _wrap(vellum)},
    ]


def _candidate(location, url=US_URL, company="Acme", title="DevOps Engineer Intern"):
    return {
        "score": 82,
        "title": title,
        "company": company,
        "location": location,
        "type": "Internship",
        "posted": "2026-08-15",
        "salary": "Not stated — verify",
        "status": "Verified open",
        "apply_url": url,
        "jd": ["AWS", "Kubernetes", "CI/CD"],
        "why_match": ["AWS certified", "Kubernetes experience"],
        "gaps": ["No major gap identified"],
        "eligibility": {
            "requirement": "CS degree in progress",
            "user_fit": "confirmed: AWS certified, Kubernetes experience",
            "sponsorship": "Not stated — verify",
        },
    }


def _report(candidates):
    return {"date": "2026-08-19", "verified_matches": len(candidates), "candidates": candidates, "best_match": None}


def _struct(n):
    """Minimal sanitized structure with n valid candidate dicts."""
    return {"candidates": [{"title": f"Job {i}"} for i in range(n)]}


def main() -> None:
    if not (HERMES_ROOT / ".git").is_dir():
        print("CRON_CAREER_SEARCH_WAVES: SKIP (Hermes Git source unavailable)")
        return

    with tempfile.TemporaryDirectory(prefix="opencloud-cron-career-search-waves-") as tmp:
        tree = Path(tmp) / "hermes"
        if not materialize(tree):
            return

        sys.path.insert(0, str(tree))
        try:
            from cron import output_contract as oc
            from cron import scheduler as sc
        finally:
            sys.path.pop(0)

        # ── 1. Deterministic valid-candidate counting ────────────────────
        assert oc._count_valid_candidates({"candidates": []}) == 0
        assert oc._count_valid_candidates(_struct(5)) == 5
        assert oc._count_valid_candidates({"candidates": [None, 1, "x"]}) == 0
        assert oc._count_valid_candidates(None) == 0

        # ── 2. Wave policy (the model can never stop early) ──────────────
        # A / L: 5 valid candidates → no further wave (stop cleanly).
        assert oc._required_next_wave(_struct(5), {}) is None
        assert oc._required_next_wave(_struct(7), {"wave2_issued": True, "wave3_issued": True}) is None
        # B / E: 1 valid candidate after Wave 1 → Wave 2 MUST run.
        assert oc._required_next_wave(_struct(1), {}) == "wave2"
        assert oc._required_next_wave(_struct(0), {}) == "wave2"
        # D: 4 valid candidates → deterministic policy still drives Wave 2
        #     (sub-target, so more recall is worthwhile; no arbitrary stop).
        assert oc._required_next_wave(_struct(4), {}) == "wave2"
        # C / F: Wave 2 done, still sub-target → Wave 3 MUST run.
        assert oc._required_next_wave(_struct(2), {"wave2_issued": True}) == "wave3"
        assert oc._required_next_wave(_struct(1), {"wave2_issued": True}) == "wave3"
        # G / H: all waves driven → valid complete 1-match / zero-match.
        assert oc._required_next_wave(_struct(1), {"wave2_issued": True, "wave3_issued": True}) is None
        assert oc._required_next_wave(_struct(0), {"wave2_issued": True, "wave3_issued": True}) is None
        # J: hard bounds — no infinite loop (returns None once both driven).
        assert oc._required_next_wave(_struct(1), {"wave2_issued": True, "wave3_issued": True, "extra": True}) is None

        # ── 3. Wave summary is deterministic ─────────────────────────────
        waves = oc._search_waves_summary(
            {"search_queries": ["DevOps internship United States"]},
            {"wave2_issued": True, "wave3_issued": True},
        )
        assert waves == {"us_core": True, "us_expansion": True, "international": True}
        waves = oc._search_waves_summary({"search_queries": []}, {})
        assert waves == {"us_core": False, "us_expansion": False, "international": False}
        assert oc._render_search_waves(waves) == ""

        full_waves = {"us_core": True, "us_expansion": True, "international": True}
        line = oc._render_search_waves(full_waves)
        assert line == "SEARCH WAVES: US Core ✓ | US Expansion ✓ | International ✓"
        assert oc._render_search_waves({}) == ""

        # ── 4. Coverage classification (Wave-3 provider failure → Partial) ─
        cov = oc._compute_search_coverage({
            "search_queries": ["DevOps internship United States", "SRE intern Singapore"],
            "search_failures": 1,
            "extracted": {"https://e/0": "page"},
            "extract_failures": 0,
        })
        assert cov["state"] == "Partial"  # I: Wave-3 search provider failed

        # ── 5. Non-zero report header renders coverage + waves ───────────
        us_messages = _extract_messages(
            US_URL,
            "Acme DevOps Engineer Intern, New York, NY, United States. "
            "Requirements: AWS, Kubernetes, CI/CD. Status: open.",
        )
        r = oc.validate_contract(CONTRACT, json.dumps(_report([_candidate("New York, NY", url=US_URL)])), us_messages)
        assert r.valid, r.errors
        assert r.structure["verified_matches"] == 1
        rendered = oc.render_contract(CONTRACT, r.structure)
        assert "SEARCH COVERAGE:" in rendered
        assert "SEARCH PRIORITY: United States → High-paying Europe / Singapore / Malaysia" in rendered

        # With wave_state threaded, the waves line is deterministic.
        r2 = oc.validate_contract(
            CONTRACT, json.dumps(_report([_candidate("New York, NY", url=US_URL)])), us_messages,
            wave_state={"wave2_issued": True, "wave3_issued": True},
        )
        rendered2 = oc.render_contract(CONTRACT, r2.structure)
        assert "SEARCH WAVES: US Core ✓ | US Expansion ✓ | International ✓" in rendered2

        # ── 6. Scheduler gate closure: blocks a sub-target final answer ──
        gate = sc._build_career_wave_gate("2026-08-19")
        state = {"wave2_issued": False, "wave3_issued": False}
        # Valid single-US-candidate answer → Wave 2 required.
        assert gate(json.dumps(_report([_candidate("New York, NY", url=US_URL)])), us_messages, state) == "wave2"
        # Invalid (malformed) answer → no wave decision (contract handles it).
        assert gate("not json at all", us_messages, state) is None

        # ── 7. India candidate is rejected regardless of wave state ──────
        in_messages = _extract_messages(
            IN_URL,
            "Mactores DevOps Engineer Intern, Mumbai, Maharashtra, India. "
            "Requirements: AWS, Kubernetes, CI/CD. Status: open.",
        )
        r3 = oc.validate_contract(
            CONTRACT, json.dumps(_report([_candidate("Mumbai, MH", url=IN_URL, company="Mactores")])), in_messages,
            wave_state={"wave2_issued": True, "wave3_issued": True},
        )
        assert r3.valid, r3.errors  # clean zero-match, never a fake India report
        assert r3.structure["verified_matches"] == 0
        assert r3.structure["candidates"] == []

    print("PASS deterministic valid-candidate counting (code-owned)")
    print("PASS Wave 2 forced after sub-target Wave 1 (no early stop)")
    print("PASS Wave 3 forced after sub-target Wave 2")
    print("PASS 5+ candidates stop cleanly; both waves driven -> allow finalization")
    print("PASS bounded wave progression never loops")
    print("PASS SEARCH COVERAGE + SEARCH WAVES render deterministically")
    print("PASS scheduler wave gate blocks sub-target final answers")
    print("PASS India candidate rejected regardless of wave state")
    print("CRON_CAREER_SEARCH_WAVES_RELIABILITY: PASS")


if __name__ == "__main__":
    main()
