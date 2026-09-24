"""SQLite glue for metadata-effective access (Phase-5 design §2.3)."""

from __future__ import annotations

import hashlib
import sqlite3
from typing import Any

from telegram_mcp.authority.effective import AccessRow, effective_rows, rows_digest
from telegram_mcp.authority.policy import (
    AuthorityRequest,
    AuthorityView,
    PeerFacts,
    evaluate_with_trace,
)
from telegram_mcp.authority.staging import Binding
from telegram_mcp.consent.challenge import jcs_dumps
from telegram_mcp.storage.authority_view import load_security, load_view, owner_account

__all__ = ["base_digest", "current_binding", "explain", "known_refs", "snapshot"]


def _inputs(
    conn: sqlite3.Connection,
) -> tuple[AuthorityView, dict[str, tuple[str, PeerFacts]]] | None:
    principal = conn.execute("SELECT id FROM principals ORDER BY id LIMIT 1").fetchone()
    if principal is None:
        return None
    account = owner_account(conn, principal_id=int(principal[0]))
    if account is None:
        return None
    view = load_view(conn, principal_id=int(principal[0]), account_id=account)
    peers = {
        f"{row[0]}:{int(row[1])}": (row[2], PeerFacts.from_stored(row[0]))
        for row in conn.execute(
            "SELECT telegram_peer_type, telegram_peer_id, peer_ref FROM peers WHERE account_id = ?",
            (account,),
        )
    }
    return view, peers


def snapshot(conn: sqlite3.Connection) -> tuple[AccessRow, ...]:
    loaded = _inputs(conn)
    if loaded is None:
        return ()
    return effective_rows(*loaded)


def explain(
    conn: sqlite3.Connection, *, client_ref: str, project_ref: str, peer_ref: str | None = None
) -> list[dict[str, Any]]:
    loaded = _inputs(conn)
    if loaded is None:
        return []
    view, peers = loaded
    if project_ref not in view.projects:
        raise ValueError("unknown project_ref")
    by_ref = {ref: (identity, facts) for identity, (ref, facts) in peers.items()}
    if peer_ref is not None and peer_ref not in by_ref:
        raise ValueError("unknown peer_ref")
    requests: list[tuple[str, AuthorityRequest]] = []
    if peer_ref is None:
        requests.append(("", AuthorityRequest("discover", client_ref, (project_ref,))))
    members = view.memberships.get(project_ref, frozenset())
    targets = (
        [peer_ref]
        if peer_ref is not None
        else sorted(ref for ref, (identity, _facts) in by_ref.items() if identity in members)
    )
    for ref in targets:
        identity, facts = by_ref[ref]
        for operation in ("read", "cross_search"):
            requests.append(
                (
                    ref,
                    AuthorityRequest(operation, client_ref, (project_ref,), identity, facts=facts),
                )
            )
    out = []
    for ref, request in requests:
        verdict, trace = evaluate_with_trace(view, request)
        out.append(
            {
                "client_ref": client_ref,
                "project_ref": project_ref,
                "peer_ref": ref,
                "operation": request.operation,
                "decision": getattr(verdict, "code", "allow"),
                "trace": [list(step) for step in trace],
            }
        )
    return out


def base_digest(conn: sqlite3.Connection) -> str:
    """What a staged change was computed against (design §2.3 ``tps_`` binding)."""
    security_epoch, locked = load_security(conn)
    policy = [
        [int(r[0]), int(r[1]), int(r[2])]
        for r in conn.execute(
            "SELECT principal_id, account_id, policy_epoch FROM policy_state ORDER BY 1, 2"
        )
    ]
    projects = {
        r[0]: int(r[1])
        for r in conn.execute("SELECT project_ref, project_epoch FROM projects ORDER BY 1")
    }
    body = {
        "locked": bool(locked),
        "policy": policy,
        "projects": projects,
        "security_epoch": security_epoch,
        "snapshot": rows_digest(snapshot(conn)),
    }
    return hashlib.sha256(jcs_dumps(body)).hexdigest()


def known_refs(conn: sqlite3.Connection) -> set[str]:
    refs: set[str] = set()
    for sql in (
        "SELECT project_ref FROM projects",
        "SELECT peer_ref FROM peers",
        "SELECT client_ref FROM mcp_clients",
    ):
        refs.update(r[0] for r in conn.execute(sql))
    return refs


def current_binding(conn: sqlite3.Connection, peer_uid: int | None) -> Binding:
    principal = conn.execute(
        "SELECT id, principal_ref FROM principals ORDER BY id LIMIT 1"
    ).fetchone()
    account = owner_account(conn, principal_id=int(principal[0])) if principal else None
    if principal is None or account is None:
        raise ValueError("log in first: no account exists")
    account_ref = conn.execute(
        "SELECT account_ref FROM accounts WHERE id = ?", (account,)
    ).fetchone()[0]
    security_epoch, _locked = load_security(conn)
    return Binding(peer_uid, principal[1], account_ref, security_epoch)
