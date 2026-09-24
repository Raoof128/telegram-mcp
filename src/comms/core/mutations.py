"""Mutation records: the core persistence API the MutationExecutor drives (comms v0.3 D3, D4).

Every function runs inside the caller's transaction. Results stored for replay hold opaque
refs and codes only, never a provider identity.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from comms.core.canonical import jcs_dumps
from comms.core.storage.db import io_guard

__all__ = [
    "MutationRow",
    "StepRow",
    "add_steps",
    "before_call",
    "find",
    "finish",
    "finish_step",
    "insert",
    "mark_degraded",
    "mark_retried",
    "start_step",
    "steps",
]


@dataclass(frozen=True)
class MutationRow:
    id: int
    op_ref: str
    request_digest: str
    state: str
    ambiguity_policy: str
    retried: bool
    provider_code: str | None
    result: Mapping[str, Any] | None
    actor: str | None


@dataclass(frozen=True)
class StepRow:
    step_no: int
    capability: str
    state: str


def _need_tx(conn: Any) -> None:
    if not conn.in_transaction:
        raise RuntimeError("mutation records are written inside a transaction")


def find(conn: Any, client: str, request_id: str) -> MutationRow | None:
    row = conn.execute(
        "SELECT id, op_ref, request_digest, state, ambiguity_policy, retried, provider_code, result, actor"
        " FROM mutations WHERE authenticated_client = ? AND request_id = ?",
        (client, request_id),
    ).fetchone()
    if row is None:
        return None
    result = json.loads(row[7]) if row[7] is not None else None
    return MutationRow(row[0], row[1], row[2], row[3], row[4], bool(row[5]), row[6], result, row[8])


def insert(
    conn: Any,
    *,
    op_ref: str,
    client: str,
    request_id: str,
    request_digest: str,
    tool: str,
    scope: str,
    target_refs: Sequence[str],
    actor: str | None,
    retry_class: str,
    ambiguity_policy: str,
    stamp: str,
) -> int:
    _need_tx(conn)
    return int(
        conn.execute(
            "INSERT INTO mutations (op_ref, authenticated_client, request_id, request_digest, tool,"
            " scope, target_refs, actor, retry_class, ambiguity_policy, state, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'IN_FLIGHT', ?)",
            (
                op_ref,
                client,
                request_id,
                request_digest,
                tool,
                scope,
                json.dumps(sorted(target_refs)),
                actor,
                retry_class,
                ambiguity_policy,
                stamp,
            ),
        ).lastrowid
    )


def add_steps(conn: Any, mutation_id: int, capabilities: Sequence[str]) -> None:
    _need_tx(conn)
    for step_no, capability in enumerate(capabilities, start=1):
        conn.execute(
            "INSERT INTO mutation_steps (mutation_id, step_no, capability, state) VALUES (?, ?, ?, 'PENDING')",
            (mutation_id, step_no, capability),
        )


def steps(conn: Any, mutation_id: int) -> list[StepRow]:
    return [
        StepRow(r[0], r[1], r[2])
        for r in conn.execute(
            "SELECT step_no, capability, state FROM mutation_steps WHERE mutation_id = ? ORDER BY step_no",
            (mutation_id,),
        )
    ]


def start_step(conn: Any, mutation_id: int, step_no: int, request_key: str) -> None:
    """A42: the step is IN_FLIGHT and its provider request key durable before the call."""
    _need_tx(conn)
    conn.execute(
        "UPDATE mutation_steps SET state = CASE WHEN state = 'PENDING' THEN 'IN_FLIGHT' ELSE state END,"
        " provider_request_key = coalesce(provider_request_key, ?) WHERE mutation_id = ? AND step_no = ?",
        (request_key, mutation_id, step_no),
    )


def finish_step(conn: Any, mutation_id: int, step_no: int, state: str, code: str | None) -> None:
    _need_tx(conn)
    conn.execute(
        "UPDATE mutation_steps SET state = ?, provider_code = ? WHERE mutation_id = ? AND step_no = ?",
        (state, code, mutation_id, step_no),
    )


def finish(
    conn: Any, mutation_id: int, state: str, code: str | None, result: Mapping[str, Any], stamp: str
) -> str:
    """Record the outcome and its result; returns the result digest."""
    _need_tx(conn)
    encoded = jcs_dumps(dict(result))
    digest = hashlib.sha256(encoded).hexdigest()
    conn.execute(
        "UPDATE mutations SET state = ?, provider_code = ?, result = ?, result_digest = ?, finished_at = ?"
        " WHERE id = ?",
        (state, code, encoded.decode(), digest, stamp, mutation_id),
    )
    return digest


def mark_retried(conn: Any, mutation_id: int) -> None:
    _need_tx(conn)
    conn.execute("UPDATE mutations SET retried = 1 WHERE id = ?", (mutation_id,))


def mark_degraded(conn: Any, mutation_id: int) -> None:
    _need_tx(conn)
    conn.execute("UPDATE mutations SET audit_status = 'DEGRADED' WHERE id = ?", (mutation_id,))


def before_call(conn: Any) -> None:
    """No provider call is ever made with a comms.db transaction open (R18)."""
    io_guard(conn)
