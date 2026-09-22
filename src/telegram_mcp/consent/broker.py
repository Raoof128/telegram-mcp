"""In-memory consent broker: ``tgu_`` issuance, exact-once consume, sweeps.

``ConsentBroker(challenge_key, agent_verify, runtime_id)`` keeps an
in-memory dict only (never SQLite). ``consume(handle, envelope)`` verifies
in order: handle exists/unused/unexpired → recompute SHA256 of the exact
challenge JCS → constant-time compare ``challenge_sha256`` → ``key_id``
equals the currently pinned approval key → P-256 ECDSA/SHA-256 verifies →
snapshot still current (liveness re-check: epoch/grant/disconnect sweeps
remove records via ``invalidate_where``) → atomic consume.

``agent_verify`` is a two-argument ``(sig, msg) -> bool`` closure; in tests
it is ``StubSigner.verify`` (pinned id read off the fixture pair), in
production a closure over the pinned agent key with the same shape (pass
``pinned_key_id`` explicitly there). The broker raises fixed-code
``ConsentError``s and never builds MCP results: ``challenge-expired`` maps
to ``DEADLINE_EXCEEDED``, rate-limit/unavailable states map to
``CONSENT_UNAVAILABLE``, everything else maps to ``CONSENT_DENIED`` (wiring
lands in Task 9's vertical slice).

There is no auto-approve path in this module: issuance (shell-triggerable)
only creates a challenge; approval exists solely as agent-signed
``consume``. No method named ``approve`` may ever be added here.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from telegram_mcp.consent.challenge import (
    CHALLENGE_TTL_S,
    canonical_challenge,
    mint_challenge_handle,
    sign_challenge,
    synthetic_exposure_digest,
)
from telegram_mcp.opaque import validate_ref_format

__all__ = [
    "ConsentBroker",
    "ConsentError",
    "ConsumedChallenge",
    "PendingChallenge",
]

# Rate limits per client: 5/min and 30/hr.
_RATE_PER_MINUTE = 5
_RATE_PER_HOUR = 30
_MINUTE_S = 60.0
_HOUR_S = 3600.0


class ConsentError(Exception):
    """Fixed-code consent failure. ``.code`` is specific; ``dispatch_code`` maps."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)

    @property
    def dispatch_code(self) -> str:
        """Dispatch-layer mapping (wired in Task 9; broker never builds results)."""
        if self.code == "challenge-expired":
            return "DEADLINE_EXCEEDED"
        if self.code in ("rate-limited", "broker-unavailable"):
            return "CONSENT_UNAVAILABLE"
        return "CONSENT_DENIED"


@dataclass(frozen=True)
class ConsumedChallenge:
    """Result of a verified exact-once consume."""

    handle: str
    tool: str
    principal: str
    client: str
    account: str
    policy_epoch: int
    security_epoch: int
    challenge_sha256: str
    key_id: str


@dataclass
class PendingChallenge:
    """Live (unconsumed) challenge record; predicate view for sweeps."""

    handle: str
    tool: str
    principal: str
    client: str
    account: str
    policy_epoch: int
    project_scope_digest: str
    security_epoch: int
    challenge: bytes
    daemon_sig: str
    expires_at: float
    consumed: bool = field(default=False)


@dataclass(frozen=True)
class _IssueParams:
    tool: str
    request_hmac: str
    principal: str
    client: str
    account: str
    policy_epoch: int
    project_scope_digest: str
    security_epoch: int
    display_digest: str
    exposure_snapshot_digest: str


class ConsentBroker:
    """In-memory ``tgu_`` challenge map with exact-once consume."""

    def __init__(
        self,
        challenge_key: bytes,
        agent_verify: Callable[[bytes, bytes], bool],
        runtime_id: bytes,
        *,
        now: Callable[[], float] | None = None,
        pinned_key_id: str | None = None,
    ) -> None:
        if not isinstance(challenge_key, bytes) or len(challenge_key) != 32:
            raise ValueError("invalid challenge key")
        if not isinstance(runtime_id, bytes) or len(runtime_id) != 16:
            raise ValueError("invalid runtime_id")
        if not callable(agent_verify):
            raise ValueError("invalid agent verifier")  # noqa: TRY004 -- uniform ValueError on broker validation
        self._challenge_key = bytes(challenge_key)
        self._agent_verify = agent_verify
        self._runtime_id = bytes(runtime_id)
        # Monotonic clock with an injectable override for tests. Public so
        # frozen-clock tests can assign ``broker.now = lambda: t``.
        self.now: Callable[[], float] = now or time.monotonic
        self._pinned_key_id = pinned_key_id or self._resolve_pinned_key_id(agent_verify)
        if self._pinned_key_id is None:
            raise ValueError("pinned approval key id is unknown")
        self._pending: dict[str, PendingChallenge] = {}
        self._buckets: dict[str, deque[float]] = {}

    @staticmethod
    def _resolve_pinned_key_id(agent_verify: Callable[[bytes, bytes], bool]) -> str | None:
        owner = getattr(agent_verify, "__self__", None)
        if owner is not None and isinstance(getattr(owner, "key_id", None), str):
            return owner.key_id
        key_id = getattr(agent_verify, "key_id", None)
        return key_id if isinstance(key_id, str) else None

    @property
    def pinned_key_id(self) -> str:
        """Currently pinned approval key id (``key_id`` pin check target)."""
        return self._pinned_key_id

    def _check_rate_limit(self, client: str) -> None:
        at = self.now()
        bucket = self._buckets.setdefault(client, deque())
        while bucket and bucket[0] <= at - _HOUR_S:
            bucket.popleft()
        recent = sum(1 for stamp in bucket if stamp > at - _MINUTE_S)
        if recent >= _RATE_PER_MINUTE or len(bucket) >= _RATE_PER_HOUR:
            raise ConsentError("rate-limited")
        bucket.append(at)

    async def issue(
        self,
        *,
        tool: str,
        request_hmac: str,
        principal: str,
        client: str,
        account: str,
        policy_epoch: int,
        project_scope_digest: str,
        security_epoch: int,
        display_digest: str,
        exposure_snapshot_digest: str | None = None,
    ) -> str:
        """Freeze args, bind a ``tgu_`` challenge, daemon-sign, return handle.

        ``exposure_snapshot_digest`` defaults to the frozen synthetic-zero
        form so callers that only carry display material still sign the full
        set (the wire field is always present).
        """
        validate_ref_format(client)
        self._check_rate_limit(client)
        params = _IssueParams(
            tool=tool,
            request_hmac=request_hmac,
            principal=principal,
            client=client,
            account=account,
            policy_epoch=policy_epoch,
            project_scope_digest=project_scope_digest,
            security_epoch=security_epoch,
            display_digest=display_digest,
            exposure_snapshot_digest=exposure_snapshot_digest or synthetic_exposure_digest(),
        )
        raw = canonical_challenge(
            tool=params.tool,
            request_hmac=params.request_hmac,
            principal=params.principal,
            client=params.client,
            account=params.account,
            policy_epoch=params.policy_epoch,
            project_scope_digest=params.project_scope_digest,
            security_epoch=params.security_epoch,
            runtime_id=self._runtime_id,
            display_digest=params.display_digest,
            exposure_snapshot_digest=params.exposure_snapshot_digest,
        )
        handle = mint_challenge_handle()
        while handle in self._pending:
            handle = mint_challenge_handle()
        self._pending[handle] = PendingChallenge(
            handle=handle,
            tool=params.tool,
            principal=params.principal,
            client=params.client,
            account=params.account,
            policy_epoch=params.policy_epoch,
            project_scope_digest=params.project_scope_digest,
            security_epoch=params.security_epoch,
            challenge=raw,
            daemon_sig=sign_challenge(raw, self._challenge_key),
            expires_at=self.now() + CHALLENGE_TTL_S,
        )
        return handle

    def challenge_bytes(self, handle: str) -> bytes:
        """Exact challenge JCS bytes for ``handle`` (test/agent accessor)."""
        record = self._pending.get(handle)
        if record is None or record.consumed:
            raise ConsentError("unknown-challenge")
        return record.challenge

    def daemon_signature(self, handle: str) -> str:
        """Base64url daemon Ed25519 signature over the challenge JCS."""
        record = self._pending.get(handle)
        if record is None or record.consumed:
            raise ConsentError("unknown-challenge")
        return record.daemon_sig

    @staticmethod
    def _decode_sig(sig: Any) -> bytes | None:
        if isinstance(sig, (bytes, bytearray)):
            return bytes(sig)
        if isinstance(sig, str):
            padded = sig + "=" * (-len(sig) % 4)
            try:
                return base64.urlsafe_b64decode(padded.encode("ascii"))
            except (ValueError, base64.binascii.Error):
                return None
        return None

    async def consume(self, handle: str, envelope: dict[str, Any]) -> ConsumedChallenge:
        """Verify the full ApprovalEnvelope in order; exact-once consume."""
        record = self._pending.get(handle)
        if record is None or record.consumed:
            raise ConsentError("unknown-challenge")
        if self.now() > record.expires_at:
            del self._pending[handle]
            raise ConsentError("challenge-expired")
        # 1. SHA256 of the exact challenge JCS, constant-time compare.
        want_sha = hashlib.sha256(record.challenge).hexdigest()
        got_sha = envelope.get("challenge_sha256")
        if not isinstance(got_sha, str) or not hmac.compare_digest(want_sha, got_sha):
            raise ConsentError("challenge-mismatch")
        # 2. key_id equals the currently pinned approval key.
        got_key = envelope.get("key_id")
        if not isinstance(got_key, str) or not hmac.compare_digest(self._pinned_key_id, got_key):
            raise ConsentError("unknown-key")
        # 3. P-256 ECDSA/SHA-256 over the same JCS bytes (fail closed).
        sig = self._decode_sig(envelope.get("sig"))
        try:
            verified = sig is not None and bool(self._agent_verify(sig, record.challenge))
        except Exception:  # noqa: BLE001 -- fail closed on any third-party verifier failure
            verified = False
        if not verified:
            raise ConsentError("bad-agent-signature")
        # 4. Snapshot still current: the record must still be live (sweeps
        # for epoch/grant/disconnect remove it via invalidate_where).
        live = self._pending.get(handle)
        if live is None or live.consumed or live.challenge != record.challenge:
            raise ConsentError("challenge-superseded")
        # 5. Atomic consume: single-threaded asyncio — mark and remove.
        live.consumed = True
        del self._pending[handle]
        return ConsumedChallenge(
            handle=handle,
            tool=record.tool,
            principal=record.principal,
            client=record.client,
            account=record.account,
            policy_epoch=record.policy_epoch,
            security_epoch=record.security_epoch,
            challenge_sha256=want_sha,
            key_id=self._pinned_key_id,
        )

    def invalidate(self, handle: str) -> bool:
        """Remove one pending challenge; return True if one was live."""
        record = self._pending.pop(handle, None)
        return record is not None and not record.consumed

    def invalidate_where(self, predicate: Callable[[PendingChallenge], bool]) -> int:
        """Sweep pending challenges (epoch/grant/disconnect); return count."""
        victims = [handle for handle, record in self._pending.items() if predicate(record)]
        for handle in victims:
            del self._pending[handle]
        return len(victims)

    def pending_count(self) -> int:
        """Live (unconsumed, unswept) challenge count (tests/diagnostics)."""
        return len(self._pending)
