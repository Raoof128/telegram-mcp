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

from comms.transports.telegram.ipc.handlers._wrapper import TxCommand, tx_handler
from comms.transports.telegram.storage.refstore import RefStore
from comms.transports.telegram.telegram.deadline import Deadline, WorkBudget
from comms.transports.telegram.telegram.discovery import DiscoveryStore

__all__ = ["scope_commands", "scope_handlers"]

_DISCOVER_DEADLINE_S = 30.0


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _owner(conn: sqlite3.Connection) -> tuple[int, int, int]:
    row = conn.execute("SELECT principal_id, account_id, policy_epoch FROM policy_state").fetchone()
    if row is None:
        raise ValueError("log in first: no account exists")
    return int(row[0]), int(row[1]), int(row[2])


def _parse_handle(args: dict[str, Any], extra: frozenset[str] = frozenset()) -> dict[str, Any]:
    body = dict(args)
    if set(body) - {"handle", *extra}:
        raise ValueError("unknown argument")
    handle = body.get("handle")
    if not isinstance(handle, str) or not handle.startswith("tgl_"):
        raise ValueError("handle must be a tgl_ selection from scope discover")
    return body


def scope_commands(discovery: DiscoveryStore) -> dict[str, TxCommand[Any, Any]]:
    def plan_selection(conn: sqlite3.Connection, parsed: dict[str, Any]) -> dict[str, Any]:
        principal, account, epoch = _owner(conn)
        view = discovery.take(parsed["handle"], policy_epoch=epoch)
        return {**parsed, "principal": principal, "account": account, "view": view}

    def decide(decision: str) -> TxCommand[Any, Any]:
        def apply(conn: sqlite3.Connection, plan: dict[str, Any]) -> dict[str, Any]:
            view = plan["view"]
            RefStore(conn, account_id=plan["account"]).ensure_peer_in_tx(
                view.peer_type, view.peer_id, display_name=view.display_name, username=view.username
            )
            now = _now()
            conn.execute(
                "INSERT INTO peer_policy (principal_id, account_id, telegram_peer_type,"
                " telegram_peer_id, decision, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)"
                " ON CONFLICT(principal_id, account_id, telegram_peer_type, telegram_peer_id)"
                " DO UPDATE SET decision = excluded.decision, updated_at = excluded.updated_at",
                (
                    plan["principal"],
                    plan["account"],
                    view.peer_type,
                    view.peer_id,
                    decision,
                    now,
                    now,
                ),
            )
            conn.execute(
                "UPDATE policy_state SET policy_epoch = policy_epoch + 1, updated_at = ?"
                " WHERE principal_id = ? AND account_id = ?",
                (now, plan["principal"], plan["account"]),
            )
            return {"decision": decision}

        return TxCommand(
            _parse_handle, plan_selection, apply, post_commit=lambda _r: discovery.invalidate_all()
        )

    def parse_add(args: dict[str, Any]) -> dict[str, Any]:
        body = _parse_handle(args, frozenset({"project_ref", "shared"}))
        if not isinstance(body.get("shared", False), bool):
            raise ValueError("shared must be a boolean")  # noqa: TRY004 -- uniform ValueError on admin validation
        return body

    def plan_add(conn: sqlite3.Connection, parsed: dict[str, Any]) -> dict[str, Any]:
        plan = plan_selection(conn, parsed)
        project = conn.execute(
            "SELECT id FROM projects WHERE project_ref = ? AND account_id = ?",
            (parsed.get("project_ref"), plan["account"]),
        ).fetchone()
        if project is None:
            raise ValueError("unknown project_ref")
        return {**plan, "project_id": int(project[0])}

    def apply_add(conn: sqlite3.Connection, plan: dict[str, Any]) -> dict[str, Any]:
        view = plan["view"]
        peer = RefStore(conn, account_id=plan["account"]).ensure_peer_in_tx(
            view.peer_type, view.peer_id, display_name=view.display_name, username=view.username
        )
        now = _now()
        try:
            conn.execute(
                "INSERT INTO project_peers (project_id, peer_id, membership_kind, created_at,"
                " updated_at) VALUES (?, ?, ?, ?, ?)",
                (
                    plan["project_id"],
                    peer.row_id,
                    "shared" if plan.get("shared") else "primary",
                    now,
                    now,
                ),
            )
            conn.execute(
                "UPDATE projects SET project_epoch = project_epoch + 1, updated_at = ? WHERE id = ?",
                (now, plan["project_id"]),
            )
        except sqlite3.IntegrityError:
            raise ValueError(
                "membership refused: overlapping membership must be declared shared"
            ) from None
        return {"added": True}

    def apply_remove(conn: sqlite3.Connection, plan: dict[str, Any]) -> dict[str, Any]:
        view = plan["view"]
        removed = conn.execute(
            "DELETE FROM peer_policy WHERE principal_id = ? AND account_id = ?"
            " AND telegram_peer_type = ? AND telegram_peer_id = ?",
            (plan["principal"], plan["account"], view.peer_type, view.peer_id),
        ).rowcount
        if removed != 1:
            raise ValueError("no rule for that chat")
        conn.execute(
            "UPDATE policy_state SET policy_epoch = policy_epoch + 1, updated_at = ?"
            " WHERE principal_id = ? AND account_id = ?",
            (_now(), plan["principal"], plan["account"]),
        )
        return {"removed": True}

    return {
        "scope allow": decide("allow"),
        "scope deny": decide("deny"),
        "project add-peer": TxCommand(parse_add, plan_add, apply_add),
        "scope remove": TxCommand(
            _parse_handle,
            plan_selection,
            apply_remove,
            post_commit=lambda _r: discovery.invalidate_all(),
        ),
    }


def scope_handlers(
    conn: sqlite3.Connection, session: Any, discovery: DiscoveryStore
) -> dict[str, Callable[[dict[str, Any]], Any]]:
    async def discover(args: dict[str, Any]) -> dict[str, Any]:
        _principal, _account, epoch = _owner(conn)
        views, complete = await session.scan_dialogs(
            client_ref="operator", deadline=Deadline(_DISCOVER_DEADLINE_S), budget=WorkBudget()
        )
        return {
            "selections": discovery.new_snapshot(views, policy_epoch=epoch),
            "complete": complete,
        }

    def list_(args: dict[str, Any]) -> dict[str, Any]:
        if set(args):
            raise ValueError("unknown argument")
        rows = conn.execute(
            "SELECT pp.decision, p.display_name_cache, p.telegram_peer_type FROM peer_policy pp"
            " LEFT JOIN peers p ON p.account_id = pp.account_id"
            " AND p.telegram_peer_type = pp.telegram_peer_type"
            " AND p.telegram_peer_id = pp.telegram_peer_id ORDER BY pp.decision, p.display_name_cache"
        ).fetchall()
        return {
            "rules": [{"decision": r[0], "display_name": r[1], "peer_type": r[2]} for r in rows]
        }

    handlers: dict[str, Callable[[dict[str, Any]], Any]] = {
        name: tx_handler(conn, command) for name, command in scope_commands(discovery).items()
    }
    handlers.update({"scope discover": discover, "scope list": list_})
    return handlers
