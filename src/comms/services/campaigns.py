"""Campaign services over the 5b-4 core (comms v0.3 Task D15; P §30, G16).

The core's semantics stay authoritative; this layer adds the replay contract. **Every** write —
create, set content, set targets, validate, schedule, unschedule, send, cancel, retry, resolve —
runs through ``MutationExecutor.local`` with the caller's ``req_`` id, so the mutation record,
the campaign effect and its audit event commit in one transaction (the core's ``*_in_tx``
bodies), and a replay returns the stored result. For a send or schedule that result is the
generation, the send's natural identity. A refused write rolls back whole: no record, no effect.

Reads (get, list, status, preview, delivery_report) carry refs, states, counts and digests —
never a delivery identity, a payload or a body — and page with an opaque numeric cursor.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from datetime import datetime
from typing import Any, Literal, cast

from comms.core import timeutil
from comms.core.audit.writer import AuditTx, AuditWriter
from comms.core.campaigns import drafts, reports
from comms.core.campaigns.resolve import resolve_targets
from comms.core.delivery import freeze, operations
from comms.core.delivery.commitment import CommitContext
from comms.core.delivery.transport import DeliveryTransport
from comms.core.errors import CommsError
from comms.services.local import effect, mapped, next_cursor, page_args, run_local
from comms.services.mutations import CallContext, MutationExecutor

__all__ = ["CampaignService"]

Verdict = Literal["sent", "not_sent"]
_CONTENT_KEYS = frozenset({"canonical", "fa", "en", "links", "media"})


class CampaignService:
    def __init__(
        self,
        writer: AuditWriter,
        executor: MutationExecutor,
        transports: Mapping[str, DeliveryTransport],
        *,
        commit: Callable[[], CommitContext],
    ) -> None:
        self._writer, self._executor = writer, executor
        self._transports, self._commit = dict(transports), commit

    # -- writes ---------------------------------------------------------------------------

    def create(self, ctx: CallContext, title: str, request_id: str) -> dict[str, Any]:
        if not isinstance(title, str) or not title.strip() or len(title) > 200:
            raise CommsError("INVALID_ARGUMENT")
        return run_local(
            self._executor,
            ctx,
            "comms_campaign_create",
            {},
            {"title": title},
            request_id,
            lambda tx: {"campaign": drafts.create_campaign_in_tx(tx.conn, title, now=tx.now)},
        )

    def set_content(
        self, ctx: CallContext, cmp: str, content: Mapping[str, Any], request_id: str
    ) -> dict[str, Any]:
        if not isinstance(content, Mapping) or not content or not set(content) <= _CONTENT_KEYS:
            raise CommsError("INVALID_ARGUMENT")
        fields = dict(content)
        return run_local(
            self._executor,
            ctx,
            "comms_campaign_set_content",
            {"campaign": cmp},
            fields,
            request_id,
            effect(lambda tx: drafts.set_content_in_tx(tx.conn, cmp, **fields, now=tx.now)),
        )

    def set_targets(
        self,
        ctx: CallContext,
        cmp: str,
        targets: Mapping[str, Sequence[str]],
        transports: Sequence[str],
        request_id: str,
    ) -> dict[str, Any]:
        if not isinstance(transports, Sequence) or isinstance(transports, str):
            raise CommsError("INVALID_ARGUMENT")
        if not isinstance(targets, Mapping):
            raise CommsError("INVALID_ARGUMENT")
        wanted = frozenset(transports)
        shaped = {k: list(v) for k, v in targets.items()}
        return run_local(
            self._executor,
            ctx,
            "comms_campaign_set_targets",
            {"campaign": cmp},
            {"targets": shaped, "transports": sorted(wanted)},
            request_id,
            effect(lambda tx: drafts.set_targets_in_tx(tx.conn, cmp, shaped, wanted, now=tx.now)),
        )

    def validate(self, ctx: CallContext, cmp: str, request_id: str) -> dict[str, Any]:
        return run_local(
            self._executor,
            ctx,
            "comms_campaign_validate",
            {"campaign": cmp},
            {},
            request_id,
            effect(lambda tx: drafts.validate_in_tx(tx.conn, cmp, now=tx.now)),
        )

    def schedule(self, ctx: CallContext, cmp: str, at: datetime, request_id: str) -> dict[str, Any]:
        if not isinstance(at, datetime) or at.tzinfo is None:
            raise CommsError("INVALID_ARGUMENT")
        audited = self._commit()
        return run_local(
            self._executor,
            ctx,
            "comms_campaign_schedule",
            {"campaign": cmp},
            {"at": timeutil.iso(at)},
            request_id,
            lambda tx: {
                "generation": freeze.schedule_in_tx(
                    tx, cmp, at, self._transports, now=tx.now, audited=audited
                )
            },
        )

    def unschedule(self, ctx: CallContext, cmp: str, request_id: str) -> dict[str, Any]:
        return run_local(
            self._executor,
            ctx,
            "comms_campaign_unschedule",
            {"campaign": cmp},
            {},
            request_id,
            effect(lambda tx: freeze.unschedule_in_tx(tx.conn, cmp, now=tx.now)),
        )

    def send(self, ctx: CallContext, cmp: str, request_id: str) -> dict[str, Any]:
        audited = self._commit()
        return run_local(
            self._executor,
            ctx,
            "comms_campaign_send",
            {"campaign": cmp},
            {},
            request_id,
            lambda tx: {
                "generation": freeze.send_in_tx(
                    tx, cmp, self._transports, now=tx.now, audited=audited
                )
            },
        )

    def cancel(self, ctx: CallContext, cmp: str, request_id: str) -> dict[str, Any]:
        def apply(tx: AuditTx) -> dict[str, Any]:
            report = freeze.cancel_in_tx(tx.conn, cmp, now=tx.now)
            return {
                "cancelled_before_send": report.cancelled_before_send,
                "already_sent": report.already_sent,
                "currently_in_flight": report.currently_in_flight,
            }

        return run_local(
            self._executor, ctx, "comms_campaign_cancel", {"campaign": cmp}, {}, request_id, apply
        )

    def retry_failed(self, ctx: CallContext, cmp: str, request_id: str) -> dict[str, Any]:
        def apply(tx: AuditTx) -> dict[str, Any]:
            report = operations.retry_failed_in_tx(tx.conn, cmp, now=tx.now)
            return {"requeued": report.requeued, "retry_exhausted": report.retry_exhausted}

        return run_local(
            self._executor,
            ctx,
            "comms_campaign_comms_campaign_retry_failed",
            {"campaign": cmp},
            {},
            request_id,
            apply,
        )

    def resolve_unknown(
        self, ctx: CallContext, job: str, verdict: str, request_id: str
    ) -> dict[str, Any]:
        if verdict not in ("sent", "not_sent"):
            raise CommsError("INVALID_ARGUMENT")
        return run_local(
            self._executor,
            ctx,
            "comms_campaign_resolve_unknown",
            {"job": job},
            {"verdict": verdict},
            request_id,
            effect(
                lambda tx: operations.resolve_outcome_in_tx(
                    tx.conn, job, cast(Verdict, verdict), now=tx.now
                )
            ),
        )

    # -- reads ----------------------------------------------------------------------------

    def get(self, cmp: str) -> dict[str, Any]:
        return mapped(lambda: reports.campaign_view(self._writer.conn, cmp))

    def status(self, cmp: str) -> dict[str, Any]:
        def view() -> dict[str, Any]:
            campaign = reports.campaign_view(self._writer.conn, cmp)
            counts = reports.job_counts(self._writer.conn, cmp) if campaign["generation"] else {}
            keys = ("campaign", "lifecycle", "summary", "generation", "send_at")
            return {**{k: campaign[k] for k in keys}, "jobs": counts}

        return mapped(view)

    def list(self, *, limit: int = 20, cursor: str | None = None) -> dict[str, Any]:
        size, before = page_args(limit, cursor)
        items, more = reports.list_campaigns(self._writer.conn, limit=size, before=before)
        return {"items": items, "next_cursor": next_cursor(more)}

    def preview(self, cmp: str) -> dict[str, Any]:
        """Who a send would reach, as counts and a digest: never an identity or the body."""

        def view() -> dict[str, Any]:
            campaign = drafts.load(self._writer.conn, cmp)
            targets, transports = drafts.targets_of(campaign)
            candidates = resolve_targets(self._writer.conn, targets, transports)
            by_transport: dict[str, int] = {}
            for candidate in candidates:
                by_transport[candidate.transport] = by_transport.get(candidate.transport, 0) + 1
            return {
                "campaign": cmp,
                "lifecycle": campaign["lifecycle"],
                "recipients": len(candidates),
                "by_transport": by_transport,
                "target_digest": freeze.target_digest(targets, transports),
            }

        return mapped(view)

    def delivery_report(
        self, cmp: str, *, limit: int = 50, cursor: str | None = None
    ) -> dict[str, Any]:
        size, before = page_args(limit, cursor)
        items, more = mapped(
            lambda: reports.job_page(self._writer.conn, cmp, limit=size, before=before)
        )
        return {"campaign": cmp, "items": items, "next_cursor": next_cursor(more)}
