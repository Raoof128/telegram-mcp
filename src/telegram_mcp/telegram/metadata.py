"""``MetadataReadAdapter``: the two catalogue methods, from gateway metadata.

It reads only the authority snapshot, never the database directly, so what it
can return is exactly what step 2 authorised. It has no access to Telethon.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from telegram_mcp.disclosure.seams import CatalogueSnapshot, normalise

__all__ = ["MetadataReadAdapter"]

_RANK = {
    "exact_slug": 0,
    "exact_display_name": 1,
    "prefix_display_name": 2,
    "substring_display_name": 3,
}


class MetadataReadAdapter:
    def __init__(self, *, mint_cursor: Callable[[Any, Mapping[str, Any], int], str]) -> None:
        self._mint = mint_cursor

    async def list_projects(
        self, arguments: Mapping[str, Any], snapshot: CatalogueSnapshot
    ) -> dict[str, Any]:
        limit = int(arguments["limit"])
        start = snapshot.page * limit
        page = snapshot.visible[start : start + limit]
        out: dict[str, Any] = {"projects": [project.record() for project in page]}
        if start + limit < len(snapshot.visible):
            out["_next_cursor"] = self._mint(snapshot, arguments, snapshot.page + 1)
        return out

    async def resolve_project(
        self, arguments: Mapping[str, Any], snapshot: CatalogueSnapshot
    ) -> dict[str, Any]:
        query = normalise(arguments["query"])
        limit = int(arguments["limit"])
        ranked: list[tuple[int, str, str, dict[str, Any]]] = []
        for project in snapshot.visible:
            name = normalise(project.display_name)
            if project.slug == query:
                kind = "exact_slug"
            elif name == query:
                kind = "exact_display_name"
            elif name.startswith(query):
                kind = "prefix_display_name"
            elif query in name:
                kind = "substring_display_name"
            else:
                continue
            ranked.append(
                (_RANK[kind], name, project.project_ref, dict(project.record(), match_kind=kind))
            )
        if not ranked:
            return {"matches": [], "ambiguous": False}
        best = min(rank for rank, *_ in ranked)
        # Exact kinds form one tier: a slug and a display-name hit on two
        # different projects are both plausible, so both survive.
        tier = {0, 1} if best <= 1 else {best}
        survivors = sorted(entry for entry in ranked if entry[0] in tier)
        matches = [entry[3] for entry in survivors[:limit]]
        return {"matches": matches, "ambiguous": len(survivors) > 1}
