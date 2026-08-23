"""Run-local reliability controls for career web search."""
from __future__ import annotations
import contextvars
import logging
import re
import threading
import time
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)
SEARCH_SUCCESS_WITH_RESULTS = "SEARCH_SUCCESS_WITH_RESULTS"
SEARCH_SUCCESS_EMPTY = "SEARCH_SUCCESS_EMPTY"
SEARCH_PROVIDER_FAILURE = "SEARCH_PROVIDER_FAILURE"
SEARCH_TIMEOUT = "SEARCH_TIMEOUT"
SEARCH_RATE_LIMIT = "SEARCH_RATE_LIMIT"
SEARCH_AUTH_FAILURE = "SEARCH_AUTH_FAILURE"
SEARCH_NETWORK_FAILURE = "SEARCH_NETWORK_FAILURE"
SEARCH_INVALID_REQUEST = "SEARCH_INVALID_REQUEST"
SEARCH_UNAVAILABLE = "SEARCH_UNAVAILABLE"
MAX_SEARCH_CALLS = 18
MAX_EXTRACT_CALLS = 12
MAX_SEARCH_FAILURES = 2
MAX_PROVIDER_SWITCHES = 1
MAX_SEARCH_ORCHESTRATION_TIME = 180.0
_controller_var: contextvars.ContextVar["CareerSearchController | None"] = contextvars.ContextVar("hermes_career_search_controller", default=None)

def current_controller() -> "CareerSearchController | None":
    return _controller_var.get()

def bind_controller(controller: "CareerSearchController"):
    return _controller_var.set(controller)

def reset_controller(token) -> None:
    _controller_var.reset(token)

def classify_search_error(error: Any) -> str:
    text = str(error or "").lower()
    if "no results" in text: return SEARCH_SUCCESS_EMPTY
    if "429" in text or "rate limit" in text or "too many requests" in text: return SEARCH_RATE_LIMIT
    if "401" in text or "403" in text or "auth" in text or "forbidden" in text: return SEARCH_AUTH_FAILURE
    if "timeout" in text or "timed out" in text: return SEARCH_TIMEOUT
    if any(x in text for x in ("connection", "network", "dns", "connecterror")): return SEARCH_NETWORK_FAILURE
    if any(x in text for x in ("invalid query", "malformed", "bad request")): return SEARCH_INVALID_REQUEST
    return SEARCH_PROVIDER_FAILURE

def classify_search_response(response: Any) -> str:
    if not isinstance(response, dict): return SEARCH_PROVIDER_FAILURE
    explicit = str(response.get("search_status") or "").strip()
    if explicit: return explicit
    if response.get("success") is False or response.get("error"): return classify_search_error(response.get("error"))
    data = response.get("data")
    return SEARCH_SUCCESS_WITH_RESULTS if isinstance(data, dict) and data.get("web") else SEARCH_SUCCESS_EMPTY

@dataclass
class SearchProviderState:
    provider: str
    attempts: int = 0
    successes: int = 0
    empty_successes: int = 0
    failures: int = 0
    consecutive_failures: int = 0
    distinct_failed_queries: set[str] = field(default_factory=set)
    last_failure_class: str = ""
    circuit_open: bool = False

class CareerSearchController:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self.started = time.monotonic()
        self.search_calls = 0
        self.extract_calls = 0
        self.search_failures = 0
        self.extract_failures = 0
        self.provider_switches = 0
        self.fallback_used = False
        self.provider_attempt_order: list[str] = []
        self.states: dict[str, SearchProviderState] = {}
        self.wave_state: dict[str, Any] = {}

    def _wave(self) -> str:
        if self.wave_state.get("wave3_issued"): return "wave3"
        if self.wave_state.get("wave2_issued"): return "wave2"
        return "wave1"

    def normalize_query(self, query: str) -> tuple[str, str | None]:
        raw = " ".join(str(query or "").split())
        if not raw: return "", SEARCH_INVALID_REQUEST
        raw = re.sub(r"\b(?:companycareers\.com|jobsite\.com)\b", "", raw, flags=re.I)
        role = raw.lower()
        if not any(x in role for x in ("devops", "sre", "reliability", "cloud", "platform", "infrastructure", "systems", "build", "release", "observability")):
            raw = "infrastructure cloud platform DevOps SRE " + raw
        if not any(x in role for x in ("intern", "internship", "co-op", "new grad", "entry level")):
            raw += " internship entry-level"
        geo = raw.lower()
        if self._wave() in ("wave1", "wave2") and not any(x in geo for x in ("united states", "usa", "u.s.")):
            raw += " United States"
        elif self._wave() == "wave3" and not any(x in geo for x in ("europe", "eu", "singapore", "malaysia", "uk")):
            raw += " Europe Singapore Malaysia"
        return " ".join(raw.split()), None

    def allow_search_attempt(self, query: str, provider: str) -> tuple[bool, str | None]:
        # HERMES_CAREER_SEARCH_ATOMIC_BOUNDS_V1
        # Tool calls from one model response may execute concurrently.  The
        # check and increment must be one operation or every caller can observe
        # the same stale count and collectively exceed the run budget.
        with self._lock:
            if time.monotonic() - self.started >= MAX_SEARCH_ORCHESTRATION_TIME: return False, SEARCH_TIMEOUT
            if self.search_calls >= MAX_SEARCH_CALLS: return False, SEARCH_UNAVAILABLE
            state = self.states.setdefault(provider, SearchProviderState(provider))
            if state.circuit_open: return False, SEARCH_UNAVAILABLE
            self.search_calls += 1
            return True, None

    def observe_search(self, query: str, provider: str, response: Any) -> str:
        with self._lock:
            state = self.states.setdefault(provider, SearchProviderState(provider))
            state.attempts += 1
            status = classify_search_response(response)
            if status in (SEARCH_SUCCESS_WITH_RESULTS, SEARCH_SUCCESS_EMPTY):
                state.successes += 1
                if status == SEARCH_SUCCESS_EMPTY: state.empty_successes += 1
                state.consecutive_failures = 0
            else:
                state.failures += 1
                state.consecutive_failures += 1
                state.distinct_failed_queries.add(query)
                state.last_failure_class = status
                self.search_failures += 1
                if len(state.distinct_failed_queries) >= MAX_SEARCH_FAILURES:
                    state.circuit_open = True
                    logger.warning("CAREER_SEARCH_CIRCUIT_OPEN provider=%s failures=%d", provider, state.failures)
            if provider not in self.provider_attempt_order: self.provider_attempt_order.append(provider)
            logger.info("CAREER_SEARCH_RESULT provider=%s status=%s calls=%d failures=%d wave=%s", provider, status, self.search_calls, self.search_failures, self._wave())
            return status

    def note_fallback(self, provider: str, alternate: str) -> bool:
        with self._lock:
            if self.provider_switches >= MAX_PROVIDER_SWITCHES:
                return False
            self.provider_switches += 1
            self.fallback_used = True
            logger.warning("CAREER_SEARCH_FALLBACK primary=%s alternate=%s switches=%d", provider, alternate, self.provider_switches)
            return True

    def allow_extract(self, urls: list[str]) -> tuple[list[str], str | None]:
        with self._lock:
            if time.monotonic() - self.started >= MAX_SEARCH_ORCHESTRATION_TIME: return [], SEARCH_TIMEOUT
            if self.extract_calls >= MAX_EXTRACT_CALLS: return [], SEARCH_UNAVAILABLE
            self.extract_calls += 1
            valid = [u.strip() for u in urls if re.match(r"^https?://[^\s]+$", str(u or "").strip(), re.I) and "..." not in str(u)]
            if not valid: return [], SEARCH_INVALID_REQUEST
            return valid[:5], None

    def observe_extract(self, response: Any) -> None:
        if not isinstance(response, list) or not response or all(isinstance(x, dict) and x.get("error") for x in response): self.extract_failures += 1

    def can_continue_wave(self) -> bool:
        if time.monotonic() - self.started >= MAX_SEARCH_ORCHESTRATION_TIME: return False
        return self.search_calls < MAX_SEARCH_CALLS or any(x.successes for x in self.states.values())
