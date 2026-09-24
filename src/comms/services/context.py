"""The context engine (comms v0.3 Task D8; P §19–22, §71; A32).

It reads through the per-actor context sources and returns pages whose every item carries its
``source`` and ``observed_at`` (P §21) and refs, never a provider identity: a provider message
id becomes a durable ``cmg_`` ref (D1b), the group is named by its ``grp_`` ref, and chat,
sender and update ids are dropped. Provider text is moved into ``untrusted_text`` (A32).
``search`` is held to P §71's bounds — results, groups examined, provider requests, messages
examined, elapsed time — and names the bound that stopped it; it never spans actors or accounts
unless ``across_accounts`` is set.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from datetime import datetime
from types import MappingProxyType
from typing import Any

from comms.core.campaigns.directory import destination_id
from comms.core.errors import CommsError
from comms.core.objects import object_ref
from comms.core.providers.capability import Capability, CapabilityState
from comms.core.providers.protocols import (
    ContextPage,
    ContextQuery,
    ContextRefused,
    ContextSource,
    ProviderTarget,
)
from comms.services.capability import STATE_CODE, CapabilityService

__all__ = ["INCLUDES", "SEARCH_BOUNDS", "ContextEngine"]

SEARCH_BOUNDS = MappingProxyType(
    {"results": 50, "groups": 10, "requests": 20, "messages": 2000, "seconds": 20.0}
)
INCLUDES = frozenset(
    {"messages", "members", "admins", "topics", "capabilities", "linked_audiences", "campaigns"}
)
_SERVED = frozenset({"messages", "members", "admins"})  # the rest arrive with D10, D11, D15
_IDENTITIES = frozenset({"message_id", "sender_id", "from_id", "chat_id", "update_id", "user_id"})
_MAX_PAGE = 100


class ContextEngine:
    def __init__(
        self,
        conn: Any,
        sources: Mapping[str, ContextSource],
        *,
        clock: Callable[[], datetime],
        monotonic: Callable[[], float],
        capability: CapabilityService | None = None,
    ) -> None:
        self._conn, self._sources, self._clock = conn, dict(sources), clock
        self._monotonic, self._capability = monotonic, capability

    # -- Telegram source choice (D10; P §12, §20) ------------------------------------------

    def recent_for(
        self,
        group: str,
        targets: Mapping[str, ProviderTarget],
        *,
        limit: int = 20,
        cursor: str | None = None,
    ) -> dict[str, Any]:
        """Live provider history when the user account can read the group, else the bot's
        locally retained updates (labelled ``telegram_local``)."""
        target = self._reader(targets, Capability.HISTORY_READ, fallback=True)
        return self.recent(group, target, limit=limit, cursor=cursor)

    def search_for(
        self,
        groups: Sequence[tuple[str, Mapping[str, ProviderTarget]]],
        query: str,
        *,
        limit: int = 20,
    ) -> dict[str, Any]:
        """Search is provider history: the user actor only, never quietly the bot's updates."""
        chosen = [
            (group, self._reader(targets, Capability.HISTORY_SEARCH, fallback=False))
            for group, targets in groups
        ]
        return self.search(chosen, query, limit=limit)

    def _reader(
        self, targets: Mapping[str, ProviderTarget], capability: Capability, *, fallback: bool
    ) -> ProviderTarget:
        user = targets.get("telegram_user")
        code = "NOT_CONFIGURED"
        if user is not None and "telegram_user" in self._sources and self._capability is not None:
            state = self._capability.state("telegram_user", user, capability)
            if state is CapabilityState.AVAILABLE:
                return user
            code = STATE_CODE.get(state, "CAPABILITY_UNAVAILABLE")
        bot = targets.get("telegram_bot")
        if fallback and bot is not None and "telegram_bot" in self._sources:
            return bot
        raise CommsError(code)

    def recent(
        self, group: str, target: ProviderTarget, *, limit: int = 20, cursor: str | None = None
    ) -> dict[str, Any]:
        return self._page(group, target, "recent", {"limit": _limit(limit), **_cursor(cursor)})

    def around_message(
        self,
        group: str,
        target: ProviderTarget,
        message_id: int,
        *,
        before: int = 10,
        after: int = 10,
    ) -> dict[str, Any]:
        return self._page(
            group, target, "around", {"message_id": message_id, "before": before, "after": after}
        )

    def thread(
        self, group: str, target: ProviderTarget, message_id: int, *, limit: int = 20
    ) -> dict[str, Any]:
        return self._page(
            group, target, "thread", {"message_id": message_id, "limit": _limit(limit)}
        )

    def get(
        self,
        group: str,
        target: ProviderTarget,
        *,
        include: Sequence[str] = ("messages",),
        message_limit: int = 20,
    ) -> dict[str, Any]:
        wanted = set(include)
        if not wanted or not wanted <= INCLUDES:
            raise CommsError("INVALID_ARGUMENT")
        if not wanted <= _SERVED:
            raise CommsError("PROVIDER_UNSUPPORTED")
        result: dict[str, Any] = {"group_ref": group}
        if "messages" in wanted:
            result["messages"] = self.recent(group, target, limit=message_limit)
        if wanted & {"members", "admins"}:
            members = self._page(group, target, "members", {"limit": _MAX_PAGE})
            if "members" in wanted:
                result["members"] = members
            if "admins" in wanted:
                admins = [i for i in members["items"] if i.get("role") in ("creator", "admin")]
                result["admins"] = {**members, "items": admins, "next_cursor": None}
        return result

    def search(
        self,
        groups: Sequence[tuple[str, ProviderTarget]],
        query: str,
        *,
        limit: int = 20,
        across_accounts: bool = False,
    ) -> dict[str, Any]:
        if not isinstance(query, str) or not query.strip() or len(query) > 256:
            raise CommsError("INVALID_ARGUMENT")
        if type(limit) is not int or not 1 <= limit <= SEARCH_BOUNDS["results"]:
            raise CommsError("INVALID_ARGUMENT")
        if not groups or len(groups) > SEARCH_BOUNDS["groups"]:
            raise CommsError("INVALID_ARGUMENT")
        if not across_accounts and len({(t.transport, t.actor) for _g, t in groups}) > 1:
            raise CommsError("INVALID_ARGUMENT")  # P §71: never implicitly across accounts
        started = self._monotonic()
        items: list[dict[str, Any]] = []
        requests = examined = 0
        stopped_by = None
        for group, target in groups:
            cursor = None
            while stopped_by is None:
                if requests >= SEARCH_BOUNDS["requests"]:
                    stopped_by = "requests"
                elif self._monotonic() - started >= SEARCH_BOUNDS["seconds"]:
                    stopped_by = "seconds"
                elif examined >= SEARCH_BOUNDS["messages"]:
                    stopped_by = "messages"
                if stopped_by is not None:
                    break
                args = {
                    "query": query,
                    "limit": min(_MAX_PAGE, limit - len(items)),
                    **_cursor(cursor),
                }
                page = self._read(target, "search", args)
                requests += 1
                examined += len(page.items)
                items += [self._item(group, target, item) for item in page.items]
                if len(items) >= limit:
                    items, stopped_by = items[:limit], "results"
                cursor = page.next_cursor
                if cursor is None:
                    break
            if stopped_by is not None:
                break
        return {
            "items": items,
            "stopped_by": stopped_by,
            "requests": requests,
            "groups": len(groups),
        }

    # -- shared ---------------------------------------------------------------------------

    def _page(
        self, group: str, target: ProviderTarget, kind: str, args: Mapping[str, Any]
    ) -> dict[str, Any]:
        page = self._read(target, kind, args)
        return {
            "group_ref": group,
            "source": page.provenance,
            "items": [self._item(group, target, item) for item in page.items],
            "next_cursor": page.next_cursor,
        }

    def _read(self, target: ProviderTarget, kind: str, args: Mapping[str, Any]) -> ContextPage:
        source = self._sources.get(target.actor)
        if source is None:
            raise CommsError("NOT_CONFIGURED")
        try:
            return source.read(ContextQuery(target, kind, dict(args)))
        except ContextRefused as refused:
            raise CommsError(_REFUSALS.get(refused.code, "PROVIDER_UNAVAILABLE")) from None
        except ValueError:
            raise CommsError("INVALID_ARGUMENT") from None

    def _item(self, group: str, target: ProviderTarget, item: Mapping[str, Any]) -> dict[str, Any]:
        out = {k: v for k, v in item.items() if k not in _IDENTITIES and k != "untrusted"}
        untrusted = dict(item.get("untrusted") or {})
        out["untrusted_text"] = untrusted.pop("text", None)
        out["untrusted"] = untrusted
        out["group_ref"] = group
        if item.get("message_id") is not None:
            out["message_ref"] = object_ref(
                self._conn,
                "message",
                target.transport,
                target.actor,
                destination_id(self._conn, target.destination_ref),
                f"{target.identity}:{item['message_id']}",
                now=self._clock(),
            )
        return out


# A source's refusal code → the service error (P §54).
_REFUSALS = MappingProxyType(
    {
        "PROVIDER_UNSUPPORTED": "PROVIDER_UNSUPPORTED",
        "NOT_AUTHORIZED": "NOT_AUTHORIZED",
        "TARGET_NOT_FOUND": "NOT_FOUND",
        "SESSION_REVOKED": "ACCOUNT_INELIGIBLE",
        "ACCOUNT_UNAVAILABLE": "ACCOUNT_INELIGIBLE",
        "AUTH_REQUIRED": "NOT_CONFIGURED",
        "NOT_CONFIGURED": "NOT_CONFIGURED",
    }
)


def _limit(value: object) -> int:
    if type(value) is not int or not 1 <= value <= _MAX_PAGE:
        raise CommsError("INVALID_ARGUMENT")
    return value


def _cursor(cursor: str | None) -> dict[str, str]:
    return {"cursor": cursor} if cursor is not None else {}
