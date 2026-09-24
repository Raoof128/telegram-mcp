"""The reviewed read surface (spec §35) and closed retrieval routing."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from typing import Any, Protocol

__all__ = ["TOOL_METHODS", "RoutedRetrieval", "TelegramReadService"]

TOOL_METHODS: dict[str, str] = {
    "telegram_list_projects": "list_projects",
    "telegram_resolve_project": "resolve_project",
    "telegram_list_chats": "list_chats",
    "telegram_resolve_peer": "resolve_peer",
    "telegram_get_messages": "get_messages",
    "telegram_get_context": "get_context",
    "telegram_search_messages": "search_messages",
    "telegram_cross_project_search": "cross_project_search",
    "telegram_get_unread": "get_unread",
}


class TelegramReadService(Protocol):
    """Every method takes frozen arguments and the authority snapshot."""

    async def list_projects(
        self, arguments: Mapping[str, Any], snapshot: Any
    ) -> dict[str, Any]: ...
    async def resolve_project(
        self, arguments: Mapping[str, Any], snapshot: Any
    ) -> dict[str, Any]: ...
    async def list_chats(self, arguments: Mapping[str, Any], snapshot: Any) -> dict[str, Any]: ...
    async def resolve_peer(self, arguments: Mapping[str, Any], snapshot: Any) -> dict[str, Any]: ...
    async def get_messages(self, arguments: Mapping[str, Any], snapshot: Any) -> dict[str, Any]: ...
    async def get_context(self, arguments: Mapping[str, Any], snapshot: Any) -> dict[str, Any]: ...
    async def search_messages(
        self, arguments: Mapping[str, Any], snapshot: Any
    ) -> dict[str, Any]: ...
    async def cross_project_search(
        self, arguments: Mapping[str, Any], snapshot: Any
    ) -> dict[str, Any]: ...
    async def get_unread(self, arguments: Mapping[str, Any], snapshot: Any) -> dict[str, Any]: ...


Route = Callable[[Mapping[str, Any], Any], Awaitable[dict[str, Any]]]


class RoutedRetrieval:
    """The coordinator's RetrievalAdapter: a closed tool -> bound-method map."""

    def __init__(self, routes: Mapping[str, Route]) -> None:
        unknown = set(routes) - set(TOOL_METHODS)
        if unknown:
            raise ValueError("route for a tool outside the catalogue")
        self._routes = dict(routes)

    async def retrieve(
        self, *, tool_name: str, arguments: Mapping[str, Any], snapshot: Any
    ) -> dict[str, Any]:
        route = self._routes.get(tool_name)
        if route is None:
            raise LookupError("tool is not routed in this phase")
        return await route(arguments, snapshot)
