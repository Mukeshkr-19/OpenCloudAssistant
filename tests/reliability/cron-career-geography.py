#!/usr/bin/env python3
"""Deterministic regression coverage for the career geography + search policy.

Materializes the fully-patched Hermes tree (baseline + P1..P14 via
``install/30-brain-materialize.sh``) and exercises the career-job pipeline's:

  * geography hard gate (US primary, Europe/Singapore/Malaysia Tier-2,
    default-reject India/China/Australia/Canada/etc., remote-never-guessed);
  * location provenance (extracted page overrides the model's location);
  * deterministic search coverage (Complete / Partial / Incomplete);
  * zero-vs-partial report states (never present partial retrieval as
    authoritative zero-match);
  * deterministic ranking (score, geography priority, eligibility, role,
    verified compensation) and internal salary normalization.

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
            "CRON_CAREER_GEOGRAPHY: SKIP (materialization failed): "
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
    """One successful web_extract + one Vellum context result."""
    extract_json = json.dumps({"results": [{"url": url, "title": "Job", "content": content, "error": None}]})
    vellum = "confirmed: AWS certified, Kubernetes experience"
    return [
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


def _candidate(location, url=US_URL, score=82, company="Acme", title="DevOps Engineer Intern"):
    return {
        "score": score,
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


def _report(candidates, verified_matches=None, best_match=None, date="2026-08-19"):
    return {
        "date": date,
        "verified_matches": len(candidates) if verified_matches is None else verified_matches,
        "candidates": candidates,
        "best_match": best_match,
    }


def main() -> None:
    if not (HERMES_ROOT / ".git").is_dir():
        print("CRON_CAREER_GEOGRAPHY: SKIP (Hermes Git source unavailable)")
        return

    with tempfile.TemporaryDirectory(prefix="opencloud-cron-career-geography-") as tmp:
        tree = Path(tmp) / "hermes"
        if not materialize(tree):
            return

        sys.path.insert(0, str(tree))
        try:
            from cron import output_contract as oc
        finally:
            sys.path.pop(0)

        g = oc._resolve_geography

        # ── 1. Geography hard gate (matrix from the career policy) ───────
        # Reject: India (any form) before scoring.
        assert g("Mumbai, MH", "Mactores DevOps Engineer Intern, Mumbai, India")[0] is False
        assert g("Bengaluru, India", "DevOps intern Bangalore")[0] is False
        assert g("Remote India", "work from home, India")[0] is False
        # Reject: other unapproved markets.
        assert g("Toronto, Canada", "Canada")[0] is False
        assert g("Sydney, Australia", "Australia")[0] is False
        # Accept: United States (all normalized forms).
        assert g("United States", "") == (True, "US", "")
        assert g("New York, NY", "New York DevOps intern")[0] is True
        assert g("Remote - United States", "") == (True, "US", "")
        assert g("US nationwide remote", "") == (True, "US", "")
        assert g("Austin, TX / San Francisco, CA", "") == (True, "US", "")
        # Accept: Tier-2 markets.
        assert g("Singapore", "") == (True, "SG", "")
        assert g("Malaysia", "") == (True, "MY", "")
        assert g("Germany", "") == (True, "EU", "")
        assert g("Ireland", "") == (True, "EU", "")
        assert g("Netherlands", "") == (True, "EU", "")
        # Location provenance: extracted page overrides the model's claim.
        assert g("New York, NY", "Mumbai, India")[0] is False  # model NY, page Mumbai
        assert g("Remote", "India-only employment")[0] is False  # model remote, page India
        # Ambiguous remote geography is never guessed.
        assert g("Remote", "")[0] is False

        # ── 2. Salary normalization (internal ranking only) ──────────────
        assert oc._normalize_compensation("$35/hour") == 72800.0
        assert oc._normalize_compensation("€4,500/month") == 58320.0
        assert oc._normalize_compensation("SGD 6,000/month") == 53280.0
        assert oc._normalize_compensation("MYR 5,000/month") == 13200.0
        # Never fabricate: sentinel and estimate markers degrade to None.
        assert oc._normalize_compensation("Not stated — verify") is None
        assert oc._normalize_compensation("$40k estimated") is None
        assert oc._normalize_compensation("Salary not disclosed") is None

        # ── 3. Search coverage states ─────────────────────────────────────
        def cov(queries, search_failures=0, extract_ok=0, extract_failures=0):
            extracted = {f"https://e/{i}": "page text" for i in range(extract_ok)}
            return oc._compute_search_coverage({
                "search_queries": queries,
                "search_failures": search_failures,
                "extracted": extracted,
                "extract_failures": extract_failures,
            })

        # Complete: searched + extracted, no failures.
        assert cov(["DevOps internship United States", "SRE intern United States"], extract_ok=2)["state"] == "Complete"
        # Complete zero-match requires pages to have been extracted.
        assert cov(["DevOps internship United States"], extract_ok=0)["state"] != "Complete"
        # Incomplete: search never executed, nothing extracted.
        assert cov([], extract_ok=0)["state"] == "Incomplete"
        # Partial: a later search wave failed.
        assert cov(["DevOps internship United States"], search_failures=1, extract_ok=1)["state"] == "Partial"
        # Partial: extraction failed.
        assert cov(["DevOps internship United States"], extract_ok=0, extract_failures=2)["state"] == "Partial"

        # ── 4. Zero-vs-partial report rendering ───────────────────────────
        complete_cov = cov(["DevOps internship United States"], extract_ok=1)
        r = oc.render_zero_match(CONTRACT, "2026-08-19", complete_cov)
        assert "SEARCH COVERAGE: Complete" in r
        assert "No candidates survived the evidence/currentness/fit criteria." in r
        partial_cov = {"state": "Partial", "detail": "", "searches": 1,
                       "search_failures": 1, "extract_ok": 0, "extract_failures": 0,
                       "us": True, "tier2": False}
        r = oc.render_zero_match(CONTRACT, "2026-08-19", partial_cov)
        assert "SEARCH COVERAGE: Partial" in r
        assert "Some search/extraction operations failed" in r
        assert "SEARCH PRIORITY: United States → High-paying Europe / Singapore / Malaysia" in r

        # ── 5. Deterministic ranking ──────────────────────────────────────
        def k(score, tier, elig=5, role=30, comp=None):
            return oc._ranking_key({
                "score": score, "_geo_tier": tier, "_elig_score": elig,
                "_role_score": role, "_norm_comp": comp,
            })

        # Strong US beats weak Singapore.
        assert k(85, "US") < k(45, "SG")
        # Strong Singapore may outrank weak US (score is primary).
        assert k(85, "SG") < k(40, "US")
        # Two similar Tier-2: higher verified compensation ranks first.
        assert k(70, "SG", comp=53280.0) < k(70, "SG", comp=13200.0)
        # Missing compensation ranks last among ties.
        assert k(70, "SG", comp=53280.0) < k(70, "SG", comp=None)

        # ── 6. End-to-end: India candidate is dropped before scoring ──────
        india_messages = _extract_messages(
            IN_URL,
            "Mactores DevOps Engineer Intern, Mumbai, Maharashtra, India. "
            "Requirements: AWS, Kubernetes, CI/CD. Status: open.",
        )
        india_report = _report([_candidate("Mumbai, MH", url=IN_URL, company="Mactores", score=95)])
        r = oc.validate_contract(CONTRACT, json.dumps(india_report), india_messages)
        assert r.valid, r.errors  # a clean zero-match, not a fake India report
        assert r.structure["verified_matches"] == 0
        assert r.structure["candidates"] == []

        # A compliant US candidate survives.
        us_messages = _extract_messages(
            US_URL,
            "Acme DevOps Engineer Intern, New York, NY, United States. "
            "Requirements: AWS, Kubernetes, CI/CD. Status: open.",
        )
        us_report = _report([_candidate("New York, NY", url=US_URL)], best_match=None)
        r = oc.validate_contract(CONTRACT, json.dumps(us_report), us_messages)
        assert r.valid, r.errors
        assert r.structure["verified_matches"] == 1
        assert r.structure["candidates"][0]["company"] == "Acme"
        # best_match is code-selected even when the model omitted it.
        assert r.structure["best_match"]["candidate_index"] == 0

    print("PASS geography hard gate rejects India/unapproved markets")
    print("PASS US + Tier-2 (EU/SG/MY) geography accepted and normalized")
    print("PASS extracted-page location overrides the model location")
    print("PASS ambiguous remote geography is never guessed")
    print("PASS salary normalization is deterministic and never fabricated")
    print("PASS search coverage distinguishes Complete/Partial/Incomplete")
    print("PASS zero-match report distinguishes complete vs partial coverage")
    print("PASS deterministic ranking (score, geography, eligibility, role, salary)")
    print("PASS India candidate is dropped before scoring end-to-end")
    print("CRON_CAREER_GEOGRAPHY_RELIABILITY: PASS")


if __name__ == "__main__":
    main()
