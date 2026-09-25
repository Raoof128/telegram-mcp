"""The typed egress matrix (comms v0.3 Task D36; A44, G19): what each tool's output may carry.

Every tool declares its content classes, and the sweep holds the running system to them:

- ``refs`` — opaque refs, states, counts, digests, times: every tool.
- ``body`` — provider message text, only inside ``untrusted_text``: the content reads.
- ``names`` — provider display names, only inside ``untrusted``: reads that list people.
- ``identity`` — a provider identity (a phone number, a chat or user id, an object id): only
  ``comms_admin_identity_inspect``, and only for the ref the owner names.

No tool may carry a secret (a token, a seed, an OAuth code or secret, key material).
"""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType

from comms.mcp.catalog import TOOL_CATALOG

__all__ = ["CLASSES", "EGRESS_MATRIX"]

CLASSES = frozenset({"refs", "body", "names", "identity"})
_BODY = frozenset(
    {
        "comms_context_get",
        "comms_context_recent",
        "comms_context_around_message",
        "comms_context_thread",
        "comms_context_search",
        "comms_context_page",
        "comms_message_get",
        "comms_message_recent",
        "comms_message_search",
        "comms_message_context",
        "comms_group_context",
    }
)
_NAMES = frozenset(
    {
        "comms_group_members_list",
        "comms_group_admins_list",
        "comms_context_get",
        "comms_group_context",
    }
)
_IDENTITY = frozenset({"comms_admin_identity_inspect"})


def _classes(name: str) -> frozenset[str]:
    classes = {"refs"}
    if name in _BODY:
        classes.add("body")
    if name in _NAMES:
        classes.add("names")
    if name in _IDENTITY:
        classes.add("identity")
    return frozenset(classes)


EGRESS_MATRIX: Mapping[str, frozenset[str]] = MappingProxyType(
    {spec.name: _classes(spec.name) for spec in TOOL_CATALOG}
)
