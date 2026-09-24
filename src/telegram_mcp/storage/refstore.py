"""Persistent opaque refs for peers and messages (spec §11, §12.2).

Refs are minted only by callers that have already authorised the record
(§17.4). The policy key is canonical ``(account, type, id)``, never a ref, so
reminting a ref can never bypass policy (§10.3).
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime

from telegram_mcp.disclosure.audit.chain import immediate_transaction
from telegram_mcp.opaque import mint_opaque_ref

__all__ = ["PEER_TYPES", "PeerRow", "RefStore"]

PEER_TYPES = ("user", "chat", "channel")
_COLUMNS = "id, peer_ref, telegram_peer_type, telegram_peer_id, display_name_cache, username_cache"


@dataclass(frozen=True)
class PeerRow:
    row_id: int
    peer_ref: str
    peer_type: str
    peer_id: int
    display_name: str | None
    username: str | None

    @property
    def identity(self) -> str:
        return f"{self.peer_type}:{self.peer_id}"


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _row(r: tuple | None) -> PeerRow | None:
    return None if r is None else PeerRow(int(r[0]), r[1], r[2], int(r[3]), r[4], r[5])


class RefStore:
    def __init__(self, conn: sqlite3.Connection, *, account_id: int) -> None:
        self._conn = conn
        self._account = account_id

    def ensure_peer_in_tx(
        self, peer_type: str, peer_id: int, *, display_name: str | None, username: str | None
    ) -> PeerRow:
        """Upsert inside the caller's transaction (Phase-5 design §2.1). Never commits."""
        if not self._conn.in_transaction:
            raise ValueError("ensure_peer_in_tx requires an open transaction")
        if peer_type not in PEER_TYPES:
            raise ValueError("unknown peer type")
        now = _now()
        existing = self._conn.execute(
            "SELECT id FROM peers WHERE account_id = ? AND telegram_peer_type = ?"
            " AND telegram_peer_id = ?",
            (self._account, peer_type, peer_id),
        ).fetchone()
        if existing is None:
            self._conn.execute(
                "INSERT INTO peers (account_id, peer_ref, telegram_peer_type, telegram_peer_id,"
                " display_name_cache, username_cache, first_seen_at, last_seen_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    self._account,
                    mint_opaque_ref("tgp_"),
                    peer_type,
                    peer_id,
                    display_name,
                    username,
                    now,
                    now,
                ),
            )
        else:
            self._conn.execute(
                "UPDATE peers SET display_name_cache = ?, username_cache = ?, last_seen_at = ?"
                " WHERE id = ?",
                (display_name, username, now, existing[0]),
            )
        found = self.peer_by_identity(f"{peer_type}:{peer_id}")
        assert found is not None
        return found

    def ensure_peer(
        self, peer_type: str, peer_id: int, *, display_name: str | None, username: str | None
    ) -> PeerRow:
        with immediate_transaction(self._conn):
            return self.ensure_peer_in_tx(
                peer_type, peer_id, display_name=display_name, username=username
            )

    def peer_by_ref(self, peer_ref: str) -> PeerRow | None:
        return _row(
            self._conn.execute(
                f"SELECT {_COLUMNS} FROM peers WHERE account_id = ? AND peer_ref = ?",
                (self._account, peer_ref),
            ).fetchone()
        )

    def peer_by_row(self, row_id: int) -> PeerRow | None:
        return _row(
            self._conn.execute(
                f"SELECT {_COLUMNS} FROM peers WHERE account_id = ? AND id = ?",
                (self._account, row_id),
            ).fetchone()
        )

    def peer_by_identity(self, identity: str) -> PeerRow | None:
        peer_type, _, raw = identity.partition(":")
        if peer_type not in PEER_TYPES or not raw.lstrip("-").isdigit():
            return None
        return _row(
            self._conn.execute(
                f"SELECT {_COLUMNS} FROM peers WHERE account_id = ? AND telegram_peer_type = ?"
                " AND telegram_peer_id = ?",
                (self._account, peer_type, int(raw)),
            ).fetchone()
        )

    def peers_by_identities(self, identities: Iterable[str]) -> dict[str, PeerRow]:
        out: dict[str, PeerRow] = {}
        for identity in identities:
            row = self.peer_by_identity(identity)
            if row is not None:
                out[identity] = row
        return out

    def message_ref(self, peer_row_id: int, message_id: int) -> str:
        now = _now()
        with immediate_transaction(self._conn):
            row = self._conn.execute(
                "SELECT message_ref FROM message_refs WHERE account_id = ? AND peer_id = ?"
                " AND telegram_message_id = ?",
                (self._account, peer_row_id, message_id),
            ).fetchone()
            if row is not None:
                self._conn.execute(
                    "UPDATE message_refs SET last_used_at = ? WHERE message_ref = ?", (now, row[0])
                )
                return str(row[0])
            ref = mint_opaque_ref("tgm_")
            self._conn.execute(
                "INSERT INTO message_refs (account_id, peer_id, message_ref, telegram_message_id,"
                " minted_at, last_used_at) VALUES (?, ?, ?, ?, ?, ?)",
                (self._account, peer_row_id, ref, message_id, now, now),
            )
            return ref

    def message_by_ref(self, ref: str) -> tuple[int, int] | None:
        row = self._conn.execute(
            "SELECT peer_id, telegram_message_id FROM message_refs WHERE account_id = ?"
            " AND message_ref = ?",
            (self._account, ref),
        ).fetchone()
        return None if row is None else (int(row[0]), int(row[1]))
