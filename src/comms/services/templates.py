"""WhatsApp template services (comms v0.3 Task D17; P §32).

Reads come from a template source (``list``, ``get``). Writes are provider writes on the
business account through the one write path: create is a CREATE whose template id becomes a
``ctp_`` ref, edit names the template by that ref, delete names it by its template name (Meta
deletes every language of a name at once). Each is audited and replay-safe.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, Protocol

from comms.core.errors import CommsError
from comms.core.providers.capability import Capability as C
from comms.core.providers.protocols import ProviderTarget
from comms.services.capability import CapabilityService
from comms.services.local import MAX_PAGE
from comms.services.mutations import CallContext, MutationExecutor
from comms.services.writes import ProviderWrites, summary

__all__ = ["TemplateService", "TemplateSource"]

ACTOR = "whatsapp_cloud"
_TEMPLATE = ("template", "template_id")


class TemplatePageLike(Protocol):
    items: Sequence[Mapping[str, Any]]
    next_cursor: str | None


class TemplateSource(Protocol):
    def list(self, *, limit: int = 50, cursor: str | None = None) -> TemplatePageLike: ...

    def get(self, name: str, language: str) -> Mapping[str, Any] | None: ...


def _view(template: Mapping[str, Any]) -> dict[str, Any]:
    keys = ("name", "language", "status", "category", "schema_version", "components")
    return {k: template.get(k) for k in keys}


class TemplateService:
    def __init__(
        self,
        conn: Any,
        capability: CapabilityService,
        executor: MutationExecutor,
        source: TemplateSource,
    ) -> None:
        self._writes, self._source = ProviderWrites(conn, capability, executor), source

    def list(self, *, limit: int = 50, cursor: str | None = None) -> dict[str, Any]:
        if type(limit) is not int or not 1 <= limit <= MAX_PAGE:
            raise CommsError("INVALID_ARGUMENT")
        try:
            page = self._source.list(limit=limit, cursor=cursor)
        except ValueError:
            raise CommsError("PROVIDER_UNAVAILABLE") from None
        return {"items": [_view(t) for t in page.items], "next_cursor": page.next_cursor}

    def get(self, name: str, language: str) -> dict[str, Any]:
        try:
            found = self._source.get(name, language)
        except ValueError:
            raise CommsError("PROVIDER_UNAVAILABLE") from None
        if found is None:
            raise CommsError("NOT_FOUND")
        return _view(found)

    def create(
        self,
        ctx: CallContext,
        account: ProviderTarget,
        definition: Mapping[str, Any],
        request_id: str,
    ) -> dict[str, Any]:
        _chosen, _target, outcome = self._writes.write(
            ctx,
            "whatsapp.template.create",
            {ACTOR: account},
            C.TEMPLATE_CREATE,
            dict(definition),
            request_id,
            ACTOR,
            object_kind="template",
        )
        return {**summary(ACTOR, outcome), "template": outcome.result.get("object_ref")}

    def edit(
        self,
        ctx: CallContext,
        account: ProviderTarget,
        template: str,
        components: Sequence[Mapping[str, Any]],
        request_id: str,
    ) -> dict[str, Any]:
        _chosen, _target, outcome = self._writes.write(
            ctx,
            "whatsapp.template.edit",
            {ACTOR: account},
            C.TEMPLATE_EDIT,
            {"components": [dict(c) for c in components]},
            request_id,
            ACTOR,
            objects={_TEMPLATE: template},
        )
        return {**summary(ACTOR, outcome), "template": template}

    def delete(
        self, ctx: CallContext, account: ProviderTarget, name: str, request_id: str
    ) -> dict[str, Any]:
        _chosen, _target, outcome = self._writes.write(
            ctx,
            "whatsapp.template.delete",
            {ACTOR: account},
            C.TEMPLATE_DELETE,
            {"name": name},
            request_id,
            ACTOR,
        )
        return summary(ACTOR, outcome)
