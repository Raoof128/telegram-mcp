"""The Bot API context source (comms v0.3 Task C13; P §19–21; A32).

A bot sees only what Telegram gives it now and what this installation retained as it arrived
(P §20). ``info`` reads current provider data (chat, administrators, member count) and is
``telegram_live``; ``recent`` pages the locally retained updates for the chat and is
``telegram_local``. History, search and member enumeration are ``PROVIDER_UNSUPPORTED`` and
never reach Telegram: the bot never presents local retention as full history. Every item
carries its ``source`` and ``observed_at`` (P §21); provider text sits under ``untrusted``
(A32). A failed live lookup refuses the whole read rather than returning part of it.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from datetime import datetime
from typing import Any

from comms.core import timeutil
from comms.core.delivery.transport import ResultKind
from comms.core.providers.protocols import ContextPage, ContextQuery
from comms.transports.telegram.bot.args import positive_int, take
from comms.transports.telegram.bot.classify import LookupFailed, lookup
from comms.transports.telegram.bot.http import BotApi

__all__ = ["BotContext", "ContextRefused"]

ACTOR = "telegram_bot"
MAX_LIMIT = 100


class ContextRefused(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(f"context read refused ({code})")
        self.code = code


_LOOKUP_CODES = {ResultKind.FAILED_PERMANENT: "NOT_AUTHORIZED"}


class BotContext:
    def __init__(self, api: BotApi, conn: Any, *, clock: Callable[[], datetime]) -> None:
        self._api, self._conn, self._clock = api, conn, clock

    def __repr__(self) -> str:
        return "BotContext(<redacted>)"

    def read(self, query: ContextQuery) -> ContextPage:
        if query.target.actor != ACTOR:
            raise ValueError("not a telegram_bot destination")
        chat_id = int(query.target.identity)
        if query.kind == "info":
            take(query.args, {}, {})
            return self._info(chat_id)
        if query.kind == "recent":
            args = take(query.args, {}, {"limit": _limit, "cursor": _cursor})
            return self._recent(chat_id, args.get("limit", 20), args.get("cursor"))
        raise ContextRefused("PROVIDER_UNSUPPORTED")

    def _info(self, chat_id: int) -> ContextPage:
        try:
            chat = lookup(self._api, "getChat", {"chat_id": chat_id})
            admins = lookup(self._api, "getChatAdministrators", {"chat_id": chat_id})
            count = lookup(self._api, "getChatMemberCount", {"chat_id": chat_id})
        except LookupFailed as failed:
            raise ContextRefused(_LOOKUP_CODES.get(failed.kind, "UNAVAILABLE")) from None
        if not isinstance(chat, dict) or not isinstance(admins, list) or type(count) is not int:
            raise ContextRefused("UNAVAILABLE")
        stamp = {"source": "telegram_live", "observed_at": timeutil.iso(self._clock())}
        title = chat.get("title") or chat.get("first_name")
        items: list[Mapping[str, Any]] = [
            {
                **stamp,
                "item": "chat",
                "chat_id": chat_id,
                "type": chat.get("type"),
                "is_forum": chat.get("is_forum") is True,
                "member_count": count,
                "untrusted": {"title": title} if isinstance(title, str) else {},
            }
        ]
        for admin in admins:
            user = admin.get("user") if isinstance(admin, dict) else None
            if isinstance(user, dict) and type(user.get("id")) is int:
                items.append(
                    {
                        **stamp,
                        "item": "admin",
                        "user_id": user["id"],
                        "status": admin.get("status"),
                        "is_bot": user.get("is_bot") is True,
                        "untrusted": {"name": user.get("first_name")},
                    }
                )
        return ContextPage(tuple(items), "telegram_live")

    def _recent(self, chat_id: int, limit: int, cursor: str | None) -> ContextPage:
        before = int(cursor) if cursor is not None else None
        rows = self._conn.execute(
            "SELECT update_id, kind, payload, received_at FROM bot_updates WHERE chat_id = ?"
            " AND (? IS NULL OR update_id < ?) ORDER BY update_id DESC LIMIT ?",
            (chat_id, before, before, limit + 1),
        ).fetchall()
        items = tuple(_local_item(*row) for row in rows[:limit])
        next_cursor = str(rows[limit - 1][0]) if len(rows) > limit else None
        return ContextPage(items, "telegram_local", next_cursor)


def _limit(value: object) -> bool:
    return type(value) is int and 1 <= value <= MAX_LIMIT


def _cursor(value: object) -> bool:
    return (
        isinstance(value, str) and value.isdigit() and value.isascii() and positive_int(int(value))
    )


def _local_item(update_id: int, kind: str, payload: str, received_at: str) -> Mapping[str, Any]:
    body = json.loads(payload).get(kind)
    body = body if isinstance(body, dict) else {}
    sender = body.get("from")
    sender = sender if isinstance(sender, dict) else {}
    text = body.get("text") if isinstance(body.get("text"), str) else body.get("caption")
    return {
        "source": "telegram_local",
        "observed_at": received_at,
        "update_id": update_id,
        "kind": kind,
        "message_id": body.get("message_id"),
        "date": body.get("date"),
        "from_id": sender.get("id"),
        "untrusted": {"text": text} if isinstance(text, str) else {},
    }
