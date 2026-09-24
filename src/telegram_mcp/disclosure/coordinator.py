"""The disclosure coordinator (design §1.2, §2, §6.5).

The coordinator **sequences**; it does not decide. Authority decisions stay
in ``authority/``, consent in ``consent/``, budgets in ``budget.py``,
integrity in ``audit/``. A second policy engine here would be the failure
this design exists to prevent.

It is also the only thing in the system able to release a sensitive payload.
``DisclosureOutcome`` is either a released payload carrying its committed
receipt, or a refusal — there is no third shape, so a caller cannot obtain
data without a receipt.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol

from telegram_mcp.disclosure.audit.anchor import AnchorError, latch_degraded, write_anchor
from telegram_mcp.disclosure.audit.chain import (
    APPEND_GUARD,
    append_event,
    immediate_transaction,
    mint_event_id,
)
from telegram_mcp.disclosure.budget import BudgetError, buckets_for
from telegram_mcp.disclosure.coverage import CoverageError, coverage_digest, validate_coverage
from telegram_mcp.disclosure.egress import effective_egress_level
from telegram_mcp.disclosure.measure import (
    RECORD_ELEMENT,
    bytes_disclosed,
    records_disclosed,
)
from telegram_mcp.disclosure.provenance import provenance_digest
from telegram_mcp.disclosure.receipts import (
    build_proof_payload,
    mint_disclosure_ref,
    sign_payload,
)
from telegram_mcp.storage.settings import get_setting

__all__ = [
    "DISCLOSURE_STEPS",
    "AuthorityRefusal",
    "ConsentRefusal",
    "DisclosureCoordinator",
    "DisclosureOutcome",
    "RetrievalAdapter",
    "RetrievalRefusal",
]

# Frozen as a declaration the tests assert against. Production calls typed
# functions explicitly rather than dispatching over this tuple: a security
# protocol must not be a dynamically dispatchable plugin chain.
DISCLOSURE_STEPS: tuple[str, ...] = (
    "freeze_arguments",
    "snapshot_authority",
    "estimate_exposure",
    "consent_issue",
    "consent_consume",
    "reserve_budget",
    "retrieve",
    "revalidate_authority",
    "transform_egress",
    "measure_and_prepare_proof",
    "commit_disclosure",
    "refresh_anchor",
)

# A single process-wide guard, acquired BEFORE step 11 and held across step
# 12. Marking the chain ANCHOR_PENDING only after committing would leave a
# window in which a second caller commits and the chain goes two ahead.

_SEARCH_TOOLS = frozenset({"telegram_search_messages", "telegram_cross_project_search"})

_CATALOGUE_TOOLS = frozenset({"telegram_list_projects", "telegram_resolve_project"})

# Keys an adapter may return beside ``data``. They are split off after
# egress: never measured, never signed as data, never schema-validated as data.
_SIDECAR_KEYS = frozenset({"_coverage", "_next_cursor", "_partial"})


class RetrievalRefusal(Exception):
    """Retrieval failed with a frozen §27.1 code (never a Telegram message)."""

    def __init__(self, code: str, retry_after: int | None = None) -> None:
        super().__init__(code)
        self.code = code
        self.retry_after = retry_after


class AuthorityRefusal(Exception):
    """A seam refused before consent, with a frozen §27.1 code."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class ConsentRefusal(Exception):
    """Consent could not be obtained, with a frozen §27.1 code."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _split_sidecar(raw: Mapping[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """Separate adapter side keys from ``data``; an unknown one is an error."""
    side = {k: v for k, v in raw.items() if k.startswith("_")}
    if set(side) - _SIDECAR_KEYS:
        raise ValueError("unknown adapter side key")
    return {k: v for k, v in raw.items() if not k.startswith("_")}, side


class RetrievalAdapter(Protocol):
    """The Phase-4 seam: typed retrieval under an authority snapshot."""

    async def retrieve(
        self, *, tool_name: str, arguments: Mapping[str, Any], snapshot: Any
    ) -> dict[str, Any]:
        """Return the raw records for this call."""
        ...  # pragma: no cover - protocol shape only


@dataclass(frozen=True)
class DisclosureOutcome:
    """Either a released payload with its receipt, or a refusal. Never both."""

    released: bool
    data: dict[str, Any] | None = None
    meta: dict[str, Any] | None = None
    disclosure_ref: str | None = None
    error_code: str | None = None
    retryable: bool = False
    retry_after_seconds: int | None = None


def _now_iso() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


class DisclosureCoordinator:
    """Runs the twelve steps. Sequences; never decides."""

    def __init__(
        self,
        conn: Any,
        *,
        chain_key: bytes,
        disclosure_seed: bytes,
        disclosure_key_id: str,
        anchor_path: Any,
        ledger: Any,
        authority: Any,
        consent: Any,
        transport: Any = None,
        clock: Callable[[], float] = time.time,
        crash_at: str | None = None,
    ) -> None:
        self._conn = conn
        self._chain_key = chain_key
        self._disclosure_seed = disclosure_seed
        self._disclosure_key_id = disclosure_key_id
        self._anchor_path = anchor_path
        self._ledger = ledger
        self._authority = authority
        self._consent = consent
        self._transport = transport
        self._clock = clock
        self._crash_at = crash_at

    def _checkpoint(self, step: str) -> None:
        """Crash-injection seam.

        Not a test-only flag inside a real path: ``crash_at`` is a constructor
        argument that production never supplies, so the branch is dead in a
        real deployment rather than reachable by a caller.
        """
        if self._crash_at == step:
            raise RuntimeError(f"injected crash at {step}")

    # -- step 10 assembly ---------------------------------------------------

    def _prepare_proof(
        self,
        tool_name: str,
        data: Mapping[str, Any],
        snapshot: Any,
        approval: Any,
        coverage: Mapping[str, Any] | None,
        partial: bool,
    ) -> dict[str, Any]:
        records = list(data.get(RECORD_ELEMENT[tool_name], []))
        level = effective_egress_level(records)
        prepared: dict[str, Any] = {
            "disclosure_ref": mint_disclosure_ref(),
            "committed_at": _now_iso(),
            "tool_name": tool_name,
            "effective_egress_level": level,
            "records_disclosed": records_disclosed(tool_name, data),
            "bytes_disclosed": bytes_disclosed(data),
            "partial": partial,
            "canonical_result_provenance_digest": provenance_digest(tool_name, data),
            "canonical_coverage_digest": coverage_digest(coverage),
        }
        payload = build_proof_payload(
            disclosure_ref=prepared["disclosure_ref"],
            principal_ref=snapshot.principal_ref,
            client_ref=snapshot.client_ref,
            account_ref=snapshot.account_ref,
            tool_name=tool_name,
            security_epoch=snapshot.security_epoch,
            policy_epoch=snapshot.policy_epoch,
            project_scope_digest=snapshot.project_scope_digest,
            project_count=snapshot.project_count,
            effective_egress_level=level,
            records_disclosed=prepared["records_disclosed"],
            bytes_disclosed=prepared["bytes_disclosed"],
            partial=prepared["partial"],
            committed_at=prepared["committed_at"],
            consent_key_id=approval.key_id,
            consent_challenge_digest=approval.challenge_sha256,
            canonical_result_provenance_digest=prepared["canonical_result_provenance_digest"],
            canonical_coverage_digest=prepared["canonical_coverage_digest"],
        )
        signed = sign_payload(
            payload,
            private_seed=self._disclosure_seed,
            proof_key_id=self._disclosure_key_id,
        )
        prepared["payload"] = payload
        prepared["signed"] = signed
        prepared["consent_key_id"] = approval.key_id
        prepared["consent_challenge_digest"] = approval.challenge_sha256
        return prepared

    def _insert_receipt(self, prepared: Mapping[str, Any], snapshot: Any) -> None:
        self._conn.execute(
            "INSERT INTO disclosure_receipts (disclosure_ref, committed_at, principal_id,"
            " client_id, account_id, tool_name, security_epoch, policy_epoch,"
            " project_scope_digest, project_count, effective_egress_level, records_disclosed,"
            " bytes_disclosed, partial, commit_status, consent_key_id, consent_challenge_digest,"
            " canonical_result_provenance_digest, canonical_coverage_digest,"
            " proof_payload_sha256, proof_key_id, proof_signature)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'committed', ?, ?, ?, ?, ?, ?, ?)",
            (
                prepared["disclosure_ref"],
                prepared["committed_at"],
                snapshot.principal_id,
                snapshot.client_id,
                snapshot.account_id,
                prepared["tool_name"],
                snapshot.security_epoch,
                snapshot.policy_epoch,
                snapshot.project_scope_digest,
                snapshot.project_count,
                prepared["effective_egress_level"],
                prepared["records_disclosed"],
                prepared["bytes_disclosed"],
                int(prepared["partial"]),
                prepared["consent_key_id"],
                prepared["consent_challenge_digest"],
                prepared["canonical_result_provenance_digest"],
                prepared["canonical_coverage_digest"],
                prepared["signed"]["proof_payload_sha256"],
                prepared["signed"]["proof_key_id"],
                prepared["signed"]["proof_signature"],
            ),
        )

    def _audit_event(self, prepared: Mapping[str, Any], snapshot: Any) -> dict[str, Any]:
        return {
            "event_id": mint_event_id(),
            "ts": prepared["committed_at"],
            "tool_name": prepared["tool_name"],
            "principal_ref": snapshot.principal_ref,
            "client_ref": snapshot.client_ref,
            "account_ref": snapshot.account_ref,
            "peer_ref": getattr(snapshot, "peer_ref", None),
            "project_ref": getattr(snapshot, "project_ref", None),
            "project_count": snapshot.project_count,
            "policy_epoch": snapshot.policy_epoch,
            "result_count": prepared["records_disclosed"],
            "duration_ms": 0,
            "telegram_rpc_count": 0,
            "status": "ok",
            "error_code": None,
            "disclosure_ref": prepared["disclosure_ref"],
        }

    def _build_meta(
        self, prepared: Mapping[str, Any], coverage: Any, next_cursor: str | None
    ) -> dict[str, Any]:
        catalogue = prepared["tool_name"] in _CATALOGUE_TOOLS
        return {
            "source": "gateway" if catalogue else "telegram",
            "content_trust": (
                "non_instructional_gateway_metadata" if catalogue else "untrusted_external_content"
            ),
            "truncated": False,
            "partial": bool(prepared["partial"]),
            "next_cursor": next_cursor,
            "disclosure": {
                "receipt_ref": prepared["disclosure_ref"],
                "proof_key_id": prepared["signed"]["proof_key_id"],
                "proof_signature": prepared["signed"]["proof_signature"],
                "proof_payload_sha256": prepared["signed"]["proof_payload_sha256"],
                "proof_payload": prepared["payload"],
            },
            "coverage": coverage,
        }

    def _latch_degraded(self, disclosure_ref: str, reason: str) -> None:
        latch_degraded(self._conn, reason=reason, disclosure_ref=disclosure_ref)

    # -- the transaction ----------------------------------------------------

    async def disclose(
        self,
        *,
        tool_name: str,
        arguments: Mapping[str, Any],
        adapter: RetrievalAdapter,
        principal: Any = None,
    ) -> DisclosureOutcome:
        """Run the twelve steps. Returns a payload with its receipt, or a refusal."""
        if get_setting(self._conn, "audit.integrity_degraded"):
            return DisclosureOutcome(
                released=False, error_code="AUDIT_INTEGRITY_UNAVAILABLE", retryable=False
            )

        reservation = None
        try:
            # ---- steps 1-6: nothing has been retrieved ----------------------
            try:
                self._checkpoint("freeze_arguments")
                request = self._authority.freeze_arguments(
                    tool_name, arguments, principal=principal
                )

                self._checkpoint("snapshot_authority")
                snapshot = self._authority.snapshot(tool_name, request)
            except AuthorityRefusal as refusal:
                return DisclosureOutcome(released=False, error_code=refusal.code, retryable=False)

            self._checkpoint("estimate_exposure")
            worst_case = self._authority.worst_case_buckets(tool_name, snapshot)
            decision, projected = self._ledger.consult(worst_case)
            if decision == "refuse":
                return DisclosureOutcome(
                    released=False, error_code="EXPOSURE_BUDGET_EXCEEDED", retryable=True
                )

            approval = None
            for _attempt in range(2):  # exactly one automatic restart (design §5.5)
                try:
                    self._checkpoint("consent_issue")
                    challenge = await self._consent.issue(
                        tool_name=tool_name,
                        snapshot=snapshot,
                        request=request,
                        tier=decision,
                        projected=projected,
                        worst_case=worst_case,
                    )

                    self._checkpoint("consent_consume")
                    approval = await self._consent.consume(challenge)
                except ConsentRefusal as refusal:
                    return DisclosureOutcome(
                        released=False, error_code=refusal.code, retryable=False
                    )
                if approval is None:
                    return DisclosureOutcome(
                        released=False, error_code="CONSENT_DENIED", retryable=False
                    )

                self._checkpoint("reserve_budget")
                # consult and reserve run back to back with no await between
                # them: on the daemon's single asyncio thread nothing can
                # interleave, which is what "under the reservation lock"
                # means here (Phase-3 design §5.4).
                decision, projected = self._ledger.consult(worst_case)
                if decision == "refuse":
                    return DisclosureOutcome(
                        released=False, error_code="EXPOSURE_BUDGET_EXCEEDED", retryable=True
                    )
                if not self._consent.snapshot_matches(approval, tier=decision, projected=projected):
                    continue  # conditions moved: re-prompt with the new quantities
                try:
                    reservation = self._ledger.reserve(
                        client_id=snapshot.client_id,
                        security_epoch=snapshot.security_epoch,
                        project_scope_digest=snapshot.project_scope_digest,
                        consent_challenge_digest=approval.challenge_sha256,
                        request_nonce=approval.nonce,
                        worst_case=worst_case,
                        ttl_seconds=60,
                    )
                except BudgetError:
                    return DisclosureOutcome(
                        released=False, error_code="EXPOSURE_BUDGET_EXCEEDED", retryable=True
                    )
                break
            else:
                # Second divergence: stop an agent spinning through prompts.
                return DisclosureOutcome(
                    released=False, error_code="CONSENT_UNAVAILABLE", retryable=False
                )

            # ==== SECURITY BARRIER ==========================================
            self._checkpoint("retrieve")
            # Authority can move while the owner looks at the prompt. Step 8
            # would catch it, but only after Telegram was already asked; a
            # read the owner has since revoked must not reach Telegram at all.
            moved = self._authority.revalidate(snapshot)
            if moved is not None:
                return DisclosureOutcome(released=False, error_code=moved, retryable=False)
            try:
                raw = await adapter.retrieve(
                    tool_name=tool_name, arguments=request.validated_args, snapshot=snapshot
                )
            except RetrievalRefusal as refusal:
                return DisclosureOutcome(  # retryability comes from results.RETRYABILITY
                    released=False,
                    error_code=refusal.code,
                    retry_after_seconds=refusal.retry_after,
                )

            self._checkpoint("revalidate_authority")
            moved = self._authority.revalidate(snapshot)
            if moved is not None:
                return DisclosureOutcome(released=False, error_code=moved, retryable=False)

            self._checkpoint("transform_egress")
            try:
                data, side = _split_sidecar(self._authority.apply_egress(raw, snapshot))
            except ValueError:
                return DisclosureOutcome(
                    released=False, error_code="INTERNAL_ERROR", retryable=False
                )
            coverage = side.get("_coverage") if tool_name in _SEARCH_TOOLS else None
            next_cursor = side.get("_next_cursor")
            partial = bool(snapshot.partial) or bool(side.get("_partial"))
            if tool_name in _SEARCH_TOOLS:
                # §23D and §14.1A: a search result must carry a coverage object
                # that agrees with itself, with the cursor, with meta.partial and
                # with the records actually disclosed; otherwise nothing is signed.
                try:
                    if coverage is None or coverage["hits_returned"] != records_disclosed(
                        tool_name, data
                    ):
                        raise CoverageError("coverage does not describe this result")
                    validate_coverage(coverage, next_cursor=next_cursor, partial=partial)
                except (CoverageError, KeyError, TypeError):
                    return DisclosureOutcome(
                        released=False, error_code="PROOF_GENERATION_FAILED", retryable=False
                    )

            self._checkpoint("measure_and_prepare_proof")
            prepared = self._prepare_proof(tool_name, data, snapshot, approval, coverage, partial)
            actual = buckets_for(tool_name, data, client_id=snapshot.client_id)

            # ==== DISCLOSURE BARRIER: steps 11 and 12 under one guard =======
            with APPEND_GUARD:
                self._checkpoint("commit_disclosure")
                try:
                    with immediate_transaction(self._conn):
                        # ONE transaction: ledger rows, receipt row, exactly one
                        # audit append (frozen spec §23A.3).
                        self._insert_receipt(prepared, snapshot)
                        self._ledger.commit(
                            reservation,
                            disclosure_ref=prepared["disclosure_ref"],
                            actual=actual,
                            effective_egress_level=prepared["effective_egress_level"],
                            ts=prepared["committed_at"],
                        )
                        appended = append_event(
                            self._conn, self._chain_key, self._audit_event(prepared, snapshot)
                        )
                except BudgetError:
                    # actual > reserved: the estimator is wrong, and that is
                    # not a licence to charge more.
                    return DisclosureOutcome(
                        released=False, error_code="PROOF_GENERATION_FAILED", retryable=True
                    )
                reservation = None  # the commit consumed it

                try:
                    # The crash seam sits INSIDE the handler: an injected
                    # failure at step 12 must take the same path as a real
                    # anchor failure, or the test would prove nothing about
                    # the degraded latch.
                    self._checkpoint("refresh_anchor")
                    write_anchor(
                        self._anchor_path,
                        self._chain_key,
                        now=prepared["committed_at"],
                        **{
                            k: appended[k]
                            for k in ("chain_epoch", "chain_seq", "event_id", "event_mac")
                        },
                    )
                except (AnchorError, OSError, RuntimeError):
                    self._latch_degraded(prepared["disclosure_ref"], "anchor_refresh_failure")
                    return DisclosureOutcome(
                        released=False, error_code="AUDIT_INTEGRITY_UNAVAILABLE", retryable=False
                    )

            # ==== only now may a byte cross the boundary ====================
            meta = self._build_meta(prepared, coverage, next_cursor)
            if self._transport is not None:
                self._transport.write(prepared["disclosure_ref"].encode("ascii"))
            return DisclosureOutcome(
                released=True,
                data=dict(data),
                meta=meta,
                disclosure_ref=prepared["disclosure_ref"],
            )
        finally:
            # Conservative cleanup: a reservation freed against a commit that
            # may have happened is a hole; a stale one is an annoyance.
            if reservation is not None:
                self._ledger.release(reservation.reservation_ref)
