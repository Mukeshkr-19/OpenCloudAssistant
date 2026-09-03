#!/usr/bin/env python3
"""Deterministic regression coverage for the Hermes captured-baseline fetch.

Pinned Hermes-and-baseline compatibility suite is invoked by CI as
"Hermes compatibility / captured baseline" (.github/workflows/ci.yml). The
check existed and relied on a fresh anonymous HTTPS ``git fetch`` of the
pinned upstream commit on every run. github.com enforces a per-source rate
budget on its unauthenticated git endpoint and a one-off 429 burst was enough
to flip the post-merge push CI red even though every materialization step
itself was sound.

This test locks three invariants so a future regression cannot silently
return:

1. The pinned Hermes baseline SHA is a SINGLE source of truth: it appears in
   ``docs/COMPATIBILITY.md`` as ``Hermes Git HEAD:``, in
   ``install/30-brain-materialize.sh`` as ``HERMES_BASELINE_REV``, in
   ``install/35-hermes-live.sh`` as ``HERMES_BASELINE_REV``, and in
   ``.github/workflows/ci.yml`` as the ``hermes_commit:`` env on the
   "Fetch captured Hermes baseline" step. Any drift breaks CI loudly.

2. Both CI workflows use the standard authenticated checkout action for the
   external baseline instead of anonymous ``git fetch`` calls that share a
   rate-limited runner IP budget.

3. The pinned baseline is checkable on the real GitHub remote: a HEAD probe
   at the SHA must return HTTP 200 — the captured fixture is still alive on
   the upstream that the workflow will fetch from. This is the only network
   call in the suite and is intended to fail loudly if the upstream is moved
   or unreachable for a sustained period.

Keeping the test offline-by-default means the deterministic invariants (1)
and (2) run on every CI invocation without network, while invariant (3) only
runs when the harness explicitly opts in via ``OPEN_CLOUD_BASELINE_NETWORK_PROBE=1``
so local Mac test runs are not held hostage to github.com state.
"""

from __future__ import annotations

import os
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]

PINNED_SHA = "3fa318a50c02df8dbd2c55499f5f73d51ad77188"


def _read(path: Path) -> str:
    return path.read_text()


def _check_parity() -> str:
    """Verify the same pinned SHA appears in every authoritative location."""
    compat = _read(ROOT / "docs/COMPATIBILITY.md")
    brain = _read(ROOT / "install/30-brain-materialize.sh")
    live = _read(ROOT / "install/35-hermes-live.sh")
    ci = _read(ROOT / ".github/workflows/ci.yml")

    m_compat = re.search(r"^Hermes Git HEAD:\s*([0-9a-f]{40})", compat, re.MULTILINE)
    m_brain = re.search(
        r"^HERMES_BASELINE_REV=\"([0-9a-f]{40})\"", brain, re.MULTILINE
    )
    m_live = re.search(
        r"^HERMES_BASELINE_REV=\"([0-9a-f]{40})\"", live, re.MULTILINE
    )
    m_ci = re.search(
        r"^\s+hermes_commit:\s*([0-9a-f]{40})", ci, re.MULTILINE
    )

    assert m_compat, "docs/COMPATIBILITY.md must declare 'Hermes Git HEAD: <sha>'"
    assert m_brain, "install/30-brain-materialize.sh must declare HERMES_BASELINE_REV"
    assert m_live, "install/35-hermes-live.sh must declare HERMES_BASELINE_REV"
    assert m_ci, (
        "ci.yml 'Fetch captured Hermes baseline' step must declare "
        "hermes_commit env at the step level"
    )

    compat_sha = m_compat.group(1)
    assert compat_sha == PINNED_SHA, (
        f"docs/COMPATIBILITY.md pins {compat_sha}; expected {PINNED_SHA}"
    )
    assert m_brain.group(1) == PINNED_SHA, (
        f"install/30-brain-materialize.sh HERMES_BASELINE_REV={m_brain.group(1)};"
        f" expected {PINNED_SHA}"
    )
    assert m_live.group(1) == PINNED_SHA, (
        f"install/35-hermes-live.sh HERMES_BASELINE_REV={m_live.group(1)};"
        f" expected {PINNED_SHA}"
    )
    assert m_ci.group(1) == PINNED_SHA, (
        f"ci.yml hermes_commit env={m_ci.group(1)}; expected {PINNED_SHA}"
    )

    return (
        f"SHA parity OK across docs/COMPATIBILITY.md, "
        f"install/30-brain-materialize.sh, install/35-hermes-live.sh, .github/workflows/ci.yml = {PINNED_SHA}"
    )


def _check_workflow_resilience() -> str:
    """Verify both jobs use authenticated checkout and verify the exact SHA."""
    workflows = (
        ("ci.yml", "Materialization compatibility"),
        ("reliability.yml", "Install captured Hermes adapter dependency"),
    )
    for filename, next_step in workflows:
        workflow = _read(ROOT / ".github/workflows" / filename)
        checkout_idx = workflow.find("- name: Checkout captured Hermes baseline")
        assert checkout_idx >= 0, (
            f"{filename} is missing the captured-baseline checkout step"
        )
        verify_idx = workflow.find("- name: Verify captured Hermes baseline", checkout_idx)
        assert verify_idx > checkout_idx, (
            f"{filename} is missing baseline verification after checkout"
        )
        next_step_idx = workflow.find(f"- name: {next_step}", verify_idx)
        assert next_step_idx > verify_idx, (
            f"{filename} is missing '{next_step}' after the baseline fetch step"
        )
        checkout_block = workflow[checkout_idx:verify_idx]
        verify_block = workflow[verify_idx:next_step_idx]
        assert "uses: actions/checkout@v6" in checkout_block
        assert "repository: NousResearch/hermes-agent" in checkout_block
        assert f"ref: {PINNED_SHA}" in checkout_block
        assert "path: .hermes-baseline" in checkout_block
        assert "persist-credentials: false" in checkout_block
        assert "actual_commit=" in verify_block
        assert '"$actual_commit" != "$hermes_commit"' in verify_block
        assert "exit 1" in verify_block

    return (
        "CI baseline jobs use authenticated checkout and verify the exact SHA"
    )


def _check_upstream_probe() -> str:
    """Live HTTP HEAD probe of the pinned SHA at the upstream remote.

    Only runs when the harness explicitly opts in via
    ``OPEN_CLOUD_BASELINE_NETWORK_PROBE=1``. Otherwise the deterministic
    invariants (1) and (2) above are sufficient and the test is network-free.
    """
    url = f"https://github.com/NousResearch/hermes-agent/commit/{PINNED_SHA}"
    req = urllib.request.Request(url, method="HEAD")
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            code = resp.status
    except urllib.error.HTTPError as exc:  # pragma: no cover - exercised only when probe is enabled
        code = exc.code
    assert 200 <= code < 400, (
        f"upstream pinned baseline not reachable: {url} returned HTTP {code}; "
        f"the captured-baseline fixture is supposed to be the singular source "
        f"of truth — if the commit has been moved/rotated, update the SHA "
        f"everywhere (docs/COMPATIBILITY.md, install/30-brain-materialize.sh, "
        f"install/35-hermes-live.sh, .github/workflows/ci.yml) in lockstep"
    )
    return f"upstream HEAD ok: {url}"


def main() -> int:
    msgs: list[str] = []
    msgs.append("PASS " + _check_parity())
    msgs.append("PASS " + _check_workflow_resilience())
    if os.environ.get("OPEN_CLOUD_BASELINE_NETWORK_PROBE") == "1":
        msgs.append("PASS " + _check_upstream_probe())
    else:
        msgs.append(
            "SKIP upstream HTTP probe (set OPEN_CLOUD_BASELINE_NETWORK_PROBE=1 to enable)"
        )
    for m in msgs:
        print(m)
    print("CRON_BASELINE_FETCH_RELIABILITY: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
