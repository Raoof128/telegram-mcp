"""The one local write path and its helpers, shared by the campaign and directory services
(comms v0.3 Tasks D15–D16; G16).

``run_local`` runs a core ``*_in_tx`` write through ``MutationExecutor.local``, so the mutation
record, the effect and its audit event commit together and a replay returns the stored result.
``mapped`` turns core refusals into service errors: an unknown ref is ``NOT_FOUND``, any other
refusal ``INVALID_ARGUMENT``. Pages take a limit and an opaque numeric cursor.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from comms.core.audit.writer import AuditTx
from comms.core.campaigns.directory import DirectoryError, DirectoryNotFound
from comms.core.campaigns.drafts import LifecycleError, NotFound
from comms.core.errors import CommsError
from comms.services.mutations import CallContext, MutationExecutor, MutationOutcome

__all__ = ["MAX_PAGE", "effect", "mapped", "next_cursor", "page_args", "run_local"]

MAX_PAGE = 100


def mapped(call: Callable[[], Any]) -> Any:
    """Core refusals as service errors: unknown refs are NOT_FOUND, the rest INVALID_ARGUMENT."""
    try:
        return call()
    except CommsError:
        raise
    except (NotFound, DirectoryNotFound):
        raise CommsError("NOT_FOUND") from None
    except (LifecycleError, DirectoryError, ValueError):
        raise CommsError("INVALID_ARGUMENT") from None


def effect(run: Callable[[AuditTx], object]) -> Callable[[AuditTx], Mapping[str, Any]]:
    """A core write with nothing to report: its result is empty."""

    def apply(tx: AuditTx) -> Mapping[str, Any]:
        run(tx)
        return {}

    return apply


def run_local(
    executor: MutationExecutor,
    ctx: CallContext,
    tool: str,
    targets: Mapping[str, Any],
    args: Mapping[str, Any],
    request_id: str,
    apply: Callable[[AuditTx], Mapping[str, Any]],
) -> dict[str, Any]:
    outcome: MutationOutcome = mapped(
        lambda: executor.local(ctx, tool, targets, args, request_id, apply)
    )
    return {**outcome.result, "op_ref": outcome.op_ref, "replayed": outcome.replayed}


def page_args(limit: object, cursor: object) -> tuple[int, int | None]:
    if type(limit) is not int or not 1 <= limit <= MAX_PAGE:
        raise CommsError("INVALID_ARGUMENT")
    if cursor is None:
        return limit, None
    if not (isinstance(cursor, str) and cursor.isdigit() and len(cursor) <= 18):
        raise CommsError("INVALID_ARGUMENT")
    return limit, int(cursor)


def next_cursor(value: int | None) -> str | None:
    return None if value is None else str(value)
