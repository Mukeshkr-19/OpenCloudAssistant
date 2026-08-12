#!/usr/bin/env python3
import sys
from pathlib import Path

MARKER = "# HERMES_SILENT_GATEWAY_LIFECYCLE_NOTICE_V1"

def main():
    if len(sys.argv) != 2:
        raise SystemExit("usage: silent_gateway_lifecycle.py /path/to/gateway/run.py")

    path = Path(sys.argv[1])
    source = path.read_text()

    if MARKER in source:
        print("SILENT_GATEWAY_LIFECYCLE: ALREADY_PRESENT")
        return

    needle = (
        "    async def _notify_active_sessions_of_shutdown(self) -> None:\n"
        "        \"\"\"Send shutdown/restart notifications to active chats and home channels.\n\n"
        "        Called at the very start of stop() — adapters are still connected so\n"
        "        messages can be delivered. Best-effort: individual send failures are\n"
        "        logged and swallowed so they never block the shutdown sequence.\n"
        "        \"\"\"\n"
    )

    replacement = needle + (
        "        # HERMES_SILENT_GATEWAY_LIFECYCLE_NOTICE_V1\n"
        "        # Gateway lifecycle notifications are internal control flow.\n"
        "        # Preserve shutdown, interruption, recovery and service logging,\n"
        "        # but keep routine restart/stop chatter out of user conversations.\n"
        "        lifecycle_notices = os.getenv(\"HERMES_GATEWAY_LIFECYCLE_NOTICES\", \"\").strip().lower()\n"
        "        if lifecycle_notices not in {\"1\", \"true\", \"yes\", \"on\"}:\n"
        "            logger.info(\"Suppressing user-facing gateway lifecycle notification\")\n"
        "            return\n"
    )

    if needle not in source:
        raise SystemExit("shutdown notification function signature not found")

    path.write_text(source.replace(needle, replacement, 1))
    print("SILENT_GATEWAY_LIFECYCLE: PATCHED")

if __name__ == "__main__":
    main()
