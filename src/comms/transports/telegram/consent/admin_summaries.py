"""What an admin Touch ID prompt says (Phase-5 design §2.7, 0B G6).

The display field set is frozen, so the summary rides in ``action_display``,
which the agent renders up to 160 codepoints (``consent-agent.swift``
``renderableText``). The exact arguments stay bound by ``request_hmac``; this
line is what the human reads before approving. Secrets never appear.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

__all__ = ["LIMIT", "summarize"]

LIMIT = 160
_SECRETS = frozenset({"phone", "code", "password", "identity"})

# command -> (argument, label) pairs, in display order. Commands not listed
# show their name alone.
_FIELDS: dict[str, tuple[tuple[str, str], ...]] = {
    "client rotate": (("client", "client"),),
    "client disable": (("client", "client"),),
    "scope mode": (("mode", "mode"),),
    "scope allow": (("handle", "chat"),),
    "scope deny": (("handle", "chat"),),
    "scope remove": (("handle", "chat"),),
    "project create": (("slug", "slug"), ("display_name", "name")),
    "project rename": (("project_ref", "project"), ("display_name", "name")),
    "project enable": (("project_ref", "project"),),
    "project disable": (("project_ref", "project"),),
    "project add-peer": (("project_ref", "project"), ("handle", "chat")),
    "project remove-peer": (("project_ref", "project"), ("handle", "chat")),
    "project grant-client": (
        ("project_ref", "project"),
        ("client_ref", "client"),
        ("egress_level", "egress"),
        ("excerpt_max_codepoints", "max"),
    ),
    "project set-egress": (
        ("project_ref", "project"),
        ("client_ref", "client"),
        ("egress_level", "egress"),
        ("excerpt_max_codepoints", "max"),
    ),
    "project revoke-client": (("project_ref", "project"), ("client_ref", "client")),
    "project grant-cross-search": (("project_ref", "project"), ("client_ref", "client")),
    "project revoke-cross-search": (("project_ref", "project"), ("client_ref", "client")),
    "project instruction": (("project_ref", "project"), ("target", "for")),
}


def _short(value: Any) -> str:
    text = str(value)
    if len(text) > 17 and "_" in text[:5]:  # an opaque ref: prefix + 26
        return f"{text[:8]}…{text[-4:]}"
    return text if len(text) <= 40 else text[:39] + "…"


def summarize(command: str, args: Mapping[str, Any]) -> str:
    parts = [command]
    for name, label in _FIELDS.get(command, ()):
        if name in _SECRETS:
            continue
        value = args.get(name)
        if value is None:
            continue
        parts.append(f"{label} {_short(value)}")
    text = " · ".join(parts)
    return text if len(text) <= LIMIT else text[: LIMIT - 1] + "…"
