"""WhatsApp templates: the claim-time catalogue (C23) and the template operations (C24; A23).

The Graph API gives templates no numeric version, so ``schema_version`` is a stable digest of
the template's components: an edited body is another version, and a job frozen against the old
one is skipped, never sent with the new text.

The catalogue:

An in-memory map of ``(name, language) -> (status, schema version)``, replaced whole by the
template sync outside any transaction, so ``still_valid`` stays pure (S3): it reads memory, not
the network or a database. Only an ``APPROVED`` template of the frozen schema version is
available; anything else, or an absent entry, is not.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any

from comms.core.canonical import jcs_dumps
from comms.core.providers.protocols import ProviderResult
from comms.transports.whatsapp.cloud.classify import admin_call
from comms.transports.whatsapp.cloud.http import GraphApi, GraphResponse

__all__ = [
    "TemplateCatalog",
    "TemplateOps",
    "TemplatePage",
    "check_create",
    "check_delete",
    "check_edit",
    "schema_version",
]

_NAME = re.compile(r"\A[a-z0-9_]{1,512}\Z")
_LANGUAGE = re.compile(r"\A[a-z]{2,3}(_[A-Z]{2})?\Z")
_CATEGORIES = frozenset({"MARKETING", "UTILITY", "AUTHENTICATION"})
MAX_PAGE = 100


def schema_version(components: Sequence[Mapping[str, Any]]) -> int:
    """A template body's version: the leading 48 bits of SHA-256 over its canonical form."""
    digest = hashlib.sha256(jcs_dumps([dict(c) for c in components])).hexdigest()
    return int(digest[:12], 16) or 1


class TemplateCatalog:
    def __init__(self) -> None:
        self._entries: Mapping[tuple[str, str], tuple[str, int]] = MappingProxyType({})

    def replace(self, entries: Mapping[tuple[str, str], tuple[str, int]]) -> None:
        self._entries = MappingProxyType(dict(entries))

    def available(self, name: str, language: str, schema_version: int) -> bool:
        return self._entries.get((name, language)) == ("APPROVED", schema_version)


@dataclass(frozen=True)
class TemplatePage:
    items: tuple[Mapping[str, Any], ...]
    next_cursor: str | None


def check_create(body: Mapping[str, Any]) -> None:
    """A template definition, checked without a call (D17: the executor validates first)."""
    if (
        set(body) != {"name", "language", "category", "components"}
        or not (isinstance(body["name"], str) and _NAME.match(body["name"]))
        or not (isinstance(body["language"], str) and _LANGUAGE.match(body["language"]))
        or body["category"] not in _CATEGORIES
        or not isinstance(body["components"], list)
    ):
        raise ValueError("template definition refused")


def check_edit(template_id: object, body: Mapping[str, Any]) -> None:
    if not isinstance(template_id, str) or not template_id.isdigit():
        raise ValueError("template id refused")
    if set(body) != {"components"} or not isinstance(body["components"], list):
        raise ValueError("template edit refused")


def check_delete(name: object) -> None:
    if not isinstance(name, str) or not _NAME.match(name):
        raise ValueError("template name refused")


class TemplateOps:
    """``template.list/get/create/edit/delete``: one Graph call each, never retried here.

    Create is ``CREATE`` (resolve-only; the ref is the new template id, and a success without
    one is ``OUTCOME_UNKNOWN``); edit sets a state; delete is destructive and resolve-only.
    """

    def __init__(self, api: GraphApi) -> None:
        self._api = api

    def list(self, *, limit: int = 50, cursor: str | None = None) -> TemplatePage:
        if type(limit) is not int or not 1 <= limit <= MAX_PAGE:
            raise ValueError("template page size is 1..100")
        return _page(self._api.list_templates(limit=limit, after=cursor))

    def get(self, name: str, language: str) -> Mapping[str, Any] | None:
        page = _page(self._api.list_templates(limit=MAX_PAGE, name=name))
        return next((t for t in page.items if t["language"] == language), None)

    def refresh(self, catalog: TemplateCatalog) -> int:
        """Replace the catalogue with every template's status and version; returns the count."""
        entries: dict[tuple[str, str], tuple[str, int]] = {}
        cursor = None
        while True:
            page = self.list(limit=MAX_PAGE, cursor=cursor)
            for t in page.items:
                entries[(t["name"], t["language"])] = (t["status"], t["schema_version"])
            if page.next_cursor is None:
                break
            cursor = page.next_cursor
        catalog.replace(entries)
        return len(entries)

    def create(self, body: Mapping[str, Any]) -> ProviderResult:
        check_create(body)
        result = admin_call(lambda: self._api.create_template(body))
        if result.outcome != "SUCCEEDED":
            return result
        template_id = result.detail.get("id")
        if not isinstance(template_id, str) or not template_id.isdigit():
            return ProviderResult("OUTCOME_UNKNOWN", None)
        return ProviderResult("SUCCEEDED", None, provider_ref=template_id, detail=result.detail)

    def edit(self, template_id: str, body: Mapping[str, Any]) -> ProviderResult:
        check_edit(template_id, body)
        return admin_call(lambda: self._api.edit_template(template_id, body))

    def delete(self, name: str) -> ProviderResult:
        check_delete(name)
        return admin_call(lambda: self._api.delete_template(name))


def _page(response: GraphResponse) -> TemplatePage:
    envelope = response.envelope or {}
    if response.http_status != 200 or not isinstance(envelope.get("data"), list):
        raise ValueError("template list unavailable")
    items = []
    for t in envelope["data"]:
        components = t.get("components") if isinstance(t, dict) else None
        if not isinstance(components, list) or not all(
            isinstance(t.get(k), str) for k in ("name", "language", "status")
        ):
            continue
        items.append(
            {
                "id": t.get("id"),
                "name": t["name"],
                "language": t["language"],
                "status": t["status"],
                "category": t.get("category"),
                "components": components,
                "schema_version": schema_version(components),
            }
        )
    paging = envelope.get("paging")
    paging = paging if isinstance(paging, dict) else {}
    cursors = paging.get("cursors")
    after = cursors.get("after") if isinstance(cursors, dict) and paging.get("next") else None
    return TemplatePage(tuple(items), after if isinstance(after, str) else None)
