"""Mutation recovery after a crash (comms v0.3 Task D5; G14–G16, A8, A41).

Run once at startup, before any executor. It never calls a provider; it settles what the
records prove and leaves the rest to a replay of the same request:

- every step still ``PENDING`` → nothing was attempted: the steps and the mutation are
  ``FAILED`` with ``NOT_ATTEMPTED`` (this includes a mutation whose first anchor failed);
- a step left ``IN_FLIGHT`` → its call may have reached the provider: the step and the
  mutation are ``OUTCOME_UNKNOWN`` (a replay re-invokes it once only under ``retry_same_key``);
- a ``FAILED`` or ``OUTCOME_UNKNOWN`` step settles the mutation the same way;
- every step ``SUCCEEDED`` → ``SUCCEEDED`` (the known outcome of a degraded recording);
- steps that succeeded followed by ``PENDING`` ones → a saga stopped between steps: left
  ``IN_FLIGHT`` for the replay to resume.
"""

from __future__ import annotations

from dataclasses import dataclass

from comms.core import mutations as records
from comms.core.audit.writer import AuditTx, AuditWriter

__all__ = ["MutationRecovery", "recover_mutations"]


@dataclass(frozen=True)
class MutationRecovery:
    settled: int = 0
    not_attempted: int = 0
    unknown: int = 0
    resumable: int = 0


def _step_event(
    tx: AuditTx, op_ref: str, step: records.StepRow, state: str, code: str | None
) -> None:
    tx.append(
        "admin.mutation_step",
        subject_ref=op_ref,
        payload={
            "step_no": step.step_no,
            "capability": step.capability,
            "state": state,
            "provider_code": code,
        },
    )


def _finish(tx: AuditTx, mutation_id: int, op_ref: str, state: str, code: str | None) -> None:
    digest = records.finish(
        tx.conn, mutation_id, state, code, {"state": state, "code": code}, tx.stamp
    )
    tx.append(
        "admin.mutation_finished",
        subject_ref=op_ref,
        subject_digest=digest,
        payload={"state": state, "provider_code": code},
    )


def recover_mutations(writer: AuditWriter) -> MutationRecovery:
    settled = not_attempted = unknown = resumable = 0
    for mutation_id, op_ref in records.interrupted(writer.conn):
        steps = records.steps(writer.conn, mutation_id)
        states = [s.state for s in steps]
        if all(state == "PENDING" for state in states):
            with writer.transaction() as tx:
                for step in steps:
                    records.finish_step(
                        tx.conn, mutation_id, step.step_no, "FAILED", "NOT_ATTEMPTED"
                    )
                    _step_event(tx, op_ref, step, "FAILED", "NOT_ATTEMPTED")
                _finish(tx, mutation_id, op_ref, "FAILED", "NOT_ATTEMPTED")
            not_attempted += 1
        elif "IN_FLIGHT" in states or "OUTCOME_UNKNOWN" in states:
            with writer.transaction() as tx:
                for step in steps:
                    if step.state == "IN_FLIGHT":
                        records.finish_step(
                            tx.conn, mutation_id, step.step_no, "OUTCOME_UNKNOWN", None
                        )
                        _step_event(tx, op_ref, step, "OUTCOME_UNKNOWN", None)
                _finish(tx, mutation_id, op_ref, "OUTCOME_UNKNOWN", None)
            unknown += 1
        elif "FAILED" in states:
            code = next(s.provider_code for s in steps if s.state == "FAILED")
            with writer.transaction() as tx:
                _finish(tx, mutation_id, op_ref, "FAILED", code)
            settled += 1
        elif all(state == "SUCCEEDED" for state in states):
            with writer.transaction() as tx:
                _finish(tx, mutation_id, op_ref, "SUCCEEDED", None)
            settled += 1
        else:
            resumable += 1
    return MutationRecovery(settled, not_attempted, unknown, resumable)
