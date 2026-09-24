"""MutationExecutor: one replay contract for every write (comms v0.3 Task D4; G14–G16, A27,
A28, A41, A42, A8).

A write is identified by ``(authenticated client, request_id)``; the request digest binds its
arguments. A replay with the same digest returns the recorded outcome; another digest is
``REQUEST_ID_REUSE``.

``local``: one audited transaction holds the replay check, the change, the mutation row and its
events, so they commit together or not at all.

``provider``: the mutation and its steps (a single call is a one-step saga; a compound
operation is its ``SEMANTICS`` steps) are recorded ``IN_FLIGHT`` before any call. Each step is
marked ``IN_FLIGHT`` with its provider request key durable before its call (A42); only the
call's own exception becomes ``OUTCOME_UNKNOWN``; each outcome is recorded through the writer;
a failed or unknown step stops the saga. On replay: a finished mutation returns its record; an
interrupted one resumes — a step left ``IN_FLIGHT`` or ``OUTCOME_UNKNOWN`` is re-invoked only
when its semantics are ``retry_same_key``, and a mutation is re-invoked at most once. No new
mutation and no new provider call starts while the audit trail is degraded; recording a call
already made always proceeds (A8). ``crash_at`` is a test seam only.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

from comms.core import domains, refs
from comms.core import mutations as records
from comms.core.audit.integrity import AuditIntegrityDegraded, require_not_degraded
from comms.core.audit.writer import AnchorFailed, AuditTx, AuditWriter
from comms.core.campaigns.directory import destination_id
from comms.core.canonical import jcs_dumps
from comms.core.errors import CommsError
from comms.core.objects import KIND_PREFIX, object_ref
from comms.core.providers.capability import Capability
from comms.core.providers.protocols import (
    AdminOperations,
    ProviderResult,
    ProviderTarget,
    SemanticOperation,
)
from comms.core.providers.semantics import SEMANTICS

__all__ = [
    "CRASH_POINTS",
    "CallContext",
    "MutationCrash",
    "MutationExecutor",
    "MutationOutcome",
    "op_key",
    "request_digest",
]

CRASH_POINTS = ("before_call", "after_call", "after_record", "between_steps")


class MutationCrash(BaseException):
    """Raised only by the ``crash_at`` seam; BaseException so no handler swallows it."""


@dataclass(frozen=True)
class CallContext:
    client_ref: str


@dataclass(frozen=True)
class MutationOutcome:
    op_ref: str
    state: str
    code: str | None = None
    result: Mapping[str, Any] = field(default_factory=dict)
    replayed: bool = False


def request_digest(tool: str, args: Mapping[str, Any], targets: Mapping[str, Any]) -> str:
    body = jcs_dumps({"args": dict(args), "targets": dict(targets), "tool": tool})
    return hashlib.sha256(domains.REQUEST_DIGEST + body).hexdigest()


def op_key(client_ref: str, request_id: str) -> str:
    body = jcs_dumps({"client": client_ref, "request": request_id})
    return hashlib.sha256(domains.ADMIN_OP + body).hexdigest()


def _check_request(request_id: object) -> None:
    """A caller's request id is a well-formed ``req_`` ref, refused before any storage."""
    try:
        refs.check(request_id, "request")
    except ValueError:
        raise CommsError("INVALID_ARGUMENT") from None


def _outcome(row: records.MutationRow, *, replayed: bool) -> MutationOutcome:
    return MutationOutcome(row.op_ref, row.state, row.provider_code, row.result or {}, replayed)


class MutationExecutor:
    def __init__(
        self,
        writer: AuditWriter,
        admin: Mapping[str, AdminOperations],
        *,
        crash_at: str | None = None,
    ) -> None:
        if crash_at is not None and crash_at not in CRASH_POINTS:
            raise ValueError("unknown crash point")
        self._writer, self._admin, self._crash_at = writer, dict(admin), crash_at

    def _crash(self, point: str) -> None:
        if self._crash_at == point:
            raise MutationCrash(point)

    # -- local writes ---------------------------------------------------------------------

    def local(
        self,
        ctx: CallContext,
        tool: str,
        targets: Mapping[str, Any],
        args: Mapping[str, Any],
        request_id: str,
        apply: Callable[[AuditTx], Mapping[str, Any]],
    ) -> MutationOutcome:
        _check_request(request_id)
        digest = request_digest(tool, args, targets)
        with self._writer.transaction() as tx:
            existing = records.find(tx.conn, ctx.client_ref, request_id)
            if existing is not None:
                if existing.request_digest != digest:
                    raise CommsError("REQUEST_ID_REUSE")
                return _outcome(existing, replayed=True)
            self._require_healthy(tx.conn)
            op_ref = refs.mint("operation")
            mutation_id = records.insert(
                tx.conn,
                op_ref=op_ref,
                client=ctx.client_ref,
                request_id=request_id,
                request_digest=digest,
                tool=tool,
                scope="local",
                target_refs=[str(v) for v in targets.values()],
                actor=None,
                retry_class="LOCAL",
                ambiguity_policy="resolve_only",
                stamp=tx.stamp,
            )
            tx.append(
                "admin.mutation_started",
                subject_ref=op_ref,
                subject_digest=digest,
                payload={"tool": tool, "scope": "local", "actor": None},
            )
            result = dict(apply(tx))
            result_digest = records.finish(
                tx.conn, mutation_id, "SUCCEEDED", None, result, tx.stamp
            )
            tx.append(
                "admin.mutation_finished",
                subject_ref=op_ref,
                subject_digest=result_digest,
                payload={"state": "SUCCEEDED", "provider_code": None},
            )
        return MutationOutcome(op_ref, "SUCCEEDED", None, result)

    # -- provider writes ------------------------------------------------------------------

    def provider(
        self,
        ctx: CallContext,
        tool: str,
        target: ProviderTarget,
        op: SemanticOperation,
        request_id: str,
        *,
        object_kind: str | None = None,
    ) -> MutationOutcome:
        """``object_kind`` names what a CREATE makes: its provider ref becomes a durable object
        ref in the result, and a success without one is ``OUTCOME_UNKNOWN`` (A19)."""
        _check_request(request_id)
        if object_kind is not None and object_kind not in KIND_PREFIX:
            raise CommsError("INVALID_ARGUMENT")
        semantics = SEMANTICS.get((op.capability, target.actor))
        adapter = self._admin.get(target.actor)
        if semantics is None or adapter is None or semantics.retry_class == "READ":
            raise CommsError("PROVIDER_UNSUPPORTED")
        for index, capability in enumerate(semantics.steps or (op.capability,)):
            extra = semantics.step_args[index] if semantics.step_args else {}
            try:  # malformed arguments are refused before anything is recorded
                adapter.validate(SemanticOperation(capability, {**op.args, **extra}), target)
            except NotImplementedError:
                raise CommsError("PROVIDER_UNSUPPORTED") from None
            except ValueError:
                raise CommsError("INVALID_ARGUMENT") from None
        targets = {
            "actor": target.actor,
            "capability": op.capability.value,
            "destination": target.destination_ref,
        }
        digest = request_digest(tool, op.args, targets)
        mutation_id, op_ref = 0, ""
        try:
            with self._writer.transaction() as tx:
                row = records.find(tx.conn, ctx.client_ref, request_id)
                if row is not None and row.request_digest != digest:
                    raise CommsError("REQUEST_ID_REUSE")
                if row is None:
                    self._require_healthy(tx.conn)
                    op_ref = refs.mint("operation")
                    mutation_id = records.insert(
                        tx.conn,
                        op_ref=op_ref,
                        client=ctx.client_ref,
                        request_id=request_id,
                        request_digest=digest,
                        tool=tool,
                        scope="provider",
                        target_refs=[target.destination_ref],
                        actor=target.actor,
                        retry_class=semantics.retry_class,
                        ambiguity_policy=semantics.ambiguity_policy,
                        stamp=tx.stamp,
                    )
                    records.add_steps(
                        tx.conn, mutation_id, [c.value for c in semantics.steps or (op.capability,)]
                    )
                    tx.append(
                        "admin.mutation_started",
                        subject_ref=op_ref,
                        subject_digest=digest,
                        payload={"tool": tool, "scope": "provider", "actor": target.actor},
                    )
        except AnchorFailed:
            return self._degraded(mutation_id, op_ref, "IN_FLIGHT", False)
        if row is not None:
            if row.state in ("SUCCEEDED", "FAILED"):
                return _outcome(row, replayed=True)
            if row.state == "OUTCOME_UNKNOWN" and (
                row.retried or row.ambiguity_policy != "retry_same_key"
            ):
                return _outcome(row, replayed=True)
            if row.state == "OUTCOME_UNKNOWN":
                with self._writer.transaction() as tx:
                    records.mark_retried(tx.conn, row.id)
            return self._run(
                row.id,
                row.op_ref,
                target,
                op,
                semantics.step_args,
                ctx,
                request_id,
                adapter,
                replayed=True,
                object_kind=object_kind,
            )
        return self._run(
            mutation_id,
            op_ref,
            target,
            op,
            semantics.step_args,
            ctx,
            request_id,
            adapter,
            replayed=False,
            object_kind=object_kind,
        )

    def _run(
        self,
        mutation_id: int,
        op_ref: str,
        target: ProviderTarget,
        op: SemanticOperation,
        step_args: tuple[Mapping[str, Any], ...],
        ctx: CallContext,
        request_id: str,
        adapter: AdminOperations,
        *,
        replayed: bool,
        object_kind: str | None = None,
    ) -> MutationOutcome:
        key = op_key(ctx.client_ref, request_id)
        conn = self._writer.conn
        created: dict[str, Any] = {}
        steps = records.steps(conn, mutation_id)
        for index, step in enumerate(steps):
            if step.state == "SUCCEEDED":
                continue
            capability = Capability(step.capability)
            policy = SEMANTICS[(capability, target.actor)].ambiguity_policy
            if step.state == "FAILED":
                return self._finish(mutation_id, op_ref, "FAILED", None, replayed)
            if step.state in ("IN_FLIGHT", "OUTCOME_UNKNOWN") and policy != "retry_same_key":
                self._record_step(
                    mutation_id,
                    op_ref,
                    step.step_no,
                    capability,
                    step.state,
                    "OUTCOME_UNKNOWN",
                    None,
                )
                return self._finish(mutation_id, op_ref, "OUTCOME_UNKNOWN", None, replayed)
            if index > 0:
                self._crash("between_steps")
            try:
                require_not_degraded(conn)
            except AuditIntegrityDegraded:
                return MutationOutcome(
                    op_ref, "IN_FLIGHT", "AUDIT_INTEGRITY_DEGRADED", {}, replayed
                )
            self._crash("before_call")
            try:
                with self._writer.transaction() as tx:
                    records.start_step(tx.conn, mutation_id, step.step_no, f"{key}:{step.step_no}")
            except AnchorFailed:
                return self._degraded(mutation_id, op_ref, "IN_FLIGHT", replayed)
            args = {**op.args, **(step_args[index] if step_args else {})}
            records.before_call(conn)
            try:
                result = adapter.invoke(SemanticOperation(capability, args), target, key)
            except Exception:  # noqa: BLE001 -- the ONE boundary where an exception is an outcome
                result = ProviderResult("OUTCOME_UNKNOWN", None)
            self._crash("after_call")
            state = result.outcome
            if object_kind is not None and index == len(steps) - 1 and state == "SUCCEEDED":
                if not result.provider_ref:
                    state = "OUTCOME_UNKNOWN"  # created, but nothing to name it by
                else:
                    created["object_ref"] = object_ref(
                        conn,
                        object_kind,
                        target.transport,
                        target.actor,
                        destination_id(conn, target.destination_ref),
                        result.provider_ref,
                        now=self._writer.now(),
                    )
            try:
                self._record_step(
                    mutation_id, op_ref, step.step_no, capability, "IN_FLIGHT", state, result.code
                )
            except AnchorFailed:
                return self._degraded(mutation_id, op_ref, state, replayed)
            self._crash("after_record")
            if state != "SUCCEEDED":
                return self._finish(mutation_id, op_ref, state, result.code, replayed)
        return self._finish(mutation_id, op_ref, "SUCCEEDED", None, replayed, created)

    def _record_step(
        self,
        mutation_id: int,
        op_ref: str,
        step_no: int,
        capability: Capability,
        current: str,
        state: str,
        code: str | None,
    ) -> None:
        with self._writer.transaction() as tx:
            if current != state:
                records.finish_step(tx.conn, mutation_id, step_no, state, code)
            tx.append(
                "admin.mutation_step",
                subject_ref=op_ref,
                payload={
                    "step_no": step_no,
                    "capability": capability.value,
                    "state": state,
                    "provider_code": code,
                },
            )

    def _finish(
        self,
        mutation_id: int,
        op_ref: str,
        state: str,
        code: str | None,
        replayed: bool,
        extra: Mapping[str, Any] | None = None,
    ) -> MutationOutcome:
        result = {"state": state, "code": code, **(extra or {})}
        try:
            with self._writer.transaction() as tx:
                digest = records.finish(tx.conn, mutation_id, state, code, result, tx.stamp)
                tx.append(
                    "admin.mutation_finished",
                    subject_ref=op_ref,
                    subject_digest=digest,
                    payload={"state": state, "provider_code": code},
                )
        except AnchorFailed:
            return self._degraded(mutation_id, op_ref, state, replayed)
        return MutationOutcome(op_ref, state, code, result, replayed)

    def _degraded(
        self, mutation_id: int, op_ref: str, state: str, replayed: bool
    ) -> MutationOutcome:
        """The record committed but its anchor failed (the latch is set): mark the mutation and
        return the known state with AUDIT_INTEGRITY_DEGRADED; recovery settles it (A8)."""
        with self._writer.transaction() as tx:  # no event appended, so no anchor refresh
            records.mark_degraded(tx.conn, mutation_id)
        return MutationOutcome(op_ref, state, "AUDIT_INTEGRITY_DEGRADED", {}, replayed)

    @staticmethod
    def _require_healthy(conn: Any) -> None:
        try:
            require_not_degraded(conn)
        except AuditIntegrityDegraded:
            raise CommsError("AUDIT_INTEGRITY_DEGRADED") from None
