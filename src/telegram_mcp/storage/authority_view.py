# src/telegram_mcp/storage/authority_view.py
"""Bind SQLite authority rows to the abstract ``AuthorityView``.

The policy engine stays storage-free (``authority/policy.py``); this is the
one place its view is filled from the database. It is read fresh on every
snapshot and every revalidation, never cached: a cached grant is a revoke
that does not take effect.

Canonical peer identity is ``"<telegram_peer_type>:<telegram_peer_id>"``
(spec §10.3), never an opaque ref.
"""

from __future__ import annotations

import hashlib
import sqlite3

from telegram_mcp.authority.policy import (
    AuthorityView,
    ClientProjectGrant,
    ClientState,
    ProjectState,
    make_view,
)
from telegram_mcp.consent.challenge import jcs_dumps

__all__ = [
    "grant_digest",
    "load_security",
    "load_view",
    "owner_account",
    "peer_identity",
    "project_labels",
]


def peer_identity(peer_type: str, peer_id: int) -> str:
    """Canonical policy identity (spec §10.3)."""
    return f"{peer_type}:{int(peer_id)}"


def grant_digest(
    can_read: bool, can_cross_search: bool, egress_level: str, excerpt: int | None
) -> str:
    """Content digest of one grant: any bit that changes, changes it."""
    return hashlib.sha256(
        jcs_dumps(
            {
                "can_cross_search": bool(can_cross_search),
                "can_read": bool(can_read),
                "egress_level": egress_level,
                "excerpt_max_codepoints": excerpt,
                "schema": "tg-mcp-grant/v1",
            }
        )
    ).hexdigest()


def load_security(conn: sqlite3.Connection) -> tuple[int, bool]:
    row = conn.execute(
        "SELECT security_epoch, locked FROM security_state WHERE singleton_id = 1"
    ).fetchone()
    if row is None:
        raise ValueError("security_state singleton is missing")
    return int(row[0]), bool(row[1])


def owner_account(conn: sqlite3.Connection, *, principal_id: int) -> int | None:
    """The single account the owner has policy for, or None (unconfigured)."""
    rows = conn.execute(
        "SELECT account_id FROM policy_state WHERE principal_id = ?", (principal_id,)
    ).fetchall()
    if len(rows) != 1:
        return None
    return int(rows[0][0])


def project_labels(conn: sqlite3.Connection, *, account_id: int) -> dict[str, tuple[str, str]]:
    return {
        row[0]: (row[1], row[2])
        for row in conn.execute(
            "SELECT project_ref, slug, display_name FROM projects WHERE account_id = ?",
            (account_id,),
        )
    }


def load_view(conn: sqlite3.Connection, *, principal_id: int, account_id: int) -> AuthorityView:
    principal_ref = conn.execute(
        "SELECT principal_ref FROM principals WHERE id = ?", (principal_id,)
    ).fetchone()
    if principal_ref is None:
        raise ValueError("unknown principal")
    clients = {
        row[0]: ClientState(client_ref=row[0], enabled=bool(row[1]), principal_ref=principal_ref[0])
        for row in conn.execute(
            "SELECT client_ref, enabled FROM mcp_clients WHERE principal_id = ?", (principal_id,)
        )
    }
    projects = {
        row[0]: ProjectState(project_ref=row[0], enabled=bool(row[1]), project_epoch=int(row[2]))
        for row in conn.execute(
            "SELECT project_ref, enabled, project_epoch FROM projects WHERE account_id = ?",
            (account_id,),
        )
    }
    grants: dict[tuple[str, str], ClientProjectGrant] = {}
    for row in conn.execute(
        "SELECT c.client_ref, p.project_ref, cp.can_read, cp.can_cross_search,"
        " cp.egress_level, cp.excerpt_max_codepoints"
        " FROM client_projects cp"
        " JOIN mcp_clients c ON c.id = cp.client_id"
        " JOIN projects p ON p.id = cp.project_id"
        " WHERE c.principal_id = ? AND p.account_id = ?",
        (principal_id, account_id),
    ):
        grants[(row[0], row[1])] = ClientProjectGrant(
            can_read=bool(row[2]),
            can_cross_search=bool(row[3]),
            egress_level=row[4],
            excerpt_limit=row[5],
            grant_digest=grant_digest(bool(row[2]), bool(row[3]), row[4], row[5]),
        )
    memberships: dict[str, set[str]] = {ref: set() for ref in projects}
    for row in conn.execute(
        "SELECT p.project_ref, pe.telegram_peer_type, pe.telegram_peer_id"
        " FROM project_peers pp"
        " JOIN projects p ON p.id = pp.project_id"
        " JOIN peers pe ON pe.id = pp.peer_id"
        " WHERE p.account_id = ?",
        (account_id,),
    ):
        memberships[row[0]].add(peer_identity(row[1], row[2]))
    allows: set[str] = set()
    denies: set[str] = set()
    for row in conn.execute(
        "SELECT telegram_peer_type, telegram_peer_id, decision FROM peer_policy"
        " WHERE principal_id = ? AND account_id = ?",
        (principal_id, account_id),
    ):
        (allows if row[2] == "allow" else denies).add(peer_identity(row[0], row[1]))
    policy = conn.execute(
        "SELECT policy_epoch, mode FROM policy_state WHERE principal_id = ? AND account_id = ?",
        (principal_id, account_id),
    ).fetchone()
    security_epoch, _locked = load_security(conn)
    return make_view(
        clients=clients,
        projects=projects,
        grants=grants,
        memberships={ref: peers for ref, peers in memberships.items()},
        owner_allows=allows,
        owner_denies=denies,
        policy_epoch=int(policy[0]) if policy else 0,
        security_epoch=security_epoch,
        owner_mode=policy[1] if policy else "allowlist",
    )
