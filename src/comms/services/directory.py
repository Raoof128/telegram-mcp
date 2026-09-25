"""Location and audience services over the 5b-4 directory (comms v0.3 Task D16; P §31, G16).

Every write runs through ``MutationExecutor.local`` with the caller's ``req_`` id (the core's
``*_in_tx`` bodies), so a replay returns the stored result and creates nothing twice. An
audience that would contain itself is refused before anything is recorded. ``audience.resolve``
returns counts and endpoint refs — never a delivery identity.
"""

from __future__ import annotations

from typing import Any

from comms.core.audit.writer import AuditWriter
from comms.core.campaigns import directory as d
from comms.core.campaigns import directory_views as views
from comms.core.campaigns.drafts import TRANSPORTS
from comms.core.campaigns.resolve import resolve_targets
from comms.core.errors import CommsError
from comms.services.local import effect, mapped, next_cursor, page_args, run_local
from comms.services.mutations import CallContext, MutationExecutor

__all__ = ["RESOLVE_ENDPOINTS_MAX", "DirectoryService"]

RESOLVE_ENDPOINTS_MAX = 500
_NAME_MAX = 200


def _name(name: object) -> str:
    if not isinstance(name, str) or not name.strip() or len(name) > _NAME_MAX:
        raise CommsError("INVALID_ARGUMENT")
    return name


class DirectoryService:
    def __init__(self, writer: AuditWriter, executor: MutationExecutor) -> None:
        self._writer, self._executor = writer, executor

    # -- locations ------------------------------------------------------------------------

    def location_create(self, ctx: CallContext, name: str, request_id: str) -> dict[str, Any]:
        name = _name(name)
        return run_local(
            self._executor,
            ctx,
            "comms_location_create",
            {},
            {"name": name},
            request_id,
            lambda tx: {"location": d.add_location_in_tx(tx.conn, name, now=tx.now)},
        )

    def location_update(
        self, ctx: CallContext, location: str, name: str, request_id: str
    ) -> dict[str, Any]:
        return self._rename(ctx, "comms_location_update", "location", location, name, request_id)

    def location_enable(self, ctx: CallContext, location: str, request_id: str) -> dict[str, Any]:
        return self._enabled(ctx, "comms_location_enable", location, True, request_id)

    def location_disable(self, ctx: CallContext, location: str, request_id: str) -> dict[str, Any]:
        return self._enabled(ctx, "comms_location_disable", location, False, request_id)

    def location_get(self, location: str) -> dict[str, Any]:
        return mapped(lambda: views.location_view(self._writer.conn, location))  # type: ignore[no-any-return]

    def location_list(self, *, limit: int = 50, cursor: str | None = None) -> dict[str, Any]:
        size, before = page_args(limit, cursor)
        items, more = views.list_locations(self._writer.conn, limit=size, before=before)
        items = [{**i, "enabled": bool(i["enabled"])} for i in items]
        return {"items": items, "next_cursor": next_cursor(more)}

    # -- audiences ------------------------------------------------------------------------

    def audience_create(self, ctx: CallContext, name: str, request_id: str) -> dict[str, Any]:
        name = _name(name)
        return run_local(
            self._executor,
            ctx,
            "comms_audience_create",
            {},
            {"name": name},
            request_id,
            lambda tx: {"audience": d.add_audience_in_tx(tx.conn, name, now=tx.now)},
        )

    def audience_update(
        self, ctx: CallContext, audience: str, name: str, request_id: str
    ) -> dict[str, Any]:
        return self._rename(ctx, "comms_audience_update", "audience", audience, name, request_id)

    def audience_add(
        self, ctx: CallContext, audience: str, member: str, request_id: str
    ) -> dict[str, Any]:
        return run_local(
            self._executor,
            ctx,
            "comms_audience_add",
            {"audience": audience, "member": member},
            {},
            request_id,
            effect(lambda tx: d.add_audience_member_in_tx(tx.conn, audience, member)),
        )

    def audience_remove(
        self, ctx: CallContext, audience: str, member: str, request_id: str
    ) -> dict[str, Any]:
        return run_local(
            self._executor,
            ctx,
            "comms_audience_remove",
            {"audience": audience, "member": member},
            {},
            request_id,
            effect(lambda tx: d.remove_audience_member_in_tx(tx.conn, audience, member)),
        )

    def audience_get(self, audience: str) -> dict[str, Any]:
        return mapped(lambda: views.audience_view(self._writer.conn, audience))  # type: ignore[no-any-return]

    def audience_list(self, *, limit: int = 50, cursor: str | None = None) -> dict[str, Any]:
        size, before = page_args(limit, cursor)
        items, more = views.list_audiences(self._writer.conn, limit=size, before=before)
        return {"items": items, "next_cursor": next_cursor(more)}

    def audience_resolve(self, audience: str) -> dict[str, Any]:
        """Who the audience reaches today, as counts and endpoint refs."""

        def resolve() -> dict[str, Any]:
            views.audience_view(self._writer.conn, audience)  # NOT_FOUND before resolving
            candidates = resolve_targets(self._writer.conn, {"audiences": [audience]}, TRANSPORTS)
            by_transport: dict[str, int] = {}
            endpoints: set[str] = set()
            for candidate in candidates:
                by_transport[candidate.transport] = by_transport.get(candidate.transport, 0) + 1
                endpoints.update(candidate.endpoint_refs)
            listed = sorted(endpoints)
            return {
                "audience": audience,
                "count": len(candidates),
                "by_transport": by_transport,
                "endpoints": listed[:RESOLVE_ENDPOINTS_MAX],
                "truncated": len(listed) > RESOLVE_ENDPOINTS_MAX,
            }

        return mapped(resolve)  # type: ignore[no-any-return]

    # -- shared ---------------------------------------------------------------------------

    def _rename(
        self, ctx: CallContext, tool: str, kind: str, ref: str, name: str, request_id: str
    ) -> dict[str, Any]:
        name = _name(name)
        return run_local(
            self._executor,
            ctx,
            tool,
            {kind: ref},
            {"name": name},
            request_id,
            effect(lambda tx: d.rename_in_tx(tx.conn, ref, name)),
        )

    def _enabled(
        self, ctx: CallContext, tool: str, location: str, enabled: bool, request_id: str
    ) -> dict[str, Any]:
        if not isinstance(location, str) or not location.startswith("loc_"):
            raise CommsError("INVALID_ARGUMENT")  # these tools act on locations only
        return run_local(
            self._executor,
            ctx,
            tool,
            {"location": location},
            {},
            request_id,
            effect(lambda tx: d.set_enabled_in_tx(tx.conn, location, enabled, now=tx.now)),
        )
