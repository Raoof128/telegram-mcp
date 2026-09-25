"""Campaign drafts: create, edit content and targets, validate (design §6.1, D5).

Content is editable only in ``DRAFT``; ``READY`` returns to ``DRAFT`` to edit. Content
and targets are stored as TG-JCS-v1 text. Media are descriptors only.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any

from comms.core import refs, timeutil
from comms.core.campaigns.events import append_event
from comms.core.campaigns.resolve import Targets, check_targets
from comms.core.canonical import jcs_dumps
from comms.core.storage.db import require_tx, write_tx

__all__ = [
    "TRANSPORTS",
    "LifecycleError",
    "NotFound",
    "create_campaign",
    "create_campaign_in_tx",
    "edit",
    "edit_in_tx",
    "load",
    "set_content",
    "set_content_in_tx",
    "set_targets",
    "set_targets_in_tx",
    "validate",
    "validate_in_tx",
]

TRANSPORTS = frozenset({"telegram", "whatsapp"})
_TARGET_KEYS = ("audiences", "locations", "destinations", "recipients")
_HEX64 = re.compile(r"[0-9a-f]{64}\Z")


class LifecycleError(ValueError):
    """A campaign operation is not allowed in its current state. Messages are fixed."""


class NotFound(LifecycleError):
    """The campaign or job ref names nothing (a fixed message, never the ref)."""


def load(conn: Any, cmp: str) -> dict[str, Any]:
    """The campaign row as a dict (inside or outside a transaction); fixed error if unknown."""
    try:
        refs.check(cmp, "campaign")
    except ValueError:
        raise NotFound("unknown campaign") from None
    row = conn.execute(
        "SELECT id, ref, lifecycle, content, targets, options, summary, current_generation_id"
        " FROM campaigns WHERE ref = ?",
        (cmp,),
    ).fetchone()
    if row is None:
        raise NotFound("unknown campaign")
    keys = ("id", "ref", "lifecycle", "content", "targets", "options", "summary", "generation_id")
    return dict(zip(keys, row, strict=True))


def _require(campaign: Mapping[str, Any], lifecycle: str, message: str) -> None:
    if campaign["lifecycle"] != lifecycle:
        raise LifecycleError(message)


def _set(conn: Any, campaign_id: int, column: str, value: Any, stamp: str) -> None:
    conn.execute(
        f"UPDATE campaigns SET {column} = ?, updated_at = ? WHERE id = ?",
        (value, stamp, campaign_id),
    )


def create_campaign_in_tx(conn: Any, title: str, *, now: datetime) -> str:
    require_tx(conn)
    ref, stamp = refs.mint("campaign"), timeutil.iso(now)
    empty_targets = jcs_dumps({k: [] for k in _TARGET_KEYS}).decode()
    conn.execute(
        "INSERT INTO campaigns (ref, title, lifecycle, content, targets, options, created_at,"
        " updated_at) VALUES (?, ?, 'DRAFT', '{}', ?, ?, ?, ?)",
        (ref, str(title), empty_targets, jcs_dumps({"transports": []}).decode(), stamp, stamp),
    )
    append_event(conn, "campaign.created", ref, {"lifecycle": "DRAFT"}, now=now)
    return ref


def create_campaign(conn: Any, title: str, *, now: datetime) -> str:
    with write_tx(conn):
        return create_campaign_in_tx(conn, title, now=now)


def _text(value: Any) -> str:
    if not isinstance(value, str):
        raise ValueError("content text must be a string")  # noqa: TRY004 -- uniform ValueError
    return value


def _media(items: Any) -> list[dict[str, Any]]:
    if not isinstance(items, Sequence) or isinstance(items, (str, bytes)):
        raise ValueError("media must be a list of descriptors")  # noqa: TRY004 -- uniform ValueError
    out = []
    for item in items:
        if not isinstance(item, Mapping) or set(item) != {"sha256", "mime", "name", "size"}:
            raise ValueError("media descriptor refused")
        if not (isinstance(item["sha256"], str) and _HEX64.fullmatch(item["sha256"])):
            raise ValueError("media descriptor refused")
        if not (isinstance(item["mime"], str) and isinstance(item["name"], str)):
            raise ValueError("media descriptor refused")  # noqa: TRY004 -- uniform ValueError
        if type(item["size"]) is not int or item["size"] < 0:
            raise ValueError("media descriptor refused")
        out.append(dict(item))
    return out


def set_content_in_tx(
    conn: Any,
    cmp: str,
    *,
    canonical: str | None = None,
    fa: str | None = None,
    en: str | None = None,
    links: Sequence[str] | None = None,
    media: Sequence[Mapping[str, Any]] | None = None,
    now: datetime,
) -> None:
    require_tx(conn)
    changes: dict[str, Any] = {}
    for key, value in (("canonical", canonical), ("fa", fa), ("en", en)):
        if value is not None:
            changes[key] = _text(value)
    if links is not None:
        if isinstance(links, (str, bytes)) or not all(isinstance(x, str) for x in links):
            raise ValueError("links must be a list of strings")
        changes["links"] = list(links)
    if media is not None:
        changes["media"] = _media(media)
    stamp = timeutil.iso(now)
    campaign = load(conn, cmp)
    _require(campaign, "DRAFT", "campaign is not a draft")
    content = {**json.loads(campaign["content"]), **changes}
    _set(conn, campaign["id"], "content", jcs_dumps(content).decode(), stamp)
    append_event(conn, "campaign.modified", cmp, {"lifecycle": "DRAFT"}, now=now)


def set_content(
    conn: Any,
    cmp: str,
    *,
    canonical: str | None = None,
    fa: str | None = None,
    en: str | None = None,
    links: Sequence[str] | None = None,
    media: Sequence[Mapping[str, Any]] | None = None,
    now: datetime,
) -> None:
    with write_tx(conn):
        set_content_in_tx(
            conn, cmp, canonical=canonical, fa=fa, en=en, links=links, media=media, now=now
        )


def set_targets_in_tx(
    conn: Any, cmp: str, targets: Targets, transports: frozenset[str], *, now: datetime
) -> None:
    require_tx(conn)
    if not isinstance(targets, Mapping) or not set(targets) <= set(_TARGET_KEYS):
        raise ValueError("unknown target kind")
    shaped: dict[str, list[str]] = {}
    for key in _TARGET_KEYS:
        values = targets.get(key, [])
        if isinstance(values, (str, bytes)) or not all(isinstance(v, str) for v in values):
            raise ValueError("targets must be lists of refs")
        shaped[key] = sorted(set(values))
    if not set(transports) <= TRANSPORTS:
        raise ValueError("unknown transport")
    stamp = timeutil.iso(now)
    campaign = load(conn, cmp)
    _require(campaign, "DRAFT", "campaign is not a draft")
    _set(conn, campaign["id"], "targets", jcs_dumps(shaped).decode(), stamp)
    options = {**json.loads(campaign["options"]), "transports": sorted(transports)}
    _set(conn, campaign["id"], "options", jcs_dumps(options).decode(), stamp)
    append_event(conn, "campaign.modified", cmp, {"lifecycle": "DRAFT"}, now=now)


def set_targets(
    conn: Any, cmp: str, targets: Targets, transports: frozenset[str], *, now: datetime
) -> None:
    with write_tx(conn):
        set_targets_in_tx(conn, cmp, targets, transports, now=now)


def targets_of(campaign: Mapping[str, Any]) -> tuple[dict[str, list[str]], frozenset[str]]:
    return json.loads(campaign["targets"]), frozenset(json.loads(campaign["options"])["transports"])


def validate_in_tx(conn: Any, cmp: str, *, now: datetime) -> None:
    """DRAFT → READY: a body, at least one transport and one target, every target known."""
    require_tx(conn)
    stamp = timeutil.iso(now)
    campaign = load(conn, cmp)
    _require(campaign, "DRAFT", "campaign is not a draft")
    content = json.loads(campaign["content"])
    targets, transports = targets_of(campaign)
    has_body = any(content.get(k) for k in ("canonical", "fa", "en"))
    if not has_body or not transports or not any(targets.values()):
        raise LifecycleError("campaign is incomplete")
    check_targets(conn, targets)
    _set(conn, campaign["id"], "lifecycle", "READY", stamp)
    append_event(conn, "campaign.validated", cmp, {"lifecycle": "READY"}, now=now)


def validate(conn: Any, cmp: str, *, now: datetime) -> None:
    """DRAFT → READY: a body, at least one transport and one target, every target known."""
    with write_tx(conn):
        validate_in_tx(conn, cmp, now=now)


def edit_in_tx(conn: Any, cmp: str, *, now: datetime) -> None:
    """READY → DRAFT."""
    require_tx(conn)
    stamp = timeutil.iso(now)
    campaign = load(conn, cmp)
    _require(campaign, "READY", "campaign is not ready")
    _set(conn, campaign["id"], "lifecycle", "DRAFT", stamp)
    append_event(conn, "campaign.modified", cmp, {"lifecycle": "DRAFT"}, now=now)


def edit(conn: Any, cmp: str, *, now: datetime) -> None:
    """READY → DRAFT."""
    with write_tx(conn):
        edit_in_tx(conn, cmp, now=now)
