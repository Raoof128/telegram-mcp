"""Egress transformation (frozen spec §23B.2; design §3.1).

Runs after retrieval and revalidation, before accounting, signing and
serialisation. Deterministic, and it never summarises.

**Message text is never sanitised here.** Stripping control characters from
a Telegram body would corrupt the evidence the model is reading. The only
content change this module makes is excerpt truncation.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

__all__ = [
    "EGRESS_ORDER",
    "effective_egress_level",
    "intersect_profiles",
    "transform_record",
]

# metadata_only < excerpt < full_text (spec §23B.2).
EGRESS_ORDER: tuple[str, ...] = ("metadata_only", "excerpt", "full_text")
_RANK = {level: index for index, level in enumerate(EGRESS_ORDER)}


def _check_level(level: str) -> str:
    if level not in _RANK:
        raise ValueError("unknown egress level")
    return level


def intersect_profiles(
    profiles: Sequence[tuple[str, int | None]],
) -> tuple[str, int | None]:
    """Most restrictive level wins; among excerpts the smallest limit wins."""
    if not profiles:
        raise ValueError("no egress profiles to intersect")
    level = min((_check_level(p[0]) for p in profiles), key=lambda name: _RANK[name])
    if level != "excerpt":
        return level, None
    limits = [p[1] for p in profiles if p[0] == "excerpt" and p[1] is not None]
    if not limits:
        raise ValueError("excerpt profile without a limit")
    return level, min(limits)


def transform_record(
    record: Mapping[str, Any], level: str, excerpt_max: int | None
) -> dict[str, Any]:
    """Return a new record carrying only the authorised content class."""
    _check_level(level)
    out = dict(record)
    text = out.get("text")

    if level == "metadata_only":
        out["text"] = None
        out["text_truncated"] = False
        return out

    if level == "excerpt":
        if excerpt_max is None:
            raise ValueError("excerpt level requires a limit")
        if isinstance(text, str) and len(text) > excerpt_max:
            # Codepoints, not bytes, not grapheme clusters (spec §23B.2).
            out["text"] = text[:excerpt_max]
            out["text_truncated"] = True
        else:
            out["text_truncated"] = bool(out.get("text_truncated", False))
        return out

    out["text_truncated"] = bool(out.get("text_truncated", False))
    return out


def effective_egress_level(records: Sequence[Mapping[str, Any]]) -> str:
    """The actual maximum content class present, not the grant's ceiling.

    A response that happens to carry only metadata is ``metadata_only`` even
    under a ``full_text`` grant (spec §23B.2).
    """
    level = "metadata_only"
    for record in records:
        if record.get("text") is None:
            continue
        level = "excerpt" if record.get("text_truncated") else "full_text"
        if level == "full_text":
            return level
    return level
