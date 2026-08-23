#!/usr/bin/env bash
# Lightweight runtime detection tick (journal → ingest). Separate from repair.
set -euo pipefail

export PATH="$HOME/.local/bin:$HOME/.opencode/bin:$HOME/.bun/bin:$HOME/bin:$PATH"

if [ -z "${OPEN_CLOUD_ROOT:-}" ]; then
    echo "ERROR: OPEN_CLOUD_ROOT is required for opencloud-self-heal-detect" >&2
    exit 1
fi

if [ ! -x "$OPEN_CLOUD_ROOT/bin/opencloud" ]; then
    echo "ERROR: opencloud CLI missing under OPEN_CLOUD_ROOT=$OPEN_CLOUD_ROOT" >&2
    exit 1
fi

exec "$OPEN_CLOUD_ROOT/bin/opencloud" self-heal detect "$@"
