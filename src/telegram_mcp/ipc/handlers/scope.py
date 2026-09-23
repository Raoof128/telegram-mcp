"""Operator discovery and allowlisting (spec §10.2-§10.3, §10.6).

``scope discover`` shows dialog metadata to the operator only, behind a Touch
ID approval. Selections are ``tgl_`` handles bound to the snapshot and the
policy epoch; row numbers and names are never selectors.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from telegram_mcp.disclosure.audit.chain import immediate_transaction
from telegram_mcp.storage.refstore import RefStore
from telegram_mcp.telegram.deadline import Deadline, WorkBudget
from telegram_mcp.telegram.discovery import DiscoveryStore

__all__ = ["scope_handlers"]

_DISCOVER_DEADLINE_S = 30.0


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def scope_handlers(
    conn: sqlite3.Connection, session: Any, discovery: DiscoveryStore
) -> dict[str, Callable[[dict[str, Any]], Any]]:
    def _owner() -> tuple[int, int, int]:
        row = conn.execute(
            "SELECT principal_id, account_id, policy_epoch FROM policy_state"
        ).fetchone()
        if row is None:
            raise ValueError("log in first: no account exists")
        return int(row[0]), int(row[1]), int(row[2])

    def _take(args: dict[str, Any]) -> Any:
        handle = args.get("handle")
        if not isinstance(handle, str) or not handle.startswith("tgl_"):
            raise ValueError("handle must be a tgl_ selection from scope discover")
        return discovery.take(handle, policy_epoch=_owner()[2])

    async def discover(args: dict[str, Any]) -> dict[str, Any]:
        _principal, _account, epoch = _owner()
        views, complete = await session.scan_dialogs(
            client_ref="operator", deadline=Deadline(_DISCOVER_DEADLINE_S), budget=WorkBudget()
        )
        return {
            "selections": discovery.new_snapshot(views, policy_epoch=epoch),
            "complete": complete,
        }

    def _decide(args: dict[str, Any], decision: str) -> dict[str, Any]:
        view = _take(args)
        principal, account, _epoch = _owner()
        RefStore(conn, account_id=account).ensure_peer(
            view.peer_type, view.peer_id, display_name=view.display_name, username=view.username
        )
        now = _now()
        with immediate_transaction(conn):
            conn.execute(
                "INSERT INTO peer_policy (principal_id, account_id, telegram_peer_type,"
                " telegram_peer_id, decision, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)"
                " ON CONFLICT(principal_id, account_id, telegram_peer_type, telegram_peer_id)"
                " DO UPDATE SET decision = excluded.decision, updated_at = excluded.updated_at",
                (principal, account, view.peer_type, view.peer_id, decision, now, now),
            )
            conn.execute(
                "UPDATE policy_state SET policy_epoch = policy_epoch + 1, updated_at = ?"
                " WHERE principal_id = ? AND account_id = ?",
                (now, principal, account),
            )
        discovery.invalidate_all()  # the epoch moved; every handle is dead
        return {"decision": decision}

    def list_(args: dict[str, Any]) -> dict[str, Any]:
        rows = conn.execute(
            "SELECT pp.decision, p.display_name_cache, p.telegram_peer_type FROM peer_policy pp"
            " LEFT JOIN peers p ON p.account_id = pp.account_id"
            " AND p.telegram_peer_type = pp.telegram_peer_type"
            " AND p.telegram_peer_id = pp.telegram_peer_id ORDER BY pp.decision, p.display_name_cache"
        ).fetchall()
        return {
            "rules": [{"decision": r[0], "display_name": r[1], "peer_type": r[2]} for r in rows]
        }

    def add_peer(args: dict[str, Any]) -> dict[str, Any]:
        view = _take(args)
        _principal, account, _epoch = _owner()
        project = conn.execute(
            "SELECT id FROM projects WHERE project_ref = ? AND account_id = ?",
            (args.get("project_ref"), account),
        ).fetchone()
        if project is None:
            raise ValueError("unknown project_ref")
        shared = args.get("shared", False)
        if not isinstance(shared, bool):
            raise ValueError("shared must be a boolean")  # noqa: TRY004 -- uniform ValueError on admin validation
        peer = RefStore(conn, account_id=account).ensure_peer(
            view.peer_type, view.peer_id, display_name=view.display_name, username=view.username
        )
        now = _now()
        try:
            with immediate_transaction(conn):
                conn.execute(
                    "INSERT INTO project_peers (project_id, peer_id, membership_kind, created_at,"
                    " updated_at) VALUES (?, ?, ?, ?, ?)",
                    (project[0], peer.row_id, "shared" if shared else "primary", now, now),
                )
                conn.execute(
                    "UPDATE projects SET project_epoch = project_epoch + 1, updated_at = ? WHERE id = ?",
                    (now, project[0]),
                )
        except sqlite3.IntegrityError:
            raise ValueError(
                "membership refused: overlapping membership must be declared shared"
            ) from None
        return {"added": True}

    def mode(args: dict[str, Any]) -> dict[str, Any]:
        wanted = args.get("mode")
        if wanted not in ("allowlist", "all_cloud_chats"):
            raise ValueError("mode must be allowlist or all_cloud_chats")
        principal, account, _epoch = _owner()
        with immediate_transaction(conn):
            conn.execute(
                "UPDATE policy_state SET mode = ?, policy_epoch = policy_epoch + 1, updated_at = ?"
                " WHERE principal_id = ? AND account_id = ?",
                (wanted, _now(), principal, account),
            )
        discovery.invalidate_all()
        return {"mode": wanted}

    return {
        "scope discover": discover,
        "scope mode": mode,
        "scope allow": lambda args: _decide(args, "allow"),
        "scope deny": lambda args: _decide(args, "deny"),
        "scope list": list_,
        "project add-peer": add_peer,
    }
