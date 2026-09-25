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
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from comms.core.opaque import mint_opaque_ref
from comms.transports.telegram.ipc.handlers._wrapper import Handler, TxCommand, tx_handler
from comms.transports.telegram.storage.identity import active_account
from comms.transports.telegram.telegram.discovery import DiscoveryStore

__all__ = ["PROJECT_COMMANDS", "MemberView", "member_commands", "project_handlers"]

_SLUG = re.compile(r"[a-z0-9][a-z0-9-]{0,31}\Z")
_EGRESS = ("metadata_only", "excerpt", "full_text")
_MODES = ("allowlist", "all_cloud_chats")
# Display strings must not spoof operator output or a client's rendering. Bidi
# embedding/override/isolate controls, LRM/RLM, the Arabic letter mark and
# line/paragraph separators are refused. ZWNJ (U+200C) stays: Persian needs it.
PROMPT_UNSAFE: frozenset[int] = frozenset(
    {0x200E, 0x200F, 0x061C, 0x2028, 0x2029, *range(0x202A, 0x202F), *range(0x2066, 0x206A)}
)


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _args(args: dict[str, Any], allowed: set[str]) -> dict[str, Any]:
    body = dict(args)
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
    account = active_account(conn)
    if account is None:
        raise ValueError("exactly one account is required")
    return {**parsed, "account_id": account}


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


def _parse_rename(args: dict[str, Any]) -> dict[str, Any]:
    body = _args(args, {"project_ref", "display_name"})
    return {**body, "name": _display_name(body.get("display_name"))}


def _plan_project(conn: sqlite3.Connection, parsed: dict[str, Any]) -> dict[str, Any]:
    return {**parsed, "project_id": _id(conn, "projects", "project_ref", parsed.get("project_ref"))}


def _apply_rename(conn: sqlite3.Connection, plan: dict[str, Any]) -> dict[str, Any]:
    conn.execute(
        "UPDATE projects SET display_name = ?, project_epoch = project_epoch + 1, updated_at = ?"
        " WHERE id = ?",
        (plan["name"], _now(), plan["project_id"]),
    )
    return {"display_name": plan["name"]}


def _cross(value: int) -> TxCommand[Any, Any]:
    def apply(conn: sqlite3.Connection, plan: dict[str, Any]) -> dict[str, Any]:
        changed = conn.execute(
            "UPDATE client_projects SET can_cross_search = ?, updated_at = ?"
            " WHERE client_id = ? AND project_id = ?",
            (value, _now(), plan["client_id"], plan["project_id"]),
        ).rowcount
        if changed != 1:
            raise ValueError("no such grant")
        return {"can_cross_search": bool(value)}

    return TxCommand(lambda args: _args(args, {"project_ref", "client_ref"}), _pair, apply)


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
    "project rename": TxCommand(_parse_rename, _plan_project, _apply_rename),
    "project grant-cross-search": _cross(1),
    "project revoke-cross-search": _cross(0),
}


@dataclass(frozen=True)
class MemberView:
    """What a ``project members`` handle resolves to. No reversible Telegram id."""

    project_ref: str
    peer_row_id: int
    peer_ref: str
    display_name: str | None
    chat_type: str
    username: str | None


_TARGETS = {
    "chatgpt": "ChatGPT project",
    "codex": "Codex workspace",
    "claude": "Claude Code workspace",
}


def member_commands(store: DiscoveryStore) -> dict[str, TxCommand[Any, Any]]:
    """``project remove-peer`` as a TxCommand, so ``policy simulate`` covers it (review #3).

    ``store.take`` is non-consuming on success (``discovery.py:46-51``), so a
    simulated plan leaves the handle usable for the real command.
    """

    def parse_remove(args: dict[str, Any]) -> dict[str, Any]:
        body = _args(args, {"project_ref", "handle"})
        if not isinstance(body.get("handle"), str) or not body["handle"].startswith("tgl_"):
            raise ValueError("handle must be a tgl_ selection from project members")
        return body

    def plan_remove(conn: sqlite3.Connection, parsed: dict[str, Any]) -> dict[str, Any]:
        row = conn.execute(
            "SELECT id, project_epoch FROM projects WHERE project_ref = ?",
            (parsed.get("project_ref"),),
        ).fetchone()
        if row is None:
            raise ValueError("unknown project_ref")
        view = store.take(parsed["handle"], policy_epoch=int(row[1]))
        if view.project_ref != parsed["project_ref"]:
            raise ValueError("unknown or expired selection")
        return {"project_id": int(row[0]), "peer_row_id": view.peer_row_id}

    def apply_remove(conn: sqlite3.Connection, plan: dict[str, Any]) -> dict[str, Any]:
        removed = conn.execute(
            "DELETE FROM project_peers WHERE project_id = ? AND peer_id = ?",
            (plan["project_id"], plan["peer_row_id"]),
        ).rowcount
        if removed != 1:
            raise ValueError("not a member")
        conn.execute(
            "UPDATE projects SET project_epoch = project_epoch + 1, updated_at = ? WHERE id = ?",
            (_now(), plan["project_id"]),
        )
        return {"removed": True}

    return {"project remove-peer": TxCommand(parse_remove, plan_remove, apply_remove)}


def project_handlers(
    conn: sqlite3.Connection, *, members: DiscoveryStore | None = None
) -> dict[str, Handler]:
    store = members if members is not None else DiscoveryStore()

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

    def members_(args: dict[str, Any]) -> dict[str, Any]:
        body = _args(args, {"project_ref"})
        row = conn.execute(
            "SELECT id, project_epoch FROM projects WHERE project_ref = ?",
            (body.get("project_ref"),),
        ).fetchone()
        if row is None:
            raise ValueError("unknown project_ref")
        views = [
            MemberView(body["project_ref"], int(r[0]), r[1], r[2], r[3], r[4])
            for r in conn.execute(
                "SELECT pe.id, pe.peer_ref, pe.display_name_cache, pe.telegram_peer_type,"
                " pe.username_cache FROM project_peers pp JOIN peers pe ON pe.id = pp.peer_id"
                " WHERE pp.project_id = ? ORDER BY pe.peer_ref",
                (row[0],),
            )
        ]
        listed = store.new_snapshot(views, policy_epoch=int(row[1]))
        for entry, view in zip(listed, views, strict=True):
            entry["peer_ref"] = view.peer_ref
        return {"members": listed}

    def overlap(args: dict[str, Any]) -> dict[str, Any]:
        body = _args(args, {"project_a", "project_b"})
        given = [body.get("project_a"), body.get("project_b")]
        if sum(v is not None for v in given) == 1:
            raise ValueError("give both project_a and project_b, or neither")
        pair = {v for v in given if v is not None}
        for ref in pair:
            _id(conn, "projects", "project_ref", ref)  # unknown refs refuse
        if len(pair) == 1:
            raise ValueError("project_a and project_b must differ")
        grouped: dict[str, list[dict[str, str]]] = {}
        for peer_ref, project_ref, kind in conn.execute(
            "SELECT pe.peer_ref, p.project_ref, pp.membership_kind FROM project_peers pp"
            " JOIN projects p ON p.id = pp.project_id JOIN peers pe ON pe.id = pp.peer_id"
            " WHERE pp.peer_id IN (SELECT peer_id FROM project_peers GROUP BY peer_id"
            " HAVING COUNT(*) > 1) ORDER BY pe.peer_ref, p.project_ref"
        ):
            grouped.setdefault(peer_ref, []).append(
                {"project_ref": project_ref, "membership_kind": kind}
            )
        overlaps = [
            {
                "peer_ref": peer_ref,
                "projects": projects,
                "explicitly_shared": any(p["membership_kind"] == "shared" for p in projects),
            }
            for peer_ref, projects in sorted(grouped.items())
            if not pair or pair <= {p["project_ref"] for p in projects}
        ]
        return {"overlaps": overlaps}

    def instruction(args: dict[str, Any]) -> dict[str, Any]:
        body = _args(args, {"project_ref", "target"})
        target = body.get("target")
        if target not in _TARGETS:
            raise ValueError("target must be chatgpt, codex or claude")
        row = conn.execute(
            "SELECT display_name FROM projects WHERE project_ref = ?", (body.get("project_ref"),)
        ).fetchone()
        if row is None:
            raise ValueError("unknown project_ref")
        snippet = (
            f"Telegram gateway project: {row[0]}\n"
            f"project_ref: {body['project_ref']}\n"
            f"Pass this project_ref to every Telegram tool call in this {_TARGETS[target]}.\n"
            "This note routes calls; it grants no access."
        )
        return {"snippet": snippet}

    return {
        **{name: tx_handler(conn, command) for name, command in PROJECT_COMMANDS.items()},
        "project list": list_,
        "project members": members_,
        "project remove-peer": tx_handler(conn, member_commands(store)["project remove-peer"]),
        "project overlap": overlap,
        "project instruction": instruction,
    }
