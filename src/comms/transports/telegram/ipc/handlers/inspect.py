"""Operator inspection: receipts and keys (spec §23A.3, §33). ``exposure status`` is retired
(comms v0.3) and lives in ``exposure.py``."""

from __future__ import annotations

import sqlite3
from typing import Any

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
    body = dict(args)
    if set(body) - allowed:
        raise ValueError("unknown argument")
    return body


def inspect_handlers(
    conn: sqlite3.Connection,
    *,
    lineage: RestoreLineageLookup,
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

    return {
        "disclosure show": show,
        "disclosure verify": verify,
        "disclosure key": key,
    }
