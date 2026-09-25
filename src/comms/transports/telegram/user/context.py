"""The MTProto context source (comms v0.3 Task C20; P §19–21; A32).

The Phase-4 read engine without its authority wrapper: the adapter's reviewed read primitives
(``fetch_history``, ``search_peer``, ``fetch_participants``, whose page ends come from
Telegram's own signal, ``_page_end``) bounded by the same §13.2 ``PageBudget`` (bytes and
32,000 codepoints) and field clamps. No policy, disclosure budget, ref store or project
snapshot is consulted here; the Part D context service owns those. Everything is
``telegram_live``; provider text sits under ``untrusted`` (A32). When the page budget cuts a
page short, the cursor resumes right after the last item kept.
"""

from __future__ import annotations

from collections.abc import Callable, Coroutine, Mapping
from datetime import datetime
from typing import Any, Protocol

from comms.core import timeutil
from comms.core.providers.protocols import ContextPage, ContextQuery, ContextRefused
from comms.transports.telegram.args import take, text
from comms.transports.telegram.page_bounds import NAME_MAX, TEXT_MAX, PageBudget, clamp
from comms.transports.telegram.peers import marked_chat_id, unmark_chat_id
from comms.transports.telegram.telegram.deadline import Deadline, WorkBudget
from comms.transports.telegram.telegram.errors import GatewayError

__all__ = ["UserContext"]

ACTOR = "telegram_user"
PROVENANCE = "telegram_live"
MAX_LIMIT = 100
MAX_AROUND = 50
READ_TIMEOUT_S = 15.0
_MARK_KIND = {"user": "user", "chat": "group", "channel": "channel"}
_REFUSAL = {"NOT_ACCESSIBLE": "NOT_AUTHORIZED", "MESSAGE_NOT_FOUND": "TARGET_NOT_FOUND"}
_PASSED_ON = frozenset({"SESSION_REVOKED", "ACCOUNT_UNAVAILABLE", "AUTH_REQUIRED"})


class ReadSession(Protocol):
    def readiness(self) -> str | None: ...

    async def fetch_history(self, peer_type: str, peer_id: int, **kw: Any) -> Any: ...

    async def search_peer(self, peer_type: str, peer_id: int, query: str, **kw: Any) -> Any: ...

    async def fetch_participants(
        self, peer_type: str, peer_id: int, *, offset: int, limit: int, timeout: float
    ) -> tuple[list[tuple[int, str, str | None]], int | None]: ...


Runner = Callable[[Coroutine[Any, Any, Any]], Any]


def _limit(value: object) -> bool:
    return type(value) is int and 1 <= value <= MAX_LIMIT


def _cursor(value: object) -> bool:
    return isinstance(value, str) and value.isascii() and value.isdigit() and int(value) > 0


def _span(value: object) -> bool:
    return type(value) is int and 0 <= value <= MAX_AROUND


class UserContext:
    def __init__(self, session: ReadSession, *, run: Runner, clock: Callable[[], datetime]) -> None:
        self._session, self._run, self._clock = session, run, clock

    def __repr__(self) -> str:
        return "UserContext(<redacted>)"

    def read(self, query: ContextQuery) -> ContextPage:
        if query.target.actor != ACTOR:
            raise ValueError("not a telegram_user destination")
        peer = unmark_chat_id(query.target.identity)
        kind, args = query.kind, query.args
        if kind == "recent":
            fields = take(args, {}, {"limit": _limit, "cursor": _cursor})
        elif kind == "around":
            fields = take(args, {"message_id": _cursor_int}, {"before": _span, "after": _span})
        elif kind == "search":
            fields = take(args, {"query": text(1, 256)}, {"limit": _limit, "cursor": _cursor})
        elif kind == "members":
            fields = take(args, {}, {"limit": _limit, "cursor": _cursor})
        else:
            raise ContextRefused("PROVIDER_UNSUPPORTED")
        refused = self._session.readiness()
        if refused is not None:
            raise ContextRefused(refused if refused in _PASSED_ON else "UNAVAILABLE")
        try:
            return self._run(self._read(kind, peer, fields))  # type: ignore[no-any-return]
        except GatewayError as failed:
            raise ContextRefused(_REFUSAL.get(failed.code, "UNAVAILABLE")) from None

    async def _read(
        self, kind: str, peer: tuple[str, int], fields: Mapping[str, Any]
    ) -> ContextPage:
        observed = timeutil.iso(self._clock())
        common: dict[str, Any] = {
            "client_ref": "comms",
            "deadline": Deadline(READ_TIMEOUT_S),
            "budget": WorkBudget(max_rpcs=1),
        }
        if kind == "members":
            offset = int(fields.get("cursor", 0))
            rows, more = await self._session.fetch_participants(
                *peer, offset=offset, limit=fields.get("limit", 50), timeout=READ_TIMEOUT_S
            )
            items = [_member(row, observed) for row in rows]
            return _page(items, str(more) if more is not None else None, cursor_of=None)
        if kind == "search":
            result = await self._session.search_peer(
                *peer,
                fields["query"],
                min_date=None,
                max_date=None,
                offset_id=int(fields.get("cursor", 0)),
                limit=fields.get("limit", 20),
                **common,
            )
            views, below = result.views, result.next_offset
        elif kind == "around":
            before, after = fields.get("before", 10), fields.get("after", 10)
            views, _below = await self._session.fetch_history(
                *peer,
                offset_id=fields["message_id"],
                add_offset=-(after + 1),
                max_id=0,
                limit=before + after + 1,
                **common,
            )
            below = None
        else:
            views, below = await self._session.fetch_history(
                *peer,
                offset_id=int(fields.get("cursor", 0)),
                max_id=0,
                limit=fields.get("limit", 20),
                **common,
            )
        items = [_message(view, observed) for view in views]
        return _page(items, str(below) if below is not None else None, cursor_of=kind != "around")


def _cursor_int(value: object) -> bool:
    return type(value) is int and value > 0


def _page(
    items: list[dict[str, Any]], next_cursor: str | None, *, cursor_of: bool | None
) -> ContextPage:
    """Keep the longest prefix within the §13.2 caps; resume after the last one kept."""
    budget = PageBudget({"items": [], "provenance": PROVENANCE, "next_cursor": "9" * 20})
    kept = []
    for item in items:
        if not budget.take(item):
            break
        kept.append(item)
    if len(kept) < len(items) and cursor_of:
        next_cursor = str(kept[-1]["message_id"]) if kept else next_cursor
    return ContextPage(tuple(kept), PROVENANCE, next_cursor)


def _message(view: Any, observed: str) -> dict[str, Any]:
    body, _cut = clamp(view.text, TEXT_MAX)
    name, _cut = clamp(view.sender_display_name or view.post_author, NAME_MAX)
    sender = view.sender
    untrusted = {k: v for k, v in (("text", body), ("sender_name", name)) if v is not None}
    return {
        "source": PROVENANCE,
        "observed_at": observed,
        "message_id": view.message_id,
        "sent_at": view.sent_at,
        "outgoing": view.outgoing,
        "sender_kind": view.sender_kind,
        "sender_id": marked_chat_id(f"{_MARK_KIND[sender[0]]}:{sender[1]}") if sender else None,
        "reply_to_id": view.reply_to_id,
        "forum_topic": view.forum_topic,
        "has_media": view.has_media,
        "media_kind": view.media_kind,
        "edited": view.edited,
        "untrusted": untrusted,
    }


def _member(row: tuple[int, str, str | None], observed: str) -> dict[str, Any]:
    user_id, role, name = row
    clamped, _cut = clamp(name, NAME_MAX)
    return {
        "source": PROVENANCE,
        "observed_at": observed,
        "user_id": user_id,
        "role": role,
        "untrusted": {"name": clamped} if clamped else {},
    }
