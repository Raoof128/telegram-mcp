"""Target resolution with an explicit ambiguity refusal (comms v0.3 Task D7; P §81).

A name resolves to exactly one ref, ``NOT_FOUND``, or ``AMBIGUOUS_TARGET``. Matching is a
case- and space-insensitive substring over the owner's own labels; more than one match is
always ambiguous (an exact name does not silently win), because a wrong guess becomes a
mutation. Candidates carry refs and labels only: never a phone number, a Telegram id or message
text (a message candidate is labelled by its time).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from comms.core.campaigns.directory import group_destinations, named_recipients
from comms.core.errors import CommsError
from comms.core.groups import group_ref

__all__ = ["Candidate", "Resolution", "resolve_group", "resolve_message", "resolve_person"]


@dataclass(frozen=True)
class Candidate:
    ref: str
    label: str


@dataclass(frozen=True)
class Resolution:
    ref: str | None
    candidates: tuple[Candidate, ...]


def _normal(text: str) -> str:
    return " ".join(text.casefold().split())


def _query(query: object) -> str:
    if not isinstance(query, str) or not _normal(query) or len(query) > 200:
        raise CommsError("INVALID_ARGUMENT")
    return _normal(query)


def _decide(candidates: Sequence[Candidate], *, refuse: bool) -> Resolution:
    if not candidates:
        raise CommsError("NOT_FOUND")
    if len(candidates) == 1:
        return Resolution(candidates[0].ref, ())
    if refuse:
        raise CommsError("AMBIGUOUS_TARGET")
    return Resolution(None, tuple(candidates))


def _matching(query: str, labelled: Iterable[tuple[str, str]]) -> list[Candidate]:
    return [Candidate(ref, label) for ref, label in labelled if query in _normal(label)]


def resolve_person(conn: Any, query: str, *, refuse: bool = True) -> Resolution:
    return _decide(_matching(_query(query), named_recipients(conn)), refuse=refuse)


def resolve_group(conn: Any, query: str, *, now: datetime, refuse: bool = True) -> Resolution:
    wanted = _query(query)
    matches = [
        (dst, f"{name} · {location}")
        for dst, name, location in group_destinations(conn)
        if wanted in _normal(name) or wanted in _normal(location)
    ]
    candidates = [Candidate(group_ref(conn, dst, now=now), label) for dst, label in matches]
    return _decide(candidates, refuse=refuse)


def resolve_message(items: Sequence[Mapping[str, Any]], *, refuse: bool = True) -> Resolution:
    """One message among context search results (D8); labelled by time, never by its text."""
    candidates = [
        Candidate(str(item["message_ref"]), str(item.get("sent_at", "")))
        for item in items
        if isinstance(item.get("message_ref"), str)
    ]
    return _decide(candidates, refuse=refuse)
