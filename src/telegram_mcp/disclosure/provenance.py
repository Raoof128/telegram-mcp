"""Privacy-safe provenance vector and digest (spec §23A.2A; design §3.2).

§23A.2A says the digest commits to "the ordered set of privacy-safe record
identities", which cannot be taken literally — a set has no order, and two
implementations would canonicalise it differently. This module reads it as
an ordered vector in **emitted-record order**, one entry per record actually
present in ``data``, each carrying exactly four fields.

It binds what leaves, not what Telegram returned, and it never hashes
message or search text.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from typing import Any

from telegram_mcp.consent.challenge import jcs_dumps
from telegram_mcp.disclosure.egress import effective_egress_level
from telegram_mcp.disclosure.measure import RECORD_ELEMENT, MeasurementError

__all__ = ["provenance_digest", "provenance_vector"]


def _record_ref(record: Mapping[str, Any]) -> str:
    for field in ("message_ref", "peer_ref", "project_ref", "chat_ref"):
        value = record.get(field)
        if isinstance(value, str):
            return value
    raise ValueError("record carries no privacy-safe identity")


def provenance_vector(tool_name: str, data: Mapping[str, Any]) -> list[dict[str, Any]]:
    """One entry per emitted record, in emitted order."""
    element = RECORD_ELEMENT.get(tool_name)
    if element is None:
        raise MeasurementError("tool has no provenance vector")
    vector: list[dict[str, Any]] = []
    for record in data.get(element, []):
        vector.append(
            {
                "record_ref": _record_ref(record),
                "origin_project_refs": sorted(record.get("origin_project_refs") or []),
                "egress_level": effective_egress_level([record]),
                "text_truncated": bool(record.get("text_truncated", False)),
            }
        )
    return vector


def provenance_digest(tool_name: str, data: Mapping[str, Any]) -> str:
    """``SHA-256(JCS(vector))`` in lowercase hexadecimal."""
    return hashlib.sha256(jcs_dumps(provenance_vector(tool_name, data))).hexdigest()
