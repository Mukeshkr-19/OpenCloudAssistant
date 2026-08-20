#!/usr/bin/env python3
"""Deterministic regression coverage for P16 career candidate rejection.

Materializes the fully-patched Hermes tree (baseline + P1..P16 via
``install/30-brain-materialize.sh``) and exercises the output contract's
candidate-level job-identity rejection:

  * a candidate whose apply_url was never extracted this run, whose page is a
    404/error/closed page, or whose title / company / JD the extracted page
    cannot support is REJECTED (dropped) — never a contract-wide error;
  * the remaining valid candidates still render (the mixed-report regression
    that previously discarded the whole report as a misleading zero-match);
  * structural field errors still invalidate the contract (bounded repair);
  * best_match stays code-selected from the surviving candidates.

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
U1 = "https://boards.greenhouse.io/acme/jobs/101"
U2 = "https://boards.greenhouse.io/acme/jobs/102"
U3 = "https://boards.greenhouse.io/acme/jobs/103"
U4 = "https://boards.greenhouse.io/acme/jobs/104"
U_MISSING = "https://boards.greenhouse.io/acme/jobs/999"

_PAGE = {
    U1: "Acme DevOps Engineer Intern, New York, NY, United States. "
         "Requirements: AWS, Kubernetes, CI/CD. Status: open.",
    U2: "Acme Site Reliability Engineer Intern, Austin, TX, United States. "
         "Requirements: Linux, Terraform, Observability. Status: open.",
    U3: "Acme Cloud Engineer Intern, Seattle, WA, United States. "
         "Requirements: AWS, Python, Docker. Status: open.",
    U4: "Acme Platform Engineer Intern, Boston, MA, United States. "
         "Requirements: Go, Kubernetes, CI/CD. Status: open.",
}


def materialize(out: Path) -> bool:
    result = subprocess.run(
        ["bash", str(ROOT / "install/30-brain-materialize.sh"), "--stage", str(out)],
        env={**os.environ, "OPEN_CLOUD_HERMES_ROOT": str(HERMES_ROOT)},
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        print(
            "CRON_CAREER_CANDIDATE_REJECTION: SKIP (materialization failed): "
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


def _messages(pages):
    """One web_search + one web_extract (all pages) + one Vellum result."""
    urls = list(pages.keys())
    search_json = json.dumps(
        {"data": {"web": [{"url": u, "title": "Job", "content": "search result"} for u in urls]}}
    )
    results = [
        {"url": u, "title": "Job", "content": content, "error": None}
        for u, content in pages.items()
    ]
    extract_json = json.dumps({"results": results})
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
                "function": {"name": "web_extract", "arguments": json.dumps({"urls": urls})},
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


def _cand(title, company, location, url, jd):
    return {
        "score": 80,
        "title": title,
        "company": company,
        "location": location,
        "type": "Internship",
        "posted": "2026-08-15",
        "salary": "Not stated — verify",
        "status": "Verified open",
        "apply_url": url,
        "jd": jd,
        "why_match": ["AWS certified", "Kubernetes experience"],
        "gaps": ["No major gap identified"],
        "eligibility": {
            "requirement": "CS degree in progress",
            "user_fit": "confirmed: AWS certified, Kubernetes experience",
            "sponsorship": "Not stated — verify",
        },
    }


def _report(candidates):
    return {
        "date": "2026-08-19",
        "verified_matches": len(candidates),
        "candidates": candidates,
        "best_match": None,
    }


def _valid(url, title, location, jd):
    return _cand(title, "Acme", location, url, jd)


def main() -> None:
    if not (HERMES_ROOT / ".git").is_dir():
        print("CRON_CAREER_CANDIDATE_REJECTION: SKIP (Hermes Git source unavailable)")
        return

    with tempfile.TemporaryDirectory(prefix="opencloud-cron-career-candidate-rejection-") as tmp:
        tree = Path(tmp) / "hermes"
        if not materialize(tree):
            return

        sys.path.insert(0, str(tree))
        try:
            from cron import output_contract as oc
        finally:
            sys.path.pop(0)

        valid = [
            _valid(U1, "DevOps Engineer Intern", "New York, NY", ["AWS", "Kubernetes", "CI/CD"]),
            _valid(U2, "Site Reliability Engineer Intern", "Austin, TX", ["Linux", "Terraform", "Observability"]),
            _valid(U3, "Cloud Engineer Intern", "Seattle, WA", ["AWS", "Python", "Docker"]),
        ]

        # ── 1. Mixed report: 3 valid + 1 wrong-title candidate ──────────
        # The bad candidate (title not supported by its extracted page) must
        # be DROPPED, never invalidate the whole contract (this was the
        # production regression that produced a misleading zero-match).
        bad_title = _cand("Frontend Engineer", "Acme", "Boston, MA", U4,
                          ["Go", "Kubernetes", "CI/CD"])
        r = oc.validate_contract(CONTRACT, json.dumps(_report(valid + [bad_title])), _messages(_PAGE))
        assert r.valid, r.errors
        assert r.structure["verified_matches"] == 3
        titles = [c["title"] for c in r.structure["candidates"]]
        assert "Frontend Engineer" not in titles
        assert "DevOps Engineer Intern" in titles
        assert any("title not supported" in e for e in r.structure["_rejections"]), r.structure["_rejections"]
        # best_match is code-selected from the surviving top-ranked candidate.
        assert r.structure["best_match"]["candidate_index"] == 0
        rendered = oc.render_contract(CONTRACT, r.structure)
        assert "VERIFIED MATCHES: 3" in rendered
        assert "Frontend Engineer" not in rendered

        # ── 2. All candidates rejected => clean zero-match (valid) ───────
        r = oc.validate_contract(CONTRACT, json.dumps(_report([bad_title])), _messages(_PAGE))
        assert r.valid, r.errors
        assert r.structure["verified_matches"] == 0
        assert r.structure["candidates"] == []
        assert any("title not supported" in e for e in r.structure["_rejections"]), r.structure["_rejections"]

        # ── 3. Each job-identity failure is a candidate rejection ────────
        base = _valid(U1, "DevOps Engineer Intern", "New York, NY", ["AWS", "Kubernetes", "CI/CD"])
        pages = {U1: _PAGE[U1]}

        # (a) apply_url never extracted this run.
        r = oc.validate_contract(CONTRACT, json.dumps(_report([_cand(
            "DevOps Engineer Intern", "Acme", "New York, NY", U_MISSING,
            ["AWS", "Kubernetes", "CI/CD"],
        )])), _messages(pages))
        assert r.valid, r.errors
        assert any("not web_extract'ed" in e for e in r.structure["_rejections"]), r.structure["_rejections"]

        # (b) wrong title.
        r = oc.validate_contract(CONTRACT, json.dumps(_report([_cand(
            "Data Scientist", "Acme", "New York, NY", U1, ["AWS", "Kubernetes", "CI/CD"],
        )])), _messages(pages))
        assert r.valid, r.errors
        assert any("title not supported" in e for e in r.structure["_rejections"]), r.structure["_rejections"]

        # (c) wrong company (page + host both unsupported).
        r = oc.validate_contract(CONTRACT, json.dumps(_report([_cand(
            "DevOps Engineer Intern", "WrongCo", "New York, NY", U1,
            ["AWS", "Kubernetes", "CI/CD"],
        )])), _messages(pages))
        assert r.valid, r.errors
        assert any("company not supported" in e for e in r.structure["_rejections"]), r.structure["_rejections"]

        # (d) 404 page.
        err_page = {U1: "404 page not found"}
        r = oc.validate_contract(CONTRACT, json.dumps(_report([base])), _messages(err_page))
        assert r.valid, r.errors
        assert any("not a valid job page" in e for e in r.structure["_rejections"]), r.structure["_rejections"]

        # (e) closed page.
        closed_page = {U1: "Acme DevOps Engineer Intern. This position has been filled."}
        r = oc.validate_contract(CONTRACT, json.dumps(_report([base])), _messages(closed_page))
        assert r.valid, r.errors
        assert any("closed/expired/filled" in e for e in r.structure["_rejections"]), r.structure["_rejections"]

        # (f) fewer than 3 evidence-backed JD bullets.
        r = oc.validate_contract(CONTRACT, json.dumps(_report([_cand(
            "DevOps Engineer Intern", "Acme", "New York, NY", U1,
            ["AWS", "Kubernetes", "invented bullet"],
        )])), _messages(pages))
        assert r.valid, r.errors
        assert any("evidence-backed JD bullets" in e for e in r.structure["_rejections"]), r.structure["_rejections"]

        # ── 4. Structural errors still invalidate (bounded repair) ───────
        missing_company = base.copy()
        del missing_company["company"]
        r = oc.validate_contract(CONTRACT, json.dumps(_report([missing_company])), _messages(pages))
        assert not r.valid
        assert any("company" in e for e in r.errors), r.errors

        # ── 5. Geography rejection is unchanged and still recorded ───────
        india_page = {U1: "Mactores DevOps Engineer Intern, Mumbai, India. "
                          "Requirements: AWS, Kubernetes, CI/CD. Status: open."}
        india = _cand("DevOps Engineer Intern", "Mactores", "Mumbai, MH", U1,
                      ["AWS", "Kubernetes", "CI/CD"])
        r = oc.validate_contract(CONTRACT, json.dumps(_report([india])), _messages(india_page))
        assert r.valid, r.errors
        assert r.structure["verified_matches"] == 0
        assert any("location outside allowed geography" in e or "mumbai" in e.lower()
                   for e in r.structure["_rejections"]), r.structure["_rejections"]

    print("PASS mixed report drops wrong-title candidate, keeps 3 valid (no misleading zero-match)")
    print("PASS all-rejected report is a clean zero-match")
    print("PASS apply_url-not-extracted / wrong title / wrong company / 404 / closed / jd<3 are candidate rejections")
    print("PASS structural field errors still invalidate the contract")
    print("PASS geography rejection unchanged and recorded")
    print("CRON_CAREER_CANDIDATE_REJECTION_RELIABILITY: PASS")


if __name__ == "__main__":
    main()
