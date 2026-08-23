"""Deterministic, opt-in output contracts for cron jobs.

A cron job opts in by setting ``output_schema`` to a contract name.  The
scheduler then attaches a validator to the agent and, after the run, renders
the final message from the *sanitized* structured result instead of trusting
the model's free-form prose.

Two guarantees, both enforced without any model judgment:

1. **Structure** — the JSON must contain exactly the fields the contract
   declares, with the right types, ranges, and enums.

2. **Evidence provenance** — every ``apply_url`` must correspond to a URL that
   was *successfully* passed to ``web_extract`` during THIS run (derived from
   the actual tool-result messages, never from the model's prose), and salary /
   eligibility claims must be traceable to the extracted page / Vellum output.

Deterministic *downgrades* (Verified open -> unverified, unsupported salary ->
"Not stated — verify", unsupported user fit -> "Not confirmed") are applied to
the returned ``structure`` so the renderer only ever consumes sanitized data.
Anything that cannot be downgraded is a hard error and makes the contract
invalid (after one bounded repair the scheduler renders the visible zero-match
report).

This module is pure (stdlib only) and side-effect free so it can be exercised
deterministically in the reliability suite.
"""

from __future__ import annotations

import json
import re
from urllib.parse import urlsplit, urlunsplit

# HERMES_CRON_OUTPUT_CONTRACT_V1


# ─────────────────────────────────────────────────────────────────────────
# Contract registry
# ─────────────────────────────────────────────────────────────────────────

_STATUS_VERIFIED_OPEN = "Verified open"
_STATUS_UNVERIFIED = "Current status not explicitly stated — verify before applying"

_ALLOWED_STATUSES = frozenset({_STATUS_VERIFIED_OPEN, _STATUS_UNVERIFIED})
_ALLOWED_TYPES = frozenset(
    {"Internship", "Co-op", "New Grad", "Entry Level", "Other"}
)

_SENTINEL_SALARY = "Not stated — verify"
_SENTINEL_USER_FIT = "User eligibility against this requirement: Not confirmed"

# Conservative allowlist of employer/ATS hosts.  A job page on one of these
# hosts may be labelled "Verified open" (still gated on the extracted page not
# carrying closed/expired/filled language).  Anything else — including
# third-party aggregators — is downgraded to the "verify" status.
_ATS_HOST_SUFFIXES = (
    "greenhouse.io",
    "lever.co",
    "smartrecruiters.com",
    "icims.com",
    "myworkdayjobs.com",
    "workday.com",
    "ashbyhq.com",
    "personio.com",
    "workable.com",
    "breezy.hr",
)

_CAREER_JOB_MATCH_V1 = {
    "extract_tool": "web_extract",
    "search_tool": "web_search",
    "user_context_tool": "mcp__vellum_bridge__get_user_context",
}

CONTRACTS = {
    "career_job_match_v1": _CAREER_JOB_MATCH_V1,
}


class ContractResult:
    """Result of validating a final response against an output contract.

    ``structure`` is the *sanitized* structure (downgrades already applied)
    whenever the JSON parsed and had a dict shape, even when ``valid`` is
    False.  It is ``None`` only when the text was not a parseable JSON object.
    """

    __slots__ = ("valid", "errors", "structure")

    def __init__(self, valid, errors, structure):
        self.valid = bool(valid)
        self.errors = list(errors or [])
        self.structure = structure

    def __repr__(self):  # pragma: no cover - debugging aid
        return (
            f"ContractResult(valid={self.valid!r}, "
            f"errors={self.errors!r}, structure={self.structure!r})"
        )


# ─────────────────────────────────────────────────────────────────────────
# URL canonicalization
# ─────────────────────────────────────────────────────────────────────────


def canonicalize_url(url):
    """Deterministically normalize a URL for evidence comparison.

    - lowercase scheme + host
    - drop the fragment
    - normalize default ports (http:80 / https:443)
    - normalize the trailing slash (strip a single trailing slash, keep root)

    Query parameters are preserved verbatim: they may carry meaningful job IDs
    or routing parameters, so they are never silently discarded.
    """
    if not isinstance(url, str):
        return ""
    url = url.strip()
    if not url:
        return ""
    parts = urlsplit(url)
    scheme = (parts.scheme or "").lower()
    netloc = (parts.netloc or "").lower()
    if scheme == "http" and netloc.endswith(":80"):
        netloc = netloc[: -len(":80")]
    elif scheme == "https" and netloc.endswith(":443"):
        netloc = netloc[: -len(":443")]
    path = parts.path or ""
    if path.endswith("/") and path != "/":
        path = path.rstrip("/")
    return urlunsplit((scheme, netloc, path, parts.query, ""))


# ─────────────────────────────────────────────────────────────────────────
# Evidence ledger
# ─────────────────────────────────────────────────────────────────────────

_UNTRUSTED_START = "<untrusted_tool_result"
_UNTRUSTED_END = "</untrusted_tool_result>"


def _strip_untrusted_wrapper(content):
    """Remove Hermes' ``<untrusted_tool_result>`` delimiters from tool output."""
    if not isinstance(content, str):
        return "" if content is None else content
    s = content.strip()
    if s.startswith(_UNTRUSTED_START):
        close = s.rfind(_UNTRUSTED_END)
        if close != -1:
            open_end = s.find(">")
            if open_end != -1:
                s = s[open_end + 1 : close]
    return s.strip()


def _loads(content):
    """JSON-decode a tool result string, tolerating the untrusted wrapper.

    Hermes wraps untrusted tool output in ``<untrusted_tool_result>`` tags
    *preceded by a prose preamble* inside the block.  After stripping the tags,
    the actual JSON is located at the first ``{`` and decoded via
    ``JSONDecoder().raw_decode()`` (fail closed on malformed JSON).
    """
    if isinstance(content, str):
        content = _strip_untrusted_wrapper(content)
        start = content.find("{")
        if start == -1:
            return None
        try:
            return json.JSONDecoder().raw_decode(content, start)[0]
        except json.JSONDecodeError:
            return None
    return content if isinstance(content, (dict, list)) else None


def build_evidence_ledger(messages, contract=None):
    """Derive run-local evidence from the actual tool executions in ``messages``.

    Returns::

        {
            "extracted": {canonical_url: extracted_text},   # successful web_extract
            "extract_calls": {canonical_url, ...},          # URLs passed to web_extract
            "search_results": {canonical_url, ...},         # URLs surfaced by web_search
            "vellum_text": str,                             # confirmed Vellum facts
        }

    Only tool-result messages (``role == "tool"``) count as execution evidence;
    the model's prose is never consulted.  The ledger is rebuilt from the
    messages passed in, so evidence from a previous cron run can never satisfy
    this run.
    """
    contract = contract or {}
    extract_tool = contract.get("extract_tool", "web_extract")
    search_tool = contract.get("search_tool", "web_search")
    user_context_tool = contract.get("user_context_tool")

    extracted = {}
    extract_calls = set()
    search_results = set()
    vellum_parts = []
    search_queries = []
    search_failures = 0
    extract_failures = 0
    search_statuses = []
    search_providers = []

    for msg in messages or []:
        if not isinstance(msg, dict):
            continue
        role = msg.get("role")
        if role == "assistant":
            for tc in msg.get("tool_calls") or []:
                fn = None
                if isinstance(tc, dict):
                    fn = tc.get("function") or {}
                else:
                    fn = getattr(tc, "function", None) or {}
                if not isinstance(fn, dict):
                    continue
                name = str(fn.get("name") or "").strip()
                if name not in (extract_tool, search_tool):
                    continue
                raw_args = fn.get("arguments")
                try:
                    args = json.loads(raw_args) if isinstance(raw_args, str) else (raw_args or {})
                except (ValueError, TypeError):
                    args = {}
                if not isinstance(args, dict):
                    args = {}
                if name == extract_tool:
                    for u in args.get("urls") or []:
                        if isinstance(u, str) and u.strip():
                            extract_calls.add(canonicalize_url(u))
                else:
                    q = args.get("query")
                    if isinstance(q, str) and q.strip():
                        search_queries.append(q.strip())
        elif role == "tool":
            name = str(msg.get("name") or msg.get("tool_name") or "").strip()
            if name == extract_tool:
                data = _loads(msg.get("content"))
                if isinstance(data, dict):
                    if data.get("error"):
                        extract_failures += 1
                    for r in data.get("results") or []:
                        if not isinstance(r, dict):
                            continue
                        url = r.get("url")
                        err = r.get("error")
                        text_content = r.get("content") or ""
                        if url and err:
                            extract_failures += 1
                        elif url and not err and str(text_content).strip():
                            extracted[canonicalize_url(str(url))] = str(text_content)
            elif name == search_tool:
                data = _loads(msg.get("content"))
                if isinstance(data, dict):
                    status = str(data.get("search_status") or "").strip()
                    if status:
                        search_statuses.append(status)
                    provider = str(data.get("search_provider") or "").strip()
                    if provider:
                        search_providers.append(provider)
                    if (
                        data.get("error")
                        or data.get("success") is False
                    ) and status not in {
                        "SEARCH_SUCCESS_WITH_RESULTS", "SEARCH_SUCCESS_EMPTY"
                    }:
                        search_failures += 1
                    web = None
                    if isinstance(data.get("data"), dict):
                        web = data["data"].get("web") or []
                    for r in web or []:
                        if isinstance(r, dict) and r.get("url"):
                            search_results.add(canonicalize_url(str(r["url"])))
            elif user_context_tool and name == user_context_tool:
                vellum_parts.append(_strip_untrusted_wrapper(msg.get("content")))

    return {
        "extracted": extracted,
        "extract_calls": extract_calls,
        "search_results": search_results,
        "vellum_text": "\n".join(p for p in vellum_parts if p),
        "search_queries": search_queries,
        "search_failures": search_failures,
        "extract_failures": extract_failures,
        "search_statuses": search_statuses,
        "search_providers": search_providers,
    }


# ─────────────────────────────────────────────────────────────────────────
# Provenance helpers
# ─────────────────────────────────────────────────────────────────────────

_CLOSED_INDICATOR_RE = re.compile(
    r"\b(?:closed|expired|filled|no longer accepting applications"
    r"|position has been filled|posting is no longer)\b",
    re.IGNORECASE,
)
_SALARY_FABRICATION_RE = re.compile(
    r"estimated|estimate|approx|derived|aggregator|glassdoor|~|\u2248",
    re.IGNORECASE,
)
_ASSUMED_RE = re.compile(r"\bassumed\b", re.IGNORECASE)
_SILENT_RE = re.compile(r"\[silent\]", re.IGNORECASE)
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

# ─────────────────────────────────────────────────────────────────────────
# Deterministic scoring + factual provenance
# ─────────────────────────────────────────────────────────────────────────

_STOPWORDS = frozenset({
    "a", "an", "the", "and", "or", "of", "in", "on", "at", "to", "for",
    "with", "is", "are", "be", "as", "by", "this", "that", "it", "you",
    "your", "we", "our", "role", "job", "engineer", "intern", "senior",
    "junior", "position", "candidate", "applicant",
})

_ROLE_FAMILY_KEYWORDS = (
    "devops", "site reliability", "sre", "cloud engineer", "cloud engineering",
    "platform engineer", "platform engineering", "infrastructure",
    "production engineer", "systems engineer", "build engineer",
    "release engineer", "ci/cd", "ci cd", "cicd", "observability",
    "reliability engineer", "developer productivity",
    "infrastructure engineer", "infrastructure automation",
)

_SKILL_VOCABULARY = (
    "aws", "amazon web services", "azure", "gcp", "google cloud",
    "kubernetes", "k8s", "terraform", "docker", "container", "python",
    "golang", "linux", "bash", "shell", "jenkins", "github actions",
    "gitlab", "ci/cd", "ci cd", "ansible", "prometheus", "grafana",
    "datadog", "monitoring", "observability", "networking", "tcp/ip",
    "dns", "load balancer", "nginx", "database", "postgres", "mysql",
    "redis", "security", "iac",
)

_LEVEL_KEYWORDS = (
    "intern", "internship", "co-op", "coop", "new grad", "new graduate",
    "entry level", "entry-level", "junior", "student", "graduate",
)

_PAGE_ERROR_INDICATORS = (
    "404", "page not found", "not found", "error 404",
    "website not supported", "failed to scrape", "access denied", "forbidden",
)

_RAW_REMOTE = frozenset({"remote", "remote — verify", "remote - verify"})

# ─────────────────────────────────────────────────────────────────────────
# Geography policy + compensation normalization
# ─────────────────────────────────────────────────────────────────────────
# HERMES_CRON_GEOGRAPHY_POLICY_V1
# The user's target geography is a HARD validation gate, not a scoring hint.
# A candidate outside the allowed markets is dropped BEFORE scoring.
#
#   Tier 1 (primary):  United States
#   Tier 2 (fill-in):  Europe / EU, Singapore, Malaysia
#   Default reject:    India, China, Australia, Canada, Middle East,
#                      Latin America, and other unrelated markets.
#
# Remote roles are accepted only when the exact extracted posting supports
# employment from one of the allowed markets; ambiguous remote geography is
# never guessed.

_GEO_US = "US"
_GEO_EU = "EU"
_GEO_SG = "SG"
_GEO_MY = "MY"
_GEO_REJECT = "REJECT"
_GEO_UNKNOWN = "UNKNOWN"

# US identification.  A bare ``us`` token is only trusted in short location
# strings (never in long page text, where it is an ordinary word).
_US_COUNTRY_MARKERS = (
    "united states", "united states of america", "usa", "u.s.a", "u.s.",
)

_US_STATE_NAMES = (
    "alabama", "alaska", "arizona", "arkansas", "california", "colorado",
    "connecticut", "delaware", "florida", "georgia", "hawaii", "idaho",
    "illinois", "indiana", "iowa", "kansas", "kentucky", "louisiana",
    "maine", "maryland", "massachusetts", "michigan", "minnesota",
    "mississippi", "missouri", "montana", "nebraska", "nevada",
    "new hampshire", "new jersey", "new mexico", "new york",
    "north carolina", "north dakota", "ohio", "oklahoma", "oregon",
    "pennsylvania", "rhode island", "south carolina", "south dakota",
    "tennessee", "texas", "utah", "vermont", "virginia", "washington",
    "west virginia", "wisconsin", "wyoming", "district of columbia",
)

_US_STATE_CODES = frozenset({
    "al", "ak", "az", "ar", "ca", "co", "ct", "de", "fl", "ga", "hi", "id",
    "il", "in", "ia", "ks", "ky", "la", "me", "md", "ma", "mi", "mn", "ms",
    "mo", "mt", "ne", "nv", "nh", "nj", "nm", "ny", "nc", "nd", "oh", "ok",
    "or", "pa", "ri", "sc", "sd", "tn", "tx", "ut", "vt", "va", "wa", "wv",
    "wi", "wy", "dc",
})

# Tier-2 Europe: EU member states, the UK, and other European countries.
_EUROPE_MARKERS = (
    "germany", "france", "netherlands", "ireland", "spain", "italy", "poland",
    "sweden", "denmark", "finland", "norway", "belgium", "austria",
    "switzerland", "portugal", "czech", "czechia", "romania", "hungary",
    "estonia", "latvia", "lithuania", "luxembourg", "slovakia", "slovenia",
    "croatia", "bulgaria", "greece", "iceland", "malta", "cyprus",
    "united kingdom", "england", "scotland", "wales", "europe",
)

# Markets that are default-rejected unless the user widens the policy later.
_REJECT_COUNTRY_MARKERS = (
    "india", "china", "australia", "canada", "japan", "south korea", "korea",
    "brazil", "mexico", "israel", "uae", "united arab emirates",
    "saudi arabia", "qatar", "pakistan", "bangladesh", "sri lanka", "nepal",
    "vietnam", "thailand", "indonesia", "philippines", "argentina", "chile",
    "colombia", "turkey", "russia", "ukraine", "south africa", "nigeria",
    "kenya", "new zealand", "hong kong", "taiwan",
)

_REJECT_CITY_MARKERS = (
    "mumbai", "bangalore", "bengaluru", "hyderabad", "delhi", "new delhi",
    "pune", "chennai", "kolkata", "gurgaon", "gurugram", "noida",
    "ahmedabad", "indore", "jaipur", "lucknow", "kochi", "coimbatore",
    "beijing", "shanghai", "shenzhen", "sydney", "melbourne", "toronto",
    "vancouver", "montreal", "dubai",
)

# Fixed reference FX snapshot (USD per 1 unit of foreign currency), used ONLY
# for internal relative ranking of compensation.  It is a documented,
# deterministic snapshot — not a live feed — and is never exposed as employer
# compensation.  When a currency or amount cannot be parsed, normalization
# returns ``None`` and salary ranking degrades gracefully (no fabricated
# conversions).
_FX_REFERENCE_USD = {
    "USD": 1.0,
    "EUR": 1.08,
    "GBP": 1.27,
    "SGD": 0.74,
    "MYR": 0.22,
}

_ANNUAL_HOURS = 2080

_SEARCH_PRIORITY_LINE = "United States → High-paying Europe / Singapore / Malaysia"

_COVERAGE_COMPLETE = "Complete"
_COVERAGE_PARTIAL = "Partial"
_COVERAGE_INCOMPLETE = "Incomplete"

_TIER_RANK = {_GEO_US: 0, _GEO_EU: 1, _GEO_SG: 1, _GEO_MY: 1}


def _significant_tokens(text):
    """Lowercased alphanumeric tokens with stopwords removed."""
    if not isinstance(text, str):
        return []
    return [
        t for t in re.findall(r"[a-z0-9]+", text.lower())
        if t not in _STOPWORDS and len(t) > 1
    ]


def _token_in_text(token, text):
    """Word-boundary match for single tokens; substring for phrases."""
    token = str(token or "").strip().lower()
    if not token:
        return False
    text = _normalize_text(text)
    if any(ch in token for ch in (" ", "/", "-", ".")):
        return token in text
    return re.search(rf"\b{re.escape(token)}\b", text) is not None


def _mentions(tokens, text, threshold=0.6):
    """True when at least ``threshold`` of ``tokens`` appear in ``text``."""
    tokens = [t for t in tokens if t]
    if not tokens:
        return False
    hits = sum(1 for t in tokens if _token_in_text(t, text))
    return hits / len(tokens) >= threshold


def _has_page_error(content):
    norm = _normalize_text(content)
    return any(ind in norm for ind in _PAGE_ERROR_INDICATORS)


def _role_family_match(text):
    norm = _normalize_text(text)
    return any(kw in norm for kw in _ROLE_FAMILY_KEYWORDS)


# ─────────────────────────────────────────────────────────────────────────
# Geography classification
# ─────────────────────────────────────────────────────────────────────────


def _has_bare_token(s, token):
    return token in set(re.findall(r"[a-z0-9]+", _normalize_text(s)))


def _has_us_state_code(s):
    # Only whole two-letter tokens count as state codes, so words like
    # "singapore" (contains "ga") or "germany" (contains "in") never match.
    words = set(re.findall(r"\b[a-z]{2}\b", _normalize_text(s)))
    return bool(words & _US_STATE_CODES)


def _classify_location(text):
    """Classify a model-provided location string into a geography code.

    Returns ``(code, reason)`` where ``code`` is a ``_GEO_*`` constant.
    Reject markers take priority over allowed markers, so a string carrying
    both (e.g. "Mumbai, US") is still rejected.
    """
    s = _normalize_text(text)
    if not s or s in ("not stated", "not stated — verify"):
        return _GEO_UNKNOWN, ""
    if s in _RAW_REMOTE:
        return _GEO_UNKNOWN, ""
    # Strip a leading "remote" qualifier: "Remote - United States" -> US.
    stripped = re.sub(r"^remote[\s\-—:]+?", "", s)
    if stripped and stripped != s:
        s = stripped

    # Reject markers (cities first, then countries).
    for m in _REJECT_CITY_MARKERS:
        if _token_in_text(m, s):
            return _GEO_REJECT, f"location indicates {m}"
    for m in _REJECT_COUNTRY_MARKERS:
        if _token_in_text(m, s):
            return _GEO_REJECT, f"location indicates {m}"

    # United States.
    if any(_token_in_text(m, s) for m in _US_COUNTRY_MARKERS):
        return _GEO_US, ""
    if any(_token_in_text(st, s) for st in _US_STATE_NAMES):
        return _GEO_US, ""
    if _has_us_state_code(s):
        return _GEO_US, ""
    if _has_bare_token(s, "us"):
        return _GEO_US, ""

    # Tier-2: Singapore / Malaysia.
    if _token_in_text("singapore", s):
        return _GEO_SG, ""
    if _token_in_text("malaysia", s):
        return _GEO_MY, ""

    # Tier-2: Europe / EU.
    for m in _EUROPE_MARKERS:
        if _token_in_text(m, s):
            return _GEO_EU, ""
    if _has_bare_token(s, "eu"):
        return _GEO_EU, ""

    return _GEO_UNKNOWN, ""


def _classify_page_geography(text):
    """Classify extracted job-page text for geography evidence.

    Long text, so bare ``us``/``eu`` and bare two-letter codes are NOT trusted
    here.  Only strong, explicit signals count: reject city/country names and
    full country/state names.  A strong reject city marker always wins.
    """
    s = _normalize_text(text)
    if not s:
        return _GEO_UNKNOWN, ""

    # Strong city markers always reject (a job page naming Mumbai/Bangalore/
    # etc. as its location is a rejected market).
    for m in _REJECT_CITY_MARKERS:
        if _token_in_text(m, s):
            return _GEO_REJECT, f"page indicates {m}"

    has_reject_country = any(_token_in_text(m, s) for m in _REJECT_COUNTRY_MARKERS)
    has_allowed = (
        any(_token_in_text(m, s) for m in _US_COUNTRY_MARKERS)
        or any(_token_in_text(st, s) for st in _US_STATE_NAMES)
        or _token_in_text("singapore", s)
        or _token_in_text("malaysia", s)
        or any(_token_in_text(m, s) for m in _EUROPE_MARKERS)
    )
    # A rejected country with no allowed market co-mentioned is a reject;
    # a co-mentioned allowed market (global office list) defers to the model.
    if has_reject_country and not has_allowed:
        return _GEO_REJECT, "page indicates a rejected market"

    if any(_token_in_text(m, s) for m in _US_COUNTRY_MARKERS):
        return _GEO_US, ""
    if any(_token_in_text(st, s) for st in _US_STATE_NAMES):
        return _GEO_US, ""
    if _token_in_text("singapore", s):
        return _GEO_SG, ""
    if _token_in_text("malaysia", s):
        return _GEO_MY, ""
    for m in _EUROPE_MARKERS:
        if _token_in_text(m, s):
            return _GEO_EU, ""
    return _GEO_UNKNOWN, ""


def _resolve_geography(location, page_text):
    """Return ``(allowed, tier, reason)`` for a candidate's geography.

    Rejected markets are dropped BEFORE scoring.  Remote/ambiguous geography
    without extracted-page support is never guessed.
    """
    loc_code, loc_reason = _classify_location(location)
    page_code, page_reason = _classify_page_geography(page_text)

    if page_code == _GEO_REJECT:
        return False, None, page_reason
    if loc_code == _GEO_REJECT:
        return False, None, loc_reason

    if loc_code in (_GEO_US, _GEO_EU, _GEO_SG, _GEO_MY):
        return True, loc_code, ""
    if page_code in (_GEO_US, _GEO_EU, _GEO_SG, _GEO_MY):
        return True, page_code, ""
    return False, None, "ambiguous location not supported by extracted page"


# ─────────────────────────────────────────────────────────────────────────
# Search coverage + compensation normalization
# ─────────────────────────────────────────────────────────────────────────


def _query_targets_us(q):
    q = _normalize_text(q)
    return (
        any(_token_in_text(m, q) for m in _US_COUNTRY_MARKERS)
        or any(_token_in_text(st, q) for st in _US_STATE_NAMES)
        or _has_us_state_code(q)
    )


def _query_targets_tier2(q):
    q = _normalize_text(q)
    return (
        _token_in_text("singapore", q)
        or _token_in_text("malaysia", q)
        or any(_token_in_text(m, q) for m in _EUROPE_MARKERS)
    )


def _compute_search_coverage(ledger):
    """Deterministically classify this run's retrieval coverage.

    Distinguishes a true zero-match (searched and extracted, nothing
    compliant survived) from a degraded/partial search (provider or tool
    failures, or nothing extracted).
    """
    searches = ledger.get("search_queries") or []
    statuses = list(ledger.get("search_statuses") or [])
    search_failures = sum(
        1 for status in statuses
        if status not in {"SEARCH_SUCCESS_WITH_RESULTS", "SEARCH_SUCCESS_EMPTY"}
    )
    search_failures = max(search_failures, int(ledger.get("search_failures") or 0))
    extract_ok = len(ledger.get("extracted") or {})
    extract_failures = int(ledger.get("extract_failures") or 0)

    if not searches and extract_ok == 0:
        state = _COVERAGE_INCOMPLETE
    elif search_failures > 0 or extract_failures > 0:
        state = _COVERAGE_PARTIAL
    elif extract_ok == 0:
        state = _COVERAGE_PARTIAL
    else:
        state = _COVERAGE_COMPLETE

    us = any(_query_targets_us(q) for q in searches)
    tier2 = any(_query_targets_tier2(q) for q in searches)

    detail = ""
    if state == _COVERAGE_COMPLETE:
        parts = []
        if us:
            parts.append("US search completed")
        if tier2:
            parts.append("Tier-2 international search completed")
        detail = " + ".join(parts) or (
            f"{len(searches)} searches completed" if searches else ""
        )

    return {
        "state": state,
        "detail": detail,
        "searches": len(searches),
        "search_failures": search_failures,
        "extract_ok": extract_ok,
        "extract_failures": extract_failures,
        "us": us,
        "tier2": tier2,
    }


# ─────────────────────────────────────────────────────────────────────────
# Career search-wave orchestration (HERMES_CRON_SEARCH_WAVES_V1)
# ─────────────────────────────────────────────────────────────────────────
_CAREER_SEARCH_TARGET = 5
_CAREER_WAVE2 = "wave2"
_CAREER_WAVE3 = "wave3"


def _count_valid_candidates(structure):
    """Deterministic surviving-candidate count (post geography/evidence gate)."""
    if not isinstance(structure, dict):
        return 0
    candidates = structure.get("candidates") or []
    return sum(1 for c in candidates if isinstance(c, dict))


def _required_next_wave(structure, wave_state):
    """Return the next required wave id, or ``None`` when finalization may
    proceed.  The model can never stop early: while fewer than TARGET valid
    candidates survive, Wave 2 then Wave 3 are driven in order (bounded)."""
    if _count_valid_candidates(structure) >= _CAREER_SEARCH_TARGET:
        return None
    ws = wave_state if isinstance(wave_state, dict) else {}
    if not ws.get("wave2_issued"):
        return _CAREER_WAVE2
    if not ws.get("wave3_issued"):
        return _CAREER_WAVE3
    return None


def _search_waves_summary(ledger, wave_state):
    """Deterministic wave-execution summary for the report header."""
    searches = ledger.get("search_queries") or []
    ws = wave_state if isinstance(wave_state, dict) else {}
    return {
        "us_core": bool(any(_query_targets_us(q) for q in searches)),
        "us_expansion": bool(ws.get("wave2_issued")),
        "international": bool(ws.get("wave3_issued")),
    }


def _detect_currency(s):
    if "us$" in s or "usd" in s:
        return "USD"
    if "s$" in s or "sgd" in s:
        return "SGD"
    if "€" in s or "eur" in s:
        return "EUR"
    if "£" in s or "gbp" in s:
        return "GBP"
    if "myr" in s or "rm" in s:
        return "MYR"
    if "$" in s:
        return "USD"
    return None


def _normalize_compensation(salary):
    """Annualized USD for INTERNAL ranking only (never shown to the user).

    Returns a float, or ``None`` when the salary cannot be parsed or the
    currency is not in the reference snapshot.  Ranking degrades gracefully
    on ``None`` (no fabricated conversions).
    """
    if not isinstance(salary, str):
        return None
    if _normalize_text(salary) == _normalize_text(_SENTINEL_SALARY):
        return None
    if _SALARY_FABRICATION_RE.search(salary):
        return None
    s = salary.lower().strip()
    currency = _detect_currency(s)
    if currency is None:
        return None
    nums = re.findall(r"\d[\d,\.]*", s)
    if not nums:
        return None
    amount = float(nums[0].replace(",", ""))
    if amount <= 0:
        return None
    period = 1  # annual by default
    if any(w in s for w in ("hour", "/hr", "per hour")):
        period = _ANNUAL_HOURS
    elif any(w in s for w in ("week", "/wk", "per week")):
        period = 52
    elif any(w in s for w in ("month", "/mo", "per month")):
        period = 12
    fx = _FX_REFERENCE_USD.get(currency)
    if fx is None:
        return None
    return round(amount * period * fx, 2)


def _ranking_key(c):
    """Sort key: lower is better.

    Primary: deterministic score (desc).  Tie-breaks: geography priority
    (US before Tier-2), eligibility certainty, role alignment, then verified
    compensation (highest first; missing compensation ranks last).
    """
    comp = c.get("_norm_comp")
    return (
        -(int(c.get("score") or 0)),
        _TIER_RANK.get(c.get("_geo_tier"), 2),
        -(int(c.get("_elig_score") or 0)),
        -(int(c.get("_role_score") or 0)),
        -(comp) if isinstance(comp, (int, float)) else float("inf"),
    )


def _contains_assumed_outside_user_fit(cand):
    """True when the model wrote "assumed" anywhere except ``user_fit``.

    ``user_fit`` "assumed" is soft-downgraded by ``_sanitize_user_fit``; in any
    other field it is a hard error even if a later provenance downgrade would
    otherwise remove the text.
    """
    if not isinstance(cand, dict):
        return False
    probe = dict(cand)
    elig = probe.get("eligibility")
    if isinstance(elig, dict):
        probe["eligibility"] = {k: v for k, v in elig.items() if k != "user_fit"}
    return _ASSUMED_RE.search(json.dumps(probe, ensure_ascii=False)) is not None


def _score_components(c, page_text, vellum_text):
    """Compute the deterministic score components from verified evidence.

    # HERMES_CRON_DETERMINISTIC_SCORING_V1
    The model's ``score`` is never trusted; the code owns the number.  Six
    components, summing to 100:

        role alignment         30
        skills                 25
        experience level       15
        project relevance      10
        location               10
        eligibility / auth     10

    Every component is derived from the sanitized candidate fields, the
    extracted exact job page, and this run's confirmed Vellum facts.
    """
    title = c.get("title", "")
    ctype = c.get("type", "")
    location = c.get("location", "")
    elig = c.get("eligibility") or {}
    user_fit = elig.get("user_fit", "")
    requirement = elig.get("requirement", "")
    sponsorship = elig.get("sponsorship", "")

    page = _normalize_text(page_text)
    vellum = _normalize_text(vellum_text)

    # 1. Role alignment (30): title and page both reference a target family.
    title_role = _role_family_match(title)
    page_role = _role_family_match(page_text)
    if title_role and page_role:
        role = 30
    elif title_role or page_role:
        role = 15
    else:
        role = 0

    # 2. Skills (25): confirmed Vellum skills present on the job page.
    vellum_skills = [s for s in _SKILL_VOCABULARY if _token_in_text(s, vellum_text)]
    matched = sum(1 for s in vellum_skills if _token_in_text(s, page_text))
    skills = min(25, matched * 5)

    # 3. Experience level (15): junior posting type corroborated by level language.
    junior_types = {"Internship", "Co-op", "New Grad", "Entry Level"}
    level_in_page = any(_token_in_text(w, page_text) for w in _LEVEL_KEYWORDS)
    level_in_vellum = any(_token_in_text(w, vellum_text) for w in _LEVEL_KEYWORDS)
    if ctype in junior_types and level_in_page:
        exp = 15
    elif ctype in junior_types or (level_in_page and level_in_vellum):
        exp = 10
    elif level_in_page:
        exp = 5
    else:
        exp = 0

    # 4. Project relevance (10): confirmed Vellum fact tokens (beyond the
    #    skill vocabulary) appearing on the job page.
    vellum_tokens = [
        t for t in _significant_tokens(vellum_text) if t not in _SKILL_VOCABULARY
    ]
    project = 10 if any(_token_in_text(t, page_text) for t in vellum_tokens) else 0

    # 5. Location (10): geography is a HARD gate upstream (rejected candidates
    #    never reach scoring).  Surviving candidates score US=10, Tier-2=6,
    #    and unknown/not-stated=0.
    geo_tier = c.get("_geo_tier")
    if geo_tier is None:
        geo_tier = _resolve_geography(location, page_text)[1]
    if geo_tier == _GEO_US:
        loc_score = 10
    elif geo_tier in (_GEO_EU, _GEO_SG, _GEO_MY):
        loc_score = 6
    else:
        loc_score = 0

    # 6. Eligibility / work authorization (10): a confirmed user fit is worth
    #    5; a page-traceable requirement +3; page-stated sponsorship +2.  A
    #    "Not confirmed" user fit is a hard 0 (never a perfect component).
    user_fit_confirmed = bool(
        user_fit
        and _normalize_text(user_fit) != _normalize_text(_SENTINEL_USER_FIT)
    )
    if not user_fit_confirmed:
        elig_score = 0
    else:
        elig_score = 5
        if requirement and _normalize_text(requirement) not in (
            "", "not stated", "not stated — verify",
        ) and _mentions(_significant_tokens(requirement), page_text, threshold=0.5):
            elig_score += 3
        if sponsorship and _normalize_text(sponsorship) not in (
            "", "not stated", "not stated — verify",
        ) and _mentions(_significant_tokens(sponsorship), page_text, threshold=0.5):
            elig_score += 2

    total = max(0, min(100, role + skills + exp + project + loc_score + elig_score))
    return {
        "total": total,
        "role": role,
        "skills": skills,
        "exp": exp,
        "project": project,
        "location": loc_score,
        "eligibility": elig_score,
        "geo_tier": geo_tier,
    }


def _deterministic_score(c, page_text, vellum_text):
    """Return just the deterministic total (kept for callers/tests)."""
    return _score_components(c, page_text, vellum_text)["total"]


def _normalize_text(text):
    return re.sub(r"\s+", " ", str(text or "")).strip().lower()


def _is_official_ats(url):
    canonical = canonicalize_url(url)
    host = (urlsplit(canonical).netloc or "").lower()
    if not host:
        return False
    for suffix in _ATS_HOST_SUFFIXES:
        if host == suffix or host.endswith("." + suffix):
            return True
    return False


def _has_closed_indicator(content):
    return bool(_CLOSED_INDICATOR_RE.search(content or ""))


def _sanitize_salary(salary):
    """Return a salary value with deterministic provenance.

    Never rejects: an unsupported salary is downgraded to the "Not stated —
    verify" sentinel rather than discarding the candidate.
    """
    if not isinstance(salary, str):
        return _SENTINEL_SALARY
    salary = salary.strip()
    if not salary:
        return _SENTINEL_SALARY
    if _normalize_text(salary) == _normalize_text(_SENTINEL_SALARY):
        return _SENTINEL_SALARY
    if _SALARY_FABRICATION_RE.search(salary):
        return _SENTINEL_SALARY
    return salary


def _salary_supported(salary, extracted_text):
    """True when a concrete salary's digits appear in the extracted page text."""
    if not isinstance(salary, str):
        return False
    if _normalize_text(salary) == _normalize_text(_SENTINEL_SALARY):
        return True
    if _SALARY_FABRICATION_RE.search(salary):
        return False
    salary_digits = "".join(ch for ch in salary if ch.isdigit())
    if not salary_digits:
        return False
    content_digits = "".join(ch for ch in (extracted_text or "") if ch.isdigit())
    return salary_digits in content_digits


def _sanitize_user_fit(user_fit, vellum_text):
    """Return a user-fit value grounded in the current run's Vellum evidence."""
    if not isinstance(user_fit, str):
        return _SENTINEL_USER_FIT
    user_fit = user_fit.strip()
    if not user_fit:
        return _SENTINEL_USER_FIT
    if _normalize_text(user_fit) == _normalize_text(_SENTINEL_USER_FIT):
        return _SENTINEL_USER_FIT
    if _ASSUMED_RE.search(user_fit):
        return _SENTINEL_USER_FIT
    if _normalize_text(user_fit) not in _normalize_text(vellum_text):
        return _SENTINEL_USER_FIT
    return user_fit


# ─────────────────────────────────────────────────────────────────────────
# Validation
# ─────────────────────────────────────────────────────────────────────────


def _parse_json(text):
    """Parse a JSON object from the model response, failing closed.

    Strips at most one markdown JSON fence, then locates the first ``{`` and
    delegates to ``json.JSONDecoder().raw_decode()``.  Malformed JSON is an
    error, never a best-effort partial parse.
    """
    if not isinstance(text, str):
        return None, "output: not valid JSON"
    s = text.strip()
    if not s:
        return None, "output: empty"
    fence = re.match(r"^```(?:json)?\s*\n", s)
    if fence:
        s = s[fence.end():]
        if s.rstrip().endswith("```"):
            s = s.rstrip()[: -3].rstrip()
    start = s.find("{")
    if start == -1:
        return None, "output: no JSON object found"
    decoder = json.JSONDecoder()
    try:
        obj, _end = decoder.raw_decode(s, start)
    except json.JSONDecodeError as exc:
        return None, f"output: malformed JSON: {exc}"
    if not isinstance(obj, dict):
        return None, "output: top-level JSON must be an object"
    return obj, None


def _sanitize_best_match(raw_bm, out, errors):
    if raw_bm is None:
        errors.append("best_match: required when verified_matches > 0")
        out["best_match"] = None
        return
    if not isinstance(raw_bm, dict):
        errors.append("best_match: must be an object")
        out["best_match"] = None
        return
    idx = raw_bm.get("candidate_index")
    if isinstance(idx, bool) or not isinstance(idx, int) or not (
        0 <= idx < len(out["candidates"])
    ):
        errors.append(
            f"best_match.candidate_index: must reference an existing candidate, got {idx!r}"
        )
        idx = None
    score = raw_bm.get("score")
    if isinstance(score, bool) or not isinstance(score, int):
        errors.append(f"best_match.score: must be an integer, got {score!r}")
        score = None
    why = raw_bm.get("why", "")
    if not isinstance(why, str) or not why.strip():
        errors.append("best_match.why: missing")
        why = ""
    # The best-match score is owned by the deterministic scorer: it must equal
    # the referenced candidate's computed score (the model's value is ignored).
    if idx is not None and 0 <= idx < len(out["candidates"]):
        cand_score = out["candidates"][idx].get("score")
        if isinstance(cand_score, int):
            score = cand_score
    out["best_match"] = {"candidate_index": idx, "score": score, "why": why}


def _sanitize_candidate(cand, i, extracted, vellum_text, errors):
    c = {}

    # Hard content bans on the RAW model output (before any downgrade):
    # "assumed" personal facts and [SILENT] are never acceptable.  "assumed"
    # in user_fit is soft-downgraded by _sanitize_user_fit instead.
    if _contains_assumed_outside_user_fit(cand):
        errors.append(f"candidates[{i}]: contains 'assumed' personal fact")
    if _SILENT_RE.search(json.dumps(cand, ensure_ascii=False)):
        errors.append(f"candidates[{i}]: contains [SILENT]")

    # The model's score is a structural hint only (still must be a valid
    # integer — a "SCORE" placeholder is malformed). The final score is
    # recomputed deterministically from evidence below and overwrites it.
    score = cand.get("score")
    if isinstance(score, bool) or not isinstance(score, int) or not (0 <= score <= 100):
        errors.append(f"candidates[{i}].score: missing or not an integer 0..100")
        score = None
    c["score"] = score

    title = cand.get("title")
    if not isinstance(title, str) or not title.strip():
        errors.append(f"candidates[{i}].title: missing")
        title = ""
    c["title"] = title

    company = cand.get("company")
    if not isinstance(company, str) or not company.strip():
        errors.append(f"candidates[{i}].company: missing")
        company = ""
    c["company"] = company

    location = cand.get("location", "")
    if not isinstance(location, str):
        errors.append(f"candidates[{i}].location: must be a string")
        location = ""
    c["location"] = location

    ctype = cand.get("type")
    if ctype not in _ALLOWED_TYPES:
        errors.append(f"candidates[{i}].type: unsupported value {ctype!r}")
        ctype = "Other"
    c["type"] = ctype

    posted = cand.get("posted", "Not stated")
    if not isinstance(posted, str):
        errors.append(f"candidates[{i}].posted: must be a string")
        posted = "Not stated"
    c["posted"] = posted.strip() or "Not stated"

    salary = cand.get("salary", "")
    if not isinstance(salary, str):
        errors.append(f"candidates[{i}].salary: must be a string")
        salary = ""
    salary = _sanitize_salary(salary)

    status = cand.get("status")
    if status not in _ALLOWED_STATUSES:
        errors.append(f"candidates[{i}].status: unsupported value {status!r}")
        status = _STATUS_UNVERIFIED
    c["status"] = status

    apply_url = cand.get("apply_url", "")
    if not isinstance(apply_url, str) or not apply_url.strip():
        errors.append(f"candidates[{i}].apply_url: missing")
        apply_url = ""
    if "..." in apply_url:
        errors.append(f"candidates[{i}].apply_url: truncated URL")
    c["apply_url"] = apply_url

    canonical = canonicalize_url(apply_url)
    if canonical and canonical not in extracted:
        # HERMES_CRON_CANDIDATE_REJECTION_V1: provenance is mandatory —
        # a candidate whose apply_url was never extracted this run is
        # rejected (dropped), never a contract-wide error that would
        # discard otherwise-valid jobs.
        return None, f"candidates[{i}]: apply_url was not web_extract'ed this run"
    extracted_text = extracted.get(canonical, "")

    # GEOGRAPHY HARD GATE (HERMES_CRON_GEOGRAPHY_POLICY_V1)
    # A candidate outside the allowed markets is INVALID before scoring —
    # dropped, not downgraded.  Extracted-page evidence overrides the model's
    # location claim.
    geo_allowed, geo_tier, geo_reason = _resolve_geography(location, extracted_text)
    if not geo_allowed:
        return None, f"candidates[{i}]: {geo_reason or 'location outside allowed geography'}"
    c["_geo_tier"] = geo_tier

    # A 404/error/closed page is never a valid job page — reject the
    # candidate regardless of what the model claimed (a candidate
    # rejection, not a contract-wide error).
    if _has_page_error(extracted_text):
        return None, f"candidates[{i}]: extracted page is not a valid job page"
    if _has_closed_indicator(extracted_text):
        return None, f"candidates[{i}]: extracted page indicates closed/expired/filled"

    # Verified-open must be conservative: employer/ATS host only.
    if c["status"] == _STATUS_VERIFIED_OPEN and not _is_official_ats(apply_url):
        c["status"] = _STATUS_UNVERIFIED  # deterministic downgrade

    # Job identity / title provenance: the extracted page must describe this
    # exact role (significant title tokens must appear). A page describing a
    # different role, or a fabricated title, is unsupported.
    title_tokens = _significant_tokens(title)
    if title_tokens and extracted_text and not _mentions(title_tokens, extracted_text):
        return None, f"candidates[{i}]: title not supported by extracted page"

    # Company provenance: company tokens must appear in the page or the host.
    company_tokens = _significant_tokens(company)
    if company_tokens:
        host = (urlsplit(apply_url).netloc or "").lower()
        if not (
            _mentions(company_tokens, extracted_text)
            or _mentions(company_tokens, host)
        ):
            return None, f"candidates[{i}]: company not supported by extracted page"

    # Location provenance: an unsupported location claim is downgraded, never
    # fabricated.  Only a page- or Vellum-supported location survives.
    if location and _normalize_text(location) not in (
        "", "not stated", "not stated — verify",
    ) and _normalize_text(location) not in _RAW_REMOTE:
        loc_tokens = _significant_tokens(location)
        if loc_tokens and not _mentions(loc_tokens, extracted_text, threshold=0.5):
            location = "Not stated — verify"
    c["location"] = location

    # Salary provenance: downgrade when the figure isn't in the page.
    if _normalize_text(salary) != _normalize_text(_SENTINEL_SALARY) and not _salary_supported(
        salary, extracted_text
    ):
        salary = _SENTINEL_SALARY
    c["salary"] = salary

    # JD provenance: drop bullets the extracted page cannot support.  Fewer
    # than 3 evidence-backed bullets is a candidate rejection (dropped) —
    # never fabricated, and never a contract-wide error that would discard
    # otherwise-valid candidates.  (A non-list / sub-3 JD is still a
    # structural error.)
    jd_raw = cand.get("jd")
    jd_list = [str(b) for b in (jd_raw if isinstance(jd_raw, list) else [])]
    if not isinstance(jd_raw, list) or len(
        [b for b in jd_list if isinstance(b, str) and b.strip()]
    ) < 3:
        errors.append(f"candidates[{i}].jd: requires 3 evidence-backed bullets")
    supported_jd = [
        b for b in jd_list
        if b.strip() and _mentions(_significant_tokens(b), extracted_text)
    ]
    if len(supported_jd) < 3:
        return None, f"candidates[{i}]: fewer than 3 evidence-backed JD bullets"
    c["jd"] = supported_jd

    why_match = cand.get("why_match")
    if not isinstance(why_match, list) or len(
        [b for b in why_match if isinstance(b, str) and b.strip()]
    ) < 2:
        errors.append(f"candidates[{i}].why_match: requires at least 2 bullets")
    c["why_match"] = [str(b) for b in (why_match if isinstance(why_match, list) else [])]

    gaps = cand.get("gaps")
    if not isinstance(gaps, list) or len(
        [b for b in gaps if isinstance(b, str) and b.strip()]
    ) < 1:
        errors.append(f"candidates[{i}].gaps: requires at least 1 item")
    c["gaps"] = [str(b) for b in (gaps if isinstance(gaps, list) else [])]

    elig = cand.get("eligibility")
    if not isinstance(elig, dict):
        errors.append(f"candidates[{i}].eligibility: missing object")
        elig = {}
    requirement = elig.get("requirement", "")
    user_fit = elig.get("user_fit", "")
    sponsorship = elig.get("sponsorship", "")
    for key, value in (("requirement", requirement), ("user_fit", user_fit), ("sponsorship", sponsorship)):
        if not isinstance(value, str):
            errors.append(f"candidates[{i}].eligibility.{key}: must be a string")
    requirement = requirement if isinstance(requirement, str) else ""
    user_fit = user_fit if isinstance(user_fit, str) else ""
    sponsorship = sponsorship if isinstance(sponsorship, str) else ""
    user_fit = _sanitize_user_fit(user_fit, vellum_text)
    # Requirement provenance: an unsupported requirement is downgraded.
    if requirement and _normalize_text(requirement) not in (
        "", "not stated", "not stated — verify",
    ) and not _mentions(_significant_tokens(requirement), extracted_text, threshold=0.5):
        requirement = "Not stated — verify"
    c["eligibility"] = {
        "requirement": requirement,
        "user_fit": user_fit,
        "sponsorship": sponsorship,
    }

    # Deterministic score: the code owns the final number; the model's score
    # hint is overwritten with the evidence-derived value.  Component scores
    # are attached (internal only) for deterministic ranking and best-match.
    comps = _score_components(c, extracted_text, vellum_text)
    c["score"] = comps["total"]
    c["_role_score"] = comps["role"]
    c["_elig_score"] = comps["eligibility"]
    c["_geo_tier"] = comps["geo_tier"]
    c["_norm_comp"] = _normalize_compensation(c.get("salary", ""))

    return c, None


def _sanitize(contract, structure, ledger, errors, expected_date=None, wave_state=None):
    out = {}

    date = structure.get("date")
    if expected_date is not None:
        # The report date must match the trusted run-local execution date, and
        # the sanitized structure carries that trusted date so the renderer
        # never echoes a model-supplied date.
        if not isinstance(date, str) or date.strip() != expected_date:
            errors.append(f"date: expected {expected_date}, got {date!r}")
        out["date"] = expected_date
    else:
        if not isinstance(date, str) or not _DATE_RE.match(date.strip()):
            errors.append("date: must be YYYY-MM-DD")
            date = date.strip() if isinstance(date, str) else ""
        out["date"] = date if isinstance(date, str) else ""

    vm = structure.get("verified_matches")
    if isinstance(vm, bool) or not isinstance(vm, int):
        errors.append(f"verified_matches: must be an integer, got {vm!r}")
    elif not (0 <= vm <= 5):
        errors.append(f"verified_matches: must be an integer 0..5, got {vm}")

    raw_candidates = structure.get("candidates")
    if not isinstance(raw_candidates, list):
        errors.append("candidates: must be a list")
        raw_candidates = []
    if len(raw_candidates) > 5:
        errors.append(f"candidates: at most 5 allowed, got {len(raw_candidates)}")

    extracted = ledger["extracted"]
    vellum_text = ledger["vellum_text"]

    sanitized_candidates = []
    rejections = []
    for i, cand in enumerate(raw_candidates):
        if not isinstance(cand, dict):
            errors.append(f"candidates[{i}]: must be an object")
            continue
        sc, rejection = _sanitize_candidate(cand, i, extracted, vellum_text, errors)
        if sc is None:
            if rejection:
                rejections.append(rejection)
            continue
        sanitized_candidates.append(sc)

    # Deterministic ranking: score first, then geography priority, eligibility
    # certainty, role alignment, and verified compensation (highest first).
    sanitized_candidates.sort(key=_ranking_key)

    # The code owns verified_matches (= surviving candidates after the
    # geography hard gate), not the model's claimed count.
    out["verified_matches"] = len(sanitized_candidates)
    out["candidates"] = sanitized_candidates

    # Search coverage: distinguishes a true zero-match from a degraded search.
    out["_search_coverage"] = _compute_search_coverage(ledger)
    out["_search_waves"] = _search_waves_summary(ledger, wave_state)
    out["_rejections"] = rejections  # geography + job-identity candidate rejections

    # BEST MATCH TODAY is selected by code from the final deterministic
    # ranking; the model's best_match preference is never trusted.
    if sanitized_candidates:
        top = sanitized_candidates[0]
        why = (top.get("why_match") or [""])[0] if top.get("why_match") else ""
        out["best_match"] = {
            "candidate_index": 0,
            "score": top.get("score"),
            "why": why,
        }
    else:
        out["best_match"] = None

    return out


def validate_contract(name, text, messages, expected_date=None, wave_state=None):
    """Validate ``text`` against contract ``name`` using run-local evidence.

    ``expected_date`` is the trusted run-local execution date (YYYY-MM-DD).
    When provided, ``structure["date"]`` must equal it and the sanitized
    structure carries the trusted date (never the model's).

    Returns a :class:`ContractResult` whose ``structure`` is the sanitized
    structure (downgrades applied) or ``None`` when the text did not parse.
    """
    contract = CONTRACTS.get(name)
    if contract is None:
        return ContractResult(False, [f"output_schema: unknown contract {name!r}"], None)

    structure, parse_error = _parse_json(text)
    if parse_error:
        return ContractResult(False, [parse_error], None)
    if structure is None:
        return ContractResult(False, ["output: not valid JSON"], None)

    ledger = build_evidence_ledger(messages, contract)
    errors = []
    sanitized = _sanitize(contract, structure, ledger, errors, expected_date=expected_date, wave_state=wave_state)
    return ContractResult(not errors, errors, sanitized)


# ─────────────────────────────────────────────────────────────────────────
# Deterministic rendering
# ─────────────────────────────────────────────────────────────────────────


def _render_coverage(coverage):
    """Deterministic SEARCH COVERAGE section (complete/partial/incomplete)."""
    if coverage is None:
        return (
            "SEARCH COVERAGE: Partial\n"
            "Some search/extraction operations failed; results may be incomplete."
        )
    state = coverage.get("state", _COVERAGE_PARTIAL)
    if state == _COVERAGE_COMPLETE:
        detail = coverage.get("detail", "")
        line = "SEARCH COVERAGE: Complete"
        if detail:
            line += f" — {detail}"
        return line + "\nNo candidates survived the evidence/currentness/fit criteria."
    if state == _COVERAGE_INCOMPLETE:
        return (
            "SEARCH COVERAGE: Incomplete\n"
            "Web search provider unavailable or search not executed."
        )
    return (
        "SEARCH COVERAGE: Partial\n"
        "Some search/extraction operations failed; results may be incomplete."
    )


def _render_coverage_line(coverage):
    """Deterministic one-line SEARCH COVERAGE summary (report header)."""
    if coverage is None:
        return "SEARCH COVERAGE: Partial"
    state = coverage.get("state", _COVERAGE_PARTIAL)
    line = f"SEARCH COVERAGE: {state}"
    if state == _COVERAGE_COMPLETE:
        detail = coverage.get("detail", "")
        if detail:
            line += f" — {detail}"
    return line


def _render_search_waves(waves):
    """Deterministic SEARCH WAVES summary (driven waves marked ✓)."""
    waves = waves or {}
    parts = []
    if waves.get("us_core"):
        parts.append("US Core ✓")
    if waves.get("us_expansion"):
        parts.append("US Expansion ✓")
    if waves.get("international"):
        parts.append("International ✓")
    if not parts:
        return ""
    return "SEARCH WAVES: " + " | ".join(parts)


def render_zero_match(name, date=None, coverage=None, waves=None):
    """Deterministic visible zero-match report (no model prose involved)."""
    date = date if isinstance(date, str) else ""
    waves_line = _render_search_waves(waves)
    wave_block = f"{waves_line}\n\n" if waves_line else ""
    return (
        f"CAREER JOB MATCH REPORT — {date}\n\n"
        f"SEARCH PRIORITY: {_SEARCH_PRIORITY_LINE}\n\n"
        f"{wave_block}"
        "VERIFIED MATCHES: 0\n\n"
        f"{_render_coverage(coverage)}\n\n"
        "BEST MATCH TODAY:\n"
        "None\n"
    )


def render_contract(name, structure):
    """Render the human-readable report from a *sanitized* structure only."""
    if not isinstance(structure, dict):
        return ""
    date = structure.get("date") or ""
    vm = structure.get("verified_matches")
    candidates = structure.get("candidates") or []
    best_match = structure.get("best_match")

    coverage = structure.get("_search_coverage")

    if vm == 0 or not candidates:
        return render_zero_match(name, date, coverage, structure.get("_search_waves"))

    coverage_line = _render_coverage_line(coverage)
    waves_line = _render_search_waves(structure.get("_search_waves"))
    lines = [
        f"CAREER JOB MATCH REPORT — {date}",
        "",
        f"SEARCH PRIORITY: {_SEARCH_PRIORITY_LINE}",
        "",
        coverage_line,
    ]
    if waves_line:
        lines.append("")
        lines.append(waves_line)
    lines += ["", f"VERIFIED MATCHES: {vm}", ""]
    for idx, c in enumerate(candidates, 1):
        lines.append(f"{idx}. {c.get('score')}/100 — {c.get('title')}")
        lines.append(f"Company: {c.get('company')}")
        lines.append(f"Location: {c.get('location')}")
        lines.append(f"Type: {c.get('type')}")
        lines.append(f"Posted: {c.get('posted')}")
        lines.append(f"Salary: {c.get('salary')}")
        lines.append(f"Status: {c.get('status')}")
        lines.append(f"Apply: {c.get('apply_url')}")
        lines.append("")
        lines.append("JD:")
        for b in c.get("jd", []):
            lines.append(f"- {b}")
        lines.append("")
        lines.append("WHY YOU MATCH:")
        for b in c.get("why_match", []):
            lines.append(f"- {b}")
        lines.append("")
        lines.append("GAPS:")
        for b in c.get("gaps", []):
            lines.append(f"- {b}")
        lines.append("")
        elig = c.get("eligibility") or {}
        lines.append("ELIGIBILITY:")
        lines.append(f"- Requirement: {elig.get('requirement', '')}")
        lines.append(f"- User fit: {elig.get('user_fit', '')}")
        lines.append(f"- Sponsorship / work authorization: {elig.get('sponsorship', '')}")
        lines.append("")

    if isinstance(best_match, dict):
        bm_idx = best_match.get("candidate_index")
        bm = (
            candidates[bm_idx]
            if isinstance(bm_idx, int) and 0 <= bm_idx < len(candidates)
            else {}
        )
        lines.append("BEST MATCH TODAY:")
        lines.append(f"{bm.get('company', '')} — {bm.get('title', '')} — {bm.get('score')}/100")
        lines.append("")
        lines.append("WHY:")
        lines.append(best_match.get("why", ""))

    return "\n".join(lines).strip() + "\n"
