#!/usr/bin/env python3
from __future__ import annotations

import ast
import asyncio
import json
import os
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
HERMES = Path(os.environ.get("OPEN_CLOUD_HERMES_ROOT", str(Path.home() / ".hermes/hermes-agent")))


def main():
    policy = json.loads((ROOT / "config/hermes/orchestration.json").read_text())
    display = policy["display"]["platforms"]["bluebubbles"]
    assert display == {
        "tool_progress": "off",
        "show_reasoning": False,
        "streaming": False,
        "interim_assistant_messages": False,
        "long_running_notifications": False,
        "busy_ack_detail": False,
        "thinking_progress": False,
    }

    bluebubbles = (HERMES / "gateway/platforms/bluebubbles.py").read_text()
    delivery = (HERMES / "gateway/delivery.py").read_text()
    cron = (HERMES / "cron/scheduler.py").read_text()
    assert "MAX_TEXT_LENGTH = 4000" in bluebubbles
    assert "splits_long_messages = True" in bluebubbles
    assert "for chunk in chunks:" in bluebubbles
    assert "return SendResult(success=False" in bluebubbles
    assert 'getattr(adapter, "splits_long_messages", False)' in delivery
    assert "content reaches the adapter" in delivery
    assert "def _confirm_adapter_delivery" in cron
    assert "return bool(getattr(send_result, \"success\"))" in cron

    # Exercise the real adapter with a synthetic transport. This is not a live
    # BlueBubbles E2E test; it proves ordered chunks, URL preservation where a
    # URL fits in one chunk, final-content delivery, and explicit failure.
    sys.path.insert(0, str(HERMES))
    from gateway.platforms.bluebubbles import BlueBubblesAdapter

    adapter = BlueBubblesAdapter.__new__(BlueBubblesAdapter)
    adapter.MAX_MESSAGE_LENGTH = 120
    adapter._private_api_enabled = False
    adapter._helper_connected = False
    adapter.format_message = lambda content: content
    sent = []

    async def resolve(_chat_id):
        return "synthetic-guid"

    async def post(_path, payload):
        sent.append(payload["message"])
        return {"data": {"guid": f"synthetic-{len(sent)}"}}

    adapter._resolve_chat_guid = resolve
    adapter._api_post = post
    url = "https://example.invalid/research/result?token=synthetic-value"
    final = ("first " * 16) + url + "\n\nFinal response preserved."
    result = asyncio.run(adapter.send("synthetic-chat", final))
    assert result.success and len(sent) >= 2
    expected = adapter.truncate_message(final.split("\n\n", 1)[0], 120) + ["Final response preserved."]
    assert sent == expected
    assert sent[-1] == "Final response preserved."
    assert sum(url in chunk for chunk in sent) == 1

    attempts = []
    async def fail_second(_path, payload):
        attempts.append(payload["message"])
        if len(attempts) == 2:
            raise RuntimeError("synthetic delivery failure")
        return {"data": {"guid": "ok"}}
    adapter._api_post = fail_second
    failed = asyncio.run(adapter.send("synthetic-chat", "one\n\ntwo\n\nthree"))
    assert not failed.success and "synthetic delivery failure" in failed.error
    assert attempts == ["one", "two"]

    for boundary in (2, 3):
        attempts = []
        async def fail_at_boundary(_path, payload, boundary=boundary):
            attempts.append(payload["message"])
            if len(attempts) == boundary:
                raise RuntimeError(f"synthetic chunk {boundary} failure")
            return {"data": {"guid": "ok"}}
        adapter._api_post = fail_at_boundary
        failed = asyncio.run(adapter.send("synthetic-chat", "one\n\ntwo\n\nthree\n\nfour"))
        assert not failed.success
        assert attempts == ["one", "two", "three", "four"][:boundary]

    unicode_text = "😀資料é" * 70
    unicode_chunks = adapter.truncate_message(unicode_text, 120)
    assert "".join(unicode_chunks) == unicode_text
    long_url = "https://example.invalid/" + "segment" * 50
    url_chunks = adapter.truncate_message(long_url, 120)
    assert "".join(url_chunks) == long_url

    tree = ast.parse(cron)
    confirm = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "_confirm_adapter_delivery")
    namespace = {}
    exec(compile(ast.Module(body=[confirm], type_ignores=[]), "scheduler.py", "exec"), namespace)
    class Result:
        def __init__(self, success): self.success = success
    assert namespace["_confirm_adapter_delivery"](Result(True))
    assert not namespace["_confirm_adapter_delivery"](Result(False))
    assert not namespace["_confirm_adapter_delivery"](None)

    print("PASS BlueBubbles final-only policy")
    print("PASS BlueBubbles ordered native chunking contract")
    print("PASS cron delivery preserves full output for chunking adapters")
    print("PASS delivery success requires explicit confirmation")
    print("PASS synthetic adapter preserves final response, chunk order, and fitting URL")
    print("PASS synthetic adapter stops and surfaces chunk delivery failure")
    print("PASS Unicode and long URL content reconstruct exactly across boundaries")
    print("PASS second/third chunk failures stop once without duplicate retries")
    print("MESSAGING_DELIVERY_RELIABILITY: PASS")


if __name__ == "__main__":
    main()
