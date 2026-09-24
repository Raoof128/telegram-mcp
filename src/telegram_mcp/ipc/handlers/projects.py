"""Project, grant and scope-mode handlers (design §2.5).

Every write runs in one ``BEGIN IMMEDIATE`` transaction with its epoch bump.
Schema CHECKs and the Phase-2 triggers (C2 excerpt width, C4 shared
membership, §12.3 owner consistency) are the final word; a constraint
failure becomes a fixed ``ValueError`` and nothing is written. Presence is
enforced by the router before any of these runs.
"""

from __future__ import annotations

import re
import sqlite3
from datetime import UTC, datetime
from typing import Any

from telegram_mcp.ipc.handlers._wrapper import Handler, TxCommand, tx_handler
from telegram_mcp.opaque import mint_opaque_ref

__all__ = ["PROJECT_COMMANDS", "project_handlers"]

_SLUG = re.compile(r"[a-z0-9][a-z0-9-]{0,31}\Z")
_EGRESS = ("metadata_only", "excerpt", "full_text")
_MODES = ("allowlist", "all_cloud_chats")
# Spec §9.8: display strings must not spoof the trusted prompt. Bidi
# embedding/override/isolate controls, LRM/RLM, the Arabic letter mark and
# line/paragraph separators are refused. ZWNJ (U+200C) stays: Persian needs it.
PROMPT_UNSAFE: frozenset[int] = frozenset(
    {0x200E, 0x200F, 0x061C, 0x2028, 0x2029, *range(0x202A, 0x202F), *range(0x2066, 0x206A)}
)


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _args(args: dict[str, Any], allowed: set[str]) -> dict[str, Any]:
    body = {k: v for k, v in args.items() if k != "presence"}
    if set(body) - allowed:
        raise ValueError("unknown argument")
    return body


def _display_name(value: Any) -> str:
    if not isinstance(value, str) or not 1 <= len(value) <= 80:
        raise ValueError("display_name must be 1-80 characters")
    if any(ord(c) < 0x20 or 0x7F <= ord(c) <= 0x9F for c in value):
        raise ValueError("display_name must not contain control characters")
    if any(ord(c) in PROMPT_UNSAFE for c in value):
        raise ValueError("display_name must not contain bidi or line-separator controls")
    return value


def _egress(body: dict[str, Any]) -> tuple[str, int | None]:
    level = body.get("egress_level")
    if level not in _EGRESS:
        raise ValueError("invalid egress_level")
    excerpt = body.get("excerpt_max_codepoints")
    if level == "excerpt":
        if not isinstance(excerpt, int) or isinstance(excerpt, bool) or not 64 <= excerpt <= 4000:
            raise ValueError("excerpt grants need excerpt_max_codepoints 64-4000")
    elif excerpt is not None:
        raise ValueError("only excerpt grants take excerpt_max_codepoints")
    return level, excerpt


def _id(conn: sqlite3.Connection, table: str, column: str, ref: Any) -> int:
    if not isinstance(ref, str):
        raise ValueError(f"invalid {column}")  # noqa: TRY004 -- uniform ValueError on admin validation
    row = conn.execute(f"SELECT id FROM {table} WHERE {column} = ?", (ref,)).fetchone()
    if row is None:
        raise ValueError(f"unknown {column}")
    return int(row[0])


def _parse_create(args: dict[str, Any]) -> dict[str, Any]:
    body = _args(args, {"slug", "display_name"})
    slug = body.get("slug")
    if not isinstance(slug, str) or _SLUG.fullmatch(slug) is None:
        raise ValueError("slug must match [a-z0-9][a-z0-9-]{0,31}")
    return {"slug": slug, "name": _display_name(body.get("display_name"))}


def _plan_create(conn: sqlite3.Connection, parsed: dict[str, Any]) -> dict[str, Any]:
    accounts = conn.execute("SELECT id FROM accounts").fetchall()
    if len(accounts) != 1:
        raise ValueError("exactly one account is required")
    return {**parsed, "account_id": int(accounts[0][0])}


def _apply_create(conn: sqlite3.Connection, plan: dict[str, Any]) -> dict[str, Any]:
    ref = mint_opaque_ref("tpr_")
    now = _now()
    try:
        conn.execute(
            "INSERT INTO projects (account_id, project_ref, slug, display_name, enabled,"
            " project_epoch, created_at, updated_at) VALUES (?, ?, ?, ?, 1, 1, ?, ?)",
            (plan["account_id"], ref, plan["slug"], plan["name"], now, now),
        )
    except sqlite3.IntegrityError:
        raise ValueError("slug already exists") from None
    return {"project_ref": ref}


def _enabled(value: int) -> TxCommand[dict[str, Any], dict[str, Any]]:
    def plan(conn: sqlite3.Connection, parsed: dict[str, Any]) -> dict[str, Any]:
        return {"project_id": _id(conn, "projects", "project_ref", parsed.get("project_ref"))}

    def apply(conn: sqlite3.Connection, plan: dict[str, Any]) -> dict[str, Any]:
        try:
            conn.execute(
                "UPDATE projects SET enabled = ?, project_epoch = project_epoch + 1,"
                " updated_at = ? WHERE id = ?",
                (value, _now(), plan["project_id"]),
            )
        except sqlite3.IntegrityError:
            raise ValueError("enabling would activate an undeclared shared membership") from None
        return {"enabled": bool(value)}

    return TxCommand(lambda args: _args(args, {"project_ref"}), plan, apply)


def _pair(conn: sqlite3.Connection, parsed: dict[str, Any]) -> dict[str, Any]:
    return {
        **parsed,
        "project_id": _id(conn, "projects", "project_ref", parsed.get("project_ref")),
        "client_id": _id(conn, "mcp_clients", "client_ref", parsed.get("client_ref")),
    }


def _parse_grant(args: dict[str, Any]) -> dict[str, Any]:
    body = _args(
        args,
        {"project_ref", "client_ref", "egress_level", "excerpt_max_codepoints", "can_cross_search"},
    )
    level, excerpt = _egress(body)
    cross = body.get("can_cross_search", False)
    if not isinstance(cross, bool):
        raise ValueError("can_cross_search must be a boolean")  # noqa: TRY004 -- uniform ValueError on admin validation
    return {**body, "level": level, "excerpt": excerpt, "cross": cross}


def _apply_grant(conn: sqlite3.Connection, plan: dict[str, Any]) -> dict[str, Any]:
    now = _now()
    try:
        conn.execute(
            "INSERT INTO client_projects (client_id, project_id, can_read, can_cross_search,"
            " egress_level, excerpt_max_codepoints, created_at, updated_at)"
            " VALUES (?, ?, 1, ?, ?, ?, ?, ?)"
            " ON CONFLICT(client_id, project_id) DO UPDATE SET can_read = 1,"
            " can_cross_search = excluded.can_cross_search, egress_level = excluded.egress_level,"
            " excerpt_max_codepoints = excluded.excerpt_max_codepoints, updated_at = excluded.updated_at",
            (
                plan["client_id"],
                plan["project_id"],
                int(plan["cross"]),
                plan["level"],
                plan["excerpt"],
                now,
                now,
            ),
        )
    except sqlite3.IntegrityError:
        raise ValueError("grant refused by the owner-consistency rules") from None
    return {"granted": True}


def _parse_egress(args: dict[str, Any]) -> dict[str, Any]:
    body = _args(args, {"project_ref", "client_ref", "egress_level", "excerpt_max_codepoints"})
    level, excerpt = _egress(body)
    return {**body, "level": level, "excerpt": excerpt}


def _apply_egress(conn: sqlite3.Connection, plan: dict[str, Any]) -> dict[str, Any]:
    changed = conn.execute(
        "UPDATE client_projects SET egress_level = ?, excerpt_max_codepoints = ?, updated_at = ?"
        " WHERE client_id = ? AND project_id = ?",
        (plan["level"], plan["excerpt"], _now(), plan["client_id"], plan["project_id"]),
    ).rowcount
    if changed != 1:
        raise ValueError("no such grant")
    return {"egress_level": plan["level"]}


def _apply_revoke(conn: sqlite3.Connection, plan: dict[str, Any]) -> dict[str, Any]:
    removed = conn.execute(
        "DELETE FROM client_projects WHERE client_id = ? AND project_id = ?",
        (plan["client_id"], plan["project_id"]),
    ).rowcount
    if removed != 1:
        raise ValueError("no such grant")
    return {"revoked": True}


def _parse_mode(args: dict[str, Any]) -> dict[str, Any]:
    body = _args(args, {"mode"})
    if body.get("mode") not in _MODES:
        raise ValueError("mode must be allowlist or all_cloud_chats")
    return body


def _plan_owner(conn: sqlite3.Connection, parsed: dict[str, Any]) -> dict[str, Any]:
    rows = conn.execute("SELECT principal_id, account_id FROM policy_state").fetchall()
    if len(rows) != 1:
        raise ValueError("exactly one owner policy row is required")
    return {**parsed, "principal_id": int(rows[0][0]), "account_id": int(rows[0][1])}


def _apply_mode(conn: sqlite3.Connection, plan: dict[str, Any]) -> dict[str, Any]:
    conn.execute(
        "UPDATE policy_state SET mode = ?, policy_epoch = policy_epoch + 1, updated_at = ?"
        " WHERE principal_id = ? AND account_id = ?",
        (plan["mode"], _now(), plan["principal_id"], plan["account_id"]),
    )
    return {"mode": plan["mode"]}


PROJECT_COMMANDS: dict[str, TxCommand[Any, Any]] = {
    "project create": TxCommand(_parse_create, _plan_create, _apply_create),
    "project enable": _enabled(1),
    "project disable": _enabled(0),
    "project grant-client": TxCommand(_parse_grant, _pair, _apply_grant),
    "project set-egress": TxCommand(_parse_egress, _pair, _apply_egress),
    "project revoke-client": TxCommand(
        lambda args: _args(args, {"project_ref", "client_ref"}), _pair, _apply_revoke
    ),
    "scope mode": TxCommand(_parse_mode, _plan_owner, _apply_mode),
}


def project_handlers(conn: sqlite3.Connection) -> dict[str, Handler]:
    def list_(args: dict[str, Any]) -> dict[str, Any]:
        _args(args, set())
        rows = conn.execute(
            "SELECT project_ref, slug, display_name, enabled, project_epoch FROM projects"
            " ORDER BY slug"
        ).fetchall()
        return {
            "projects": [
                {
                    "project_ref": r[0],
                    "slug": r[1],
                    "display_name": r[2],
                    "enabled": bool(r[3]),
                    "project_epoch": r[4],
                }
                for r in rows
            ]
        }

    return {
        **{name: tx_handler(conn, command) for name, command in PROJECT_COMMANDS.items()},
        "project list": list_,
    }
