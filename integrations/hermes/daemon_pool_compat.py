#!/usr/bin/env python3
"""Make pinned Hermes' daemon executor tolerate CPython 3.14 internals."""

import sys
from pathlib import Path


MARKER = "# OPEN_CLOUD_DAEMON_POOL_PY314_V1"


def main():
    path = Path(sys.argv[1])
    source = path.read_text()
    if MARKER in source:
        return
    old = """            t = threading.Thread(
                name=thread_name,
                target=_worker,
                args=(
                    weakref.ref(self, weakref_cb),
                    self._work_queue,
                    self._initializer,
                    self._initargs,
                ),
                daemon=True,
            )
"""
    new = """            # OPEN_CLOUD_DAEMON_POOL_PY314_V1
            if hasattr(self, \"_create_worker_context\"):
                worker_args = (
                    weakref.ref(self, weakref_cb),
                    self._create_worker_context(),
                    self._work_queue,
                )
            else:
                worker_args = (
                    weakref.ref(self, weakref_cb),
                    self._work_queue,
                    getattr(self, \"_initializer\", None),
                    getattr(self, \"_initargs\", ()),
                )
            t = threading.Thread(
                name=thread_name,
                target=_worker,
                args=worker_args,
                daemon=True,
            )
"""
    if old not in source:
        # Older Hermes uses the standard executor and needs no compatibility patch.
        return
    path.write_text(source.replace(old, new, 1))


if __name__ == "__main__":
    main()
