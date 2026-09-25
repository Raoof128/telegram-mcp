"""``retention run`` and ``backup export|import stage|import commit`` (D39-PRE E8b; A15, B25–B28).

A backup crosses the admin socket in 32 KiB chunks (``TransferRegistry``, 4 MiB cap, five
minutes, bound to the admin peer that began it). ``backup export`` returns a pull transfer and
the signature sidecar; the CLI pulls the chunks and writes both files 0600. ``backup import
stage`` completes a pushed transfer and stages it (the age identity is read by the daemon from a
private 0600 file it owns); ``commit`` applies it by the staged handle, from the same peer.

The ``backup transfer`` steps are protocol steps the CLI drives, not user commands.
"""

from __future__ import annotations

import base64
import binascii
from collections.abc import Hashable
from pathlib import Path
from typing import Any

from comms.core.backup.export_import import ImportRefused, commit_import, export, stage_import
from comms.core.backup.transfer import CHUNK, TransferError
from comms.core.maintenance.retention import RetentionFailed, RetentionPolicy, run_retention
from comms.runtime.operator.context import OperatorContext, OperatorHandler
from comms.transports.telegram.ipc.admin import ADMIN_PEER

__all__ = ["MAINTENANCE_HANDLERS", "PROTOCOL_STEPS"]

PROTOCOL_STEPS = frozenset(
    {
        ("backup", "transfer", "pull"),
        ("backup", "transfer", "begin-push"),
        ("backup", "transfer", "push"),
    }
)


def _peer() -> Hashable:
    peer = ADMIN_PEER.get()
    if peer is None:
        raise ValueError("the admin peer is unknown")
    return peer


def _backups(ctx: OperatorContext) -> Any:
    if ctx.transfers is None or ctx.staged is None or ctx.providers is None:
        raise ValueError("backups are managed by the daemon")
    return ctx.transfers


def _b64(text: object) -> bytes:
    if not isinstance(text, str):
        raise ValueError("expected base64 data")  # noqa: TRY004 -- uniform ValueError: the router's refusal
    try:
        return base64.b64decode(text.encode("ascii"), validate=True)
    except (binascii.Error, UnicodeEncodeError):
        raise ValueError("expected base64 data") from None


def _retention(ctx: OperatorContext, args: dict[str, Any]) -> dict[str, Any]:
    legacy = ctx.require_legacy()
    if legacy.retention is None or legacy.retention_days is None or ctx.retention_days is None:
        raise ValueError("retention is run by the daemon")
    days = {**legacy.retention_days(), **ctx.retention_days}
    try:
        report = run_retention(
            ctx.conn,
            legacy.retention(),
            RetentionPolicy(**days),
            ctx.writer,
            now=ctx.clock(),
            store=ctx.store,
        )
    except RetentionFailed as refused:
        raise ValueError(f"retention refused: {refused}") from None
    return {"phases": dict(report.phases), "outcome": report.outcome}


def _export(ctx: OperatorContext, args: dict[str, Any]) -> dict[str, Any]:
    transfers, peer = _backups(ctx), _peer()
    if ctx.backup_recipient is None:
        raise ValueError("set backup_recipient (an age public key) in comms.json first")
    result = export(
        ctx.writer, ctx.store, ctx.backup_recipient, dict(ctx.providers or {}), now=ctx.clock()
    )
    try:
        pull = transfers.begin_pull(peer, result.ciphertext)
    except TransferError as refused:
        raise ValueError(str(refused)) from None
    return {
        "transfer": pull.transfer_id,
        "size": pull.size,
        "sha256": pull.sha256,
        "chunks": max(1, -(-pull.size // CHUNK)),
        "sidecar": base64.b64encode(result.sidecar).decode("ascii"),
        "binding": result.binding,
        "signer_key_id": result.signer_key_id,
    }


def _pull(ctx: OperatorContext, args: dict[str, Any]) -> dict[str, Any]:
    transfers, peer = _backups(ctx), _peer()
    try:
        chunk = transfers.pull(str(args.get("transfer")), peer, int(args.get("index", -1)))
    except TransferError as refused:
        raise ValueError(str(refused)) from None
    return {"chunk": base64.b64encode(chunk).decode("ascii")}


def _begin_push(ctx: OperatorContext, args: dict[str, Any]) -> dict[str, Any]:
    transfers, peer = _backups(ctx), _peer()
    try:
        transfer = transfers.begin_push(peer, size=args.get("size"), sha256=str(args.get("sha256")))
    except TransferError as refused:
        raise ValueError(str(refused)) from None
    return {"transfer": transfer}


def _push(ctx: OperatorContext, args: dict[str, Any]) -> dict[str, Any]:
    transfers, peer = _backups(ctx), _peer()
    try:
        transfers.push(
            str(args.get("transfer")), peer, int(args.get("index", -1)), _b64(args.get("chunk"))
        )
    except TransferError as refused:
        raise ValueError(str(refused)) from None
    return {"pushed": int(args.get("index", -1))}


def _stage(ctx: OperatorContext, args: dict[str, Any]) -> dict[str, Any]:
    transfers, peer = _backups(ctx), _peer()
    identity = args.get("identity")
    if not isinstance(identity, str):
        raise ValueError("stage needs --identity, a private 0600 file")  # noqa: TRY004 -- uniform ValueError
    try:
        ciphertext = transfers.complete(str(args.get("transfer")), peer)
        staged = stage_import(
            ctx.conn,
            ctx.staged,
            peer,
            ciphertext,
            _b64(args.get("sidecar")),
            Path(identity),
            providers=dict(ctx.providers or {}),
            trust_key=args.get("trust_key"),
            adopt=bool(args.get("adopt")),
            now=ctx.clock(),
        )
    except (TransferError, ImportRefused) as refused:
        raise ValueError(str(refused)) from None
    return {
        "handle": staged.handle,
        "diff": {k: dict(v) for k, v in staged.diff.items()},
        "incompatibilities": list(staged.incompatibilities),
        "adopt": list(staged.adopt) if staged.adopt else None,
    }


def _commit(ctx: OperatorContext, args: dict[str, Any]) -> dict[str, Any]:
    _backups(ctx)
    try:
        diff = commit_import(
            ctx.writer, ctx.store, ctx.staged, str(args.get("handle")), _peer(), now=ctx.clock()
        )
    except ImportRefused as refused:
        raise ValueError(str(refused)) from None
    return {"committed": True, "diff": {k: dict(v) for k, v in diff.items()}}


MAINTENANCE_HANDLERS: dict[tuple[str, ...], OperatorHandler] = {
    ("retention", "run"): _retention,
    ("backup", "export"): _export,
    ("backup", "import", "stage"): _stage,
    ("backup", "import", "commit"): _commit,
    ("backup", "transfer", "pull"): _pull,
    ("backup", "transfer", "begin-push"): _begin_push,
    ("backup", "transfer", "push"): _push,
}
