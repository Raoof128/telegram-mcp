"""policy explain / simulate / diff (spec §33.1; Phase-5 design §2.3).

Read-only for committed state. ``simulate`` runs the real ``plan``/``apply``
of a policy, project or client mutation under a rolled-back savepoint, and
stages the semantic diff, bound to who asked and to what it was computed
against.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Mapping
from typing import Any

from telegram_mcp.authority.effective import diff_rows, normalize_new_refs
from telegram_mcp.authority.staging import StagingRegistry
from telegram_mcp.ipc.admin import ADMIN_PEER
from telegram_mcp.ipc.handlers._wrapper import Handler, TxCommand, simulate_tx
from telegram_mcp.storage.effective_access import (
    base_digest,
    current_binding,
    explain,
    known_refs,
    snapshot,
)

__all__ = ["policy_handlers"]


def _bare(args: Mapping[str, Any], allowed: set[str]) -> dict[str, Any]:
    body = {k: v for k, v in args.items() if k != "presence"}
    if set(body) - allowed:
        raise ValueError("unknown argument")
    return body


def _peer_uid() -> int | None:
    peer = ADMIN_PEER.get()
    return None if peer is None else peer.uid


def policy_handlers(
    conn: sqlite3.Connection,
    *,
    registry: StagingRegistry,
    simulatable: Mapping[str, TxCommand[Any, Any]],
) -> dict[str, Handler]:
    def explain_(args: dict[str, Any]) -> dict[str, Any]:
        body = _bare(args, {"client_ref", "project_ref", "peer_ref"})
        client, project = body.get("client_ref"), body.get("project_ref")
        if not isinstance(client, str) or not isinstance(project, str):
            raise ValueError("client_ref and project_ref are required")  # noqa: TRY004 -- uniform ValueError on admin validation
        peer = body.get("peer_ref")
        if peer is not None and not isinstance(peer, str):
            raise ValueError("peer_ref must be a string")
        return {"rows": explain(conn, client_ref=client, project_ref=project, peer_ref=peer)}

    def simulate(args: dict[str, Any]) -> dict[str, Any]:
        body = _bare(args, {"command", "args"})
        command = body.get("command")
        inner = body.get("args", {})
        if not isinstance(command, str) or command not in simulatable:
            raise ValueError("command cannot be simulated")
        if not isinstance(inner, dict):
            raise ValueError("args must be an object")  # noqa: TRY004 -- uniform ValueError on admin validation
        binding = current_binding(conn, _peer_uid())
        base = base_digest(conn)
        known = known_refs(conn)
        before, after = simulate_tx(conn, simulatable[command], inner, snapshot)
        diff = normalize_new_refs(diff_rows(before, after), known)
        handle = registry.stage(
            binding=binding,
            base_digest=base,
            payload={"command": command, "args": inner},
            diff=diff,
        )
        return {"staged": handle, "diff": diff}

    def diff_(args: dict[str, Any]) -> dict[str, Any]:
        body = _bare(args, {"staged"})
        handle = body.get("staged")
        if not isinstance(handle, str):
            raise ValueError("staged must be a tps_ handle")  # noqa: TRY004 -- uniform ValueError on admin validation
        staged = registry.get(
            handle, binding=current_binding(conn, _peer_uid()), current_base=base_digest(conn)
        )
        return {"staged": staged.handle, "diff": dict(staged.diff)}

    return {"policy explain": explain_, "policy simulate": simulate, "policy diff": diff_}
