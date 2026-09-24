"""Restore lineage (Phase-5 design §2.6, §4.5).

5a ships the seam: nothing has been restored, so every receipt answers
``none``. 5c replaces this with the table-backed lookup, so a receipt whose
refs were re-minted by an authorised restore reports
``payload_unreconstructable`` instead of looking like tampering.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Protocol

__all__ = ["LineageVerdict", "NoRestoreLineage", "RestoreLineageLookup"]


@dataclass(frozen=True)
class LineageVerdict:
    state: str  # "none" | "payload_unreconstructable"


class RestoreLineageLookup(Protocol):
    def affecting(self, conn: sqlite3.Connection, disclosure_ref: str) -> LineageVerdict: ...


class NoRestoreLineage:
    def affecting(self, conn: sqlite3.Connection, disclosure_ref: str) -> LineageVerdict:
        return LineageVerdict("none")
