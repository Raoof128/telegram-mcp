"""Operator inspection: receipts, keys, exposure, consent (spec §23A.3, §23C, §33)."""

from __future__ import annotations

import sqlite3
import time
from collections.abc import Callable
from typing import Any

from comms.transports.telegram.disclosure.budget import (
    GLOBAL,
    PROJECT,
    BucketKey,
    committed_usage,
    subject_digest,
    thresholds_for,
    window_start,
)
from comms.transports.telegram.disclosure.keys import (
    current_verification_key,
    lookup_verification_key,
)
from comms.transports.telegram.disclosure.lineage import RestoreLineageLookup
from comms.transports.telegram.disclosure.verify import verify_persisted_receipt
from comms.transports.telegram.ipc.handlers._wrapper import Handler
from comms.transports.telegram.storage.settings import get_setting

__all__ = ["inspect_handlers"]

_SHOWN = (
    "disclosure_ref",
    "committed_at",
    "tool_name",
    "project_count",
    "effective_egress_level",
    "records_disclosed",
    "bytes_disclosed",
    "partial",
    "proof_key_id",
)


def _body(args: dict[str, Any], allowed: set[str]) -> dict[str, Any]:
    body = {k: v for k, v in args.items() if k != "presence"}
    if set(body) - allowed:
        raise ValueError("unknown argument")
    return body


def inspect_handlers(
    conn: sqlite3.Connection,
    *,
    lineage: RestoreLineageLookup,
    broker: Any,
    prompter: Any,
    clock: Callable[[], float] = time.time,
) -> dict[str, Handler]:
    def _ref(args: dict[str, Any]) -> str:
        ref = _body(args, {"disclosure_ref"}).get("disclosure_ref")
        if not isinstance(ref, str) or not ref.startswith("tdr_"):
            raise ValueError("disclosure_ref must be a tdr_ ref")
        return ref

    def delivery(ref: str) -> str:
        """Phase-3 truth (design §6.8): accounted but withheld is not the same as handed off."""
        latched = get_setting(conn, "audit.integrity_degraded") and (
            get_setting(conn, "audit.degraded_disclosure_ref") == ref
        )
        repaired = conn.execute(
            "SELECT 1 FROM audit_events WHERE tool_name = 'admin.repair_anchor'"
            " AND disclosure_ref = ?",
            (ref,),
        ).fetchone()
        return "withheld_audit_unavailable" if latched or repaired else "committed"

    def show(args: dict[str, Any]) -> dict[str, Any]:
        ref = _ref(args)
        row = conn.execute(
            f"SELECT {', '.join(_SHOWN)} FROM disclosure_receipts WHERE disclosure_ref = ?",
            (ref,),
        ).fetchone()
        if row is None:
            raise ValueError("unknown disclosure_ref")
        shown = dict(zip(_SHOWN, row, strict=True))
        shown["partial"] = bool(shown["partial"])
        shown["signature"] = "valid" if verify_persisted_receipt(conn, ref) else "invalid"
        shown["lineage"] = lineage.affecting(conn, ref).state
        shown["delivery"] = delivery(ref)
        return shown

    def verify(args: dict[str, Any]) -> dict[str, Any]:
        ref = _ref(args)
        if (
            conn.execute(
                "SELECT 1 FROM disclosure_receipts WHERE disclosure_ref = ?", (ref,)
            ).fetchone()
            is None
        ):
            raise ValueError("unknown disclosure_ref")
        state = lineage.affecting(conn, ref).state
        return {
            "signature_valid": verify_persisted_receipt(conn, ref),
            "payload_reconstructable": state != "payload_unreconstructable",
            "lineage": state,
            "delivery": delivery(ref),
        }

    def key(args: dict[str, Any]) -> dict[str, Any]:
        wanted = _body(args, {"key_id"}).get("key_id")
        if wanted is not None and not isinstance(wanted, str):
            raise ValueError("key_id must be a string")
        found = (
            lookup_verification_key(conn, wanted)
            if wanted is not None
            else current_verification_key(conn, "disclosure_proof")
        )
        return {"key": dict(found) if found is not None else None}

    def exposure(args: dict[str, Any]) -> dict[str, Any]:
        body = _body(args, {"client_ref", "project_ref"})
        wanted = body.get("client_ref")
        clients = conn.execute(
            "SELECT id, client_ref FROM mcp_clients ORDER BY client_ref"
        ).fetchall()
        if wanted is not None:
            clients = [c for c in clients if c[1] == wanted]
            if not clients:
                raise ValueError("unknown client_ref")
        project = body.get("project_ref")
        if (
            project is not None
            and conn.execute("SELECT 1 FROM projects WHERE project_ref = ?", (project,)).fetchone()
            is None
        ):
            raise ValueError("unknown project_ref")
        minutes = get_setting(conn, "exposure_budget.rolling_window_minutes")
        since = window_start(clock(), minutes)

        def bucket(client_id: int, kind: str, subject: str = "") -> dict[str, Any]:
            used = committed_usage(
                conn, BucketKey(client_id, kind, subject_digest(kind, subject)), since=since
            )
            limits = thresholds_for(conn, kind)
            return {
                "records": used.records,
                "bytes": used.bytes,
                "soft_records": limits.soft_records,
                "hard_records": limits.hard_records,
                "soft_bytes": limits.soft_bytes,
                "hard_bytes": limits.hard_bytes,
            }

        rows = []
        for client_id, client_ref in clients:
            row: dict[str, Any] = {"client_ref": client_ref, GLOBAL: bucket(client_id, GLOBAL)}
            if project is not None:
                row[PROJECT] = bucket(client_id, PROJECT, project)
            rows.append(row)
        return {"window_minutes": minutes, "clients": rows}

    def consent(args: dict[str, Any]) -> dict[str, Any]:
        _body(args, set())
        return {"agent_connected": bool(prompter.connected), "pending": int(broker.pending_count())}

    return {
        "disclosure show": show,
        "disclosure verify": verify,
        "disclosure key": key,
        "exposure status": exposure,
        "consent status": consent,
    }
