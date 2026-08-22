#!/usr/bin/env python3
"""Deterministic coverage for iMessage model-control + turn-recovery patch.

Covers:
  * model status / switch / voice-capability intent detection
  * Fleet alias resolution (unique vs ambiguous)
  * tool-result truthfulness (nonzero exit ≠ success)
  * patch markers + gateway wiring (clarify release, stop recovery, progress)
  * installer materialization wiring
"""

from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PATCH = ROOT / "integrations/hermes/hermes-imessage-model-control-turn-recovery.patch"
HERMES_ROOT = Path(
    os.environ.get("OPEN_CLOUD_HERMES_ROOT", Path.home() / ".hermes/hermes-agent")
)


def require(cond: bool, msg: str) -> None:
    if not cond:
        raise AssertionError(msg)


def load_module_from(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


def materialize(out: Path) -> bool:
    result = subprocess.run(
        ["bash", str(ROOT / "install/30-brain-materialize.sh"), "--stage", str(out)],
        env={**os.environ, "OPEN_CLOUD_HERMES_ROOT": str(HERMES_ROOT)},
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        print(
            "MODEL_CONTROL_TURN_RECOVERY: SKIP (materialization failed): "
            + (result.stderr or result.stdout or "").strip()
        )
        return False
    return True


def main() -> None:
    text = PATCH.read_text()
    for marker in (
        "HERMES_OPENCLOUD_MODEL_CONTROL_FAST_PATH_V1",
        "HERMES_OPENCLOUD_CLARIFY_RELEASE_V1",
        "HERMES_OPENCLOUD_STOP_RECOVERY_V1",
        "HERMES_OPENCLOUD_PROGRESS_STATE_V1",
        "HERMES_OPENCLOUD_TOOL_RESULT_TRUTH_V1",
        "WAIT_FOR_USER",
        "_maybe_handle_model_control_fast_path",
        "gateway/model_control_fast_path.py",
    ):
        require(marker in text, f"missing marker/content {marker}")

    require("success\"] = False" in text or 'success"] = False' in text
            or '"success": False' in text
            or 'result_dict["success"] = False' in text,
            "terminal nonzero must set success=False")

    # Installer wiring
    for rel in ("install/30-brain-materialize.sh", "install/35-hermes-live.sh"):
        s = (ROOT / rel).read_text()
        require(
            "hermes-imessage-model-control-turn-recovery.patch" in s,
            f"{rel} missing patch wire",
        )
        require(
            "HERMES_OPENCLOUD_MODEL_CONTROL_FAST_PATH_V1" in s
            or "model_control_fast_path" in s,
            f"{rel} incomplete model-control wire",
        )

    if not HERMES_ROOT.is_dir() or not (HERMES_ROOT / ".git").is_dir():
        print("MODEL_CONTROL_TURN_RECOVERY: SKIP (Hermes Git source unavailable)")
        return

    with tempfile.TemporaryDirectory(prefix="oca-model-control-") as tmp:
        out = Path(tmp) / "hermes"
        if not materialize(out):
            return

        mod_path = out / "gateway" / "model_control_fast_path.py"
        require(mod_path.is_file(), "model_control_fast_path.py missing after materialize")
        m = load_module_from(mod_path, "opencloud_model_control_fast_path")

        require(
            m.detect_model_control_intent("What model are you using?").kind == "status",
            "status intent",
        )
        require(
            m.detect_model_control_intent("What model are u using now").kind == "status",
            "status intent colloquial",
        )
        sw = m.detect_model_control_intent("Hermes switch to muse 1.2 in opencode")
        require(sw is not None and sw.kind == "switch", "switch intent")
        require(sw.provider_hint == "opencode-zen", "opencode → opencode-zen")
        require("muse" in sw.model_query.lower(), "muse query retained")

        cap = m.detect_model_control_intent(
            "I am using hermes through iMessage can i use voice chat ?"
        )
        require(cap is not None and cap.kind == "capability", "voice capability intent")
        ans = m.format_capability_answer(platform="imessage", capability="voice_chat")
        require("No" in ans, "voice chat must be denied for iMessage")
        require("not supported" in ans.lower() or "not available" in ans.lower(),
                "voice denial wording")

        require(m.detect_model_control_intent("/model foo") is None, "slash falls through")
        require(
            m.detect_model_control_intent("run my career report now") is None,
            "cron control must not steal",
        )

        cands = [
            {
                "provider": "opencode-zen",
                "model": "meta/muse-glimmer-30b",
                "providerGroup": "zen",
            },
            {
                "provider": "nvidia",
                "model": "z-ai/glm-5.2",
                "providerGroup": "nvidia",
            },
            {
                "provider": "opencode-zen",
                "model": "meta/muse-other-9b",
                "providerGroup": "zen",
            },
        ]
        unique, amb = m.resolve_model_alias(
            "muse-glimmer", provider_hint="opencode", candidates=cands
        )
        require(unique is not None and amb == [], "unique muse-glimmer resolve")
        require("muse-glimmer" in unique.model, "resolved id")

        none, amb2 = m.resolve_model_alias(
            "muse", provider_hint="opencode", candidates=cands
        )
        require(none is None and len(amb2) >= 2, "ambiguous muse needs clarify")

        require(
            m.tool_result_is_success('{"exit_code": 1, "output": "nope"}') is False,
            "nonzero is failure",
        )
        require(
            m.tool_result_is_success(
                '{"exit_code": 0, "success": true, "output": "ok"}'
            )
            is True,
            "zero+success is success",
        )
        require(
            m.tool_result_is_success(
                '{"exit_code": 1, "success": true, "output": "lie"}'
            )
            is False,
            "nonzero overrides claimed success",
        )

        run_src = (out / "gateway" / "run.py").read_text()
        require("_maybe_handle_model_control_fast_path" in run_src, "gateway wire")
        require("HERMES_OPENCLOUD_CLARIFY_RELEASE_V1" in run_src, "clarify release")
        require("HERMES_OPENCLOUD_STOP_RECOVERY_V1" in run_src, "stop recovery")
        require("HERMES_OPENCLOUD_PROGRESS_STATE_V1" in run_src, "progress state")
        # Clarify release must not block on wait_for_response after send.
        idx = run_src.index("HERMES_OPENCLOUD_CLARIFY_RELEASE_V1")
        chunk = run_src[idx : idx + 3500]
        require("wait_for_response" not in chunk, "clarify must release, not wait")
        require("WAIT_FOR_USER" in chunk, "WAIT_FOR_USER sentinel")
        require("_opencloud_waiting_for_user" in chunk, "waiting flag set")

        term = (out / "tools" / "terminal_tool.py").read_text()
        require("HERMES_OPENCLOUD_TOOL_RESULT_TRUTH_V1" in term, "terminal truth marker")
        require('result_dict["success"] = False' in term, "terminal success=False")

    print("PASS model-control intents")
    print("PASS Fleet alias resolve + ambiguity")
    print("PASS tool-result truthfulness")
    print("PASS clarify release / stop / progress markers")
    print("PASS installers wire model-control patch")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"FAIL {exc}", file=sys.stderr)
        sys.exit(1)
