"""Guard against OpenCloud-internal metadata leaking into provider SDK requests.

Routing V1 and Fleet stash internal control metadata (currently the workload
``routing_profile``) inside ``request_overrides`` so that ``AIAgent``
construction can consume it before any provider request is built. Those keys
are control-plane metadata, not provider kwargs; if one of them ever reaches
``chat.completions.create()`` / ``responses.create()`` the provider SDK raises
``TypeError`` (``got an unexpected keyword argument``).

This module exposes the single strip function that both the gateway and the
provider transports call at the final boundary, so no path — streaming,
non-streaming, fallback, retry, or any provider — can forward internal
metadata to a provider SDK.
"""

from typing import Any, Dict

# HERMES_OPENCLOUD_METADATA_GUARD_V1
_INTERNAL_METADATA_PREFIX = "_opencloud_"


def strip_internal_metadata(overrides: Any) -> Dict[str, Any]:
    """Return ``overrides`` without any OpenCloud-internal control metadata.

    Any key whose string form starts with ``_opencloud_`` is dropped. All other
    keys (``extra_body``, ``service_tier``, ``speed``, provider-specific
    extensions, ...) are preserved unchanged. Non-dict inputs are returned
    as-is so callers that pass ``None``/empty values keep their existing
    behaviour.
    """
    if not isinstance(overrides, dict):
        return overrides

    return {
        key: value
        for key, value in overrides.items()
        if not str(key).startswith(_INTERNAL_METADATA_PREFIX)
    }
