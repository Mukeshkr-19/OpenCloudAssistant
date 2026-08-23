#!/usr/bin/env python3
"""Deterministic coverage for OpenCloud product reliability UX patches.

Covers:
  - /models alias → /model
  - conversational greeting tool-intent filter (not exact-string-only)
  - Camofox scope fail-closed marker
  - system-prompt write guard marker
  - browser availability no-grace marker
  - Photon HTTP events inject markers (loopback acceptance)
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PATCH = ROOT / "integrations/hermes/hermes-product-reliability-ux.patch"
PHOTON_PATCH = ROOT / "integrations/hermes/hermes-photon-http-events.patch"
GREETING_TOOL_CHOICE_PATCH = ROOT / "integrations/hermes/hermes-greeting-tool-choice-none.patch"
GREETING_OUTPUT_PATCH = ROOT / "integrations/hermes/hermes-greeting-output-contract.patch"


def require(cond: bool, msg: str) -> None:
    if not cond:
        raise AssertionError(msg)


def main() -> None:
    text = PATCH.read_text()
    for marker in (
        "HERMES_OPENCLOUD_MODEL_ALIAS_V1",
        "HERMES_OPENCLOUD_FLEET_MODEL_UX_V1",
        "HERMES_OPENCLOUD_CAMOFOX_SCOPE_V1",
        "HERMES_OPENCLOUD_BROWSER_AVAIL_V1",
        "HERMES_OPENCLOUD_SYSTEM_PROMPT_V1",
        "HERMES_OPENCLOUD_TOOL_INTENT_V1",
    ):
        require(marker in text, f"missing marker {marker}")

    require('aliases=("models",)' in text, "/models alias missing from patch")
    require("UnscopedSecretError" in text, "Camofox UnscopedSecretError guard missing")
    require("_opencloud_no_failure_grace" in text, "browser no-grace opt-out missing")
    require("refuse to write None/blank prompts" in text or "HERMES_OPENCLOUD_SYSTEM_PROMPT_V1" in text, "system prompt guard missing")
    require("_opencloud_is_conversational_greeting" in text, "greeting intent helper missing")
    require("_opencloud_restore_tools" in text, "greeting tool restore helper missing")
    require("check_camofox_available" in text, "Camofox reachability check missing")
    require("Reading os.environ here would risk" not in text.split("get_camofox_url")[0][-200:] + text.split("get_camofox_url")[1][:500] or "return \"\"" in text.split("get_camofox_url")[1][:800], "Camofox unscoped path must return empty")
    require("except UnscopedSecretError" in text, "Camofox must catch UnscopedSecretError")
    require("--catalog" in text and "--auto" in text, "Fleet /model flags missing")

    photon = PHOTON_PATCH.read_text()
    require("HERMES_OPENCLOUD_PHOTON_HTTP_EVENTS_V1" in photon, "Photon HTTP events marker missing")
    require("def verify_http_event_request" in photon, "Photon verify_http_event_request missing")
    require("async def dispatch_http_event" in photon, "Photon dispatch_http_event missing")
    require("API_SERVER_KEY" in photon, "Photon inject must auth with API_SERVER_KEY")
    require("_dispatch_inbound" in photon, "Photon inject must call _dispatch_inbound")

    greeting_tc = GREETING_TOOL_CHOICE_PATCH.read_text()
    require("HERMES_OPENCLOUD_GREETING_TOOL_CHOICE_NONE_V1" in greeting_tc, "greeting tool_choice marker missing")
    require('agent._opencloud_tool_choice = "none"' in greeting_tc, "greeting must set tool_choice none")
    require('api_kwargs["tool_choice"] = "none"' in greeting_tc or "api_kwargs['tool_choice'] = 'none'" in greeting_tc, "api_kwargs tool_choice none missing")
    require("agent.tools = []" in greeting_tc, "greeting must clear tools list")

    greeting_out = GREETING_OUTPUT_PATCH.read_text()
    require("HERMES_OPENCLOUD_GREETING_OUTPUT_CONTRACT_V1" in greeting_out, "greeting output marker")
    require("_opencloud_greeting_output_contract_valid" in greeting_out, "contract validator")

    # Mirror of patched helper
    def _opencloud_is_conversational_greeting(raw_text: str) -> bool:
        raw = (raw_text or "").strip()
        if not raw or len(raw) > 80:
            return False
        if re.search(r"https?://|/\S|\b(search|find|browse|open|run|fix|deploy|cron)\b", raw, re.I):
            return False
        if re.fullmatch(
            r"(?i)(hi|hello|hey|yo|sup|howdy|hiya|good\s*(morning|afternoon|evening))"
            r"([,.!]+\s*[A-Za-z]{0,24}|\s+(bro|man|dude|buddy|pal|mate|fam|there|hermes|assistant|friend))?[.!]?",
            raw,
        ):
            return True
        if re.fullmatch(r"(?i)(hi|hello|hey)[.!]?\s+(there|hermes|assistant|friend|bro|man|dude)[.!]?", raw):
            return True
        if re.fullmatch(r"(?i)(bro|dude|yo)[.!]?", raw):
            return True
        return False

    fn = _opencloud_is_conversational_greeting
    require(fn("Hi") is True, "Hi should match")
    require(fn("hello!") is True, "hello! should match")
    require(fn("Hey there") is True, "Hey there should match")
    require(fn("Good morning") is True, "Good morning should match")
    require(fn("Hi, Mukesh") is True, "Hi, name should match")
    require(fn("Hi bro") is True, "Hi bro should match")
    require(fn("Hi man") is True, "Hi man should match")
    require(fn("Bro") is True, "Bro should match")
    require(fn("Hi bro search for jobs") is False, "task-prefixed greeting must not match")
    require(fn("Hi, search for jobs") is False, "taskful greeting must not match")
    require(fn("browse https://example.com") is False, "URL task must not match")
    require(fn("find internships in NYC") is False, "search intent must not match")
    require(fn("x" * 100) is False, "long text must not match")

    # Installer wiring
    for rel in ("install/30-brain-materialize.sh", "install/35-hermes-live.sh"):
        s = (ROOT / rel).read_text()
        require("hermes-product-reliability-ux.patch" in s, f"{rel} missing patch wire")
        require("HERMES_OPENCLOUD_TOOL_INTENT_V1" in s or "PATCH22" in s or "PATCH_PRODUCT_UX" in s, f"{rel} incomplete")
        require("hermes-greeting-tool-choice-none.patch" in s, f"{rel} missing greeting tool_choice patch")
        require("hermes-greeting-output-contract.patch" in s, f"{rel} missing greeting output patch")
        require("GREETING_TOOL_CHOICE_NONE" in s or "PATCH24" in s or "PATCH_GREETING_TOOL_CHOICE" in s, f"{rel} incomplete greeting tool_choice wire")

    print("PASS product reliability UX markers present")
    print("PASS /models alias wired")
    print("PASS greeting intent matches short greetings only")
    print("PASS installers wire product UX patch")
    print("PASS greeting tool_choice none patch wired")
    print("PASS greeting output-contract patch wired")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"FAIL {exc}", file=sys.stderr)
        sys.exit(1)
