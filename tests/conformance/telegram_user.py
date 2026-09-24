"""Conformance cases for ``telegram_user`` (A18), over a fake MTProto session that dedupes by
random_id as Telegram does. Live mode needs the operator's disposable account (C.9)."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

from comms.core.delivery.transport import (
    DeliveryIntent,
    FrozenDelivery,
    PreparedPayload,
    ResultKind,
)
from comms.core.providers.capability import Capability, CapabilityState
from comms.core.providers.protocols import (
    ContextQuery,
    ContextRefused,
    ProviderResult,
    ProviderTarget,
    SemanticOperation,
)
from comms.core.providers.semantics import SEMANTICS
from comms.transports.telegram.telegram.errors import GatewayError
from comms.transports.telegram.telegram.rights import SelfRights
from comms.transports.telegram.user.admin import UserAdmin
from comms.transports.telegram.user.capability import UserCapability
from comms.transports.telegram.user.context import UserContext
from comms.transports.telegram.user.delivery import UserDelivery
from tests.conformance.registry import REGISTRY
from tests.conformance.runner import Mode, Skip
from tests.transports.telegram_user.helpers import FakeSession

NOW = datetime(2026, 9, 25, tzinfo=UTC)
CHAT = "-1001234567890"


def _user(mode: Mode, session: FakeSession) -> UserDelivery:
    if mode.live:
        raise Skip("NOT_CONFIGURED")
    return UserDelivery(session, run=asyncio.run)


def _frozen(transport: UserDelivery, attempt_no: int = 1) -> FrozenDelivery:
    payload = transport.prepare(DeliveryIntent("telegram", CHAT, {"text": "hi"}), NOW)
    assert isinstance(payload, PreparedPayload)
    return FrozenDelivery("djb_x", "gen_x", "telegram", CHAT, payload, "ab" * 32, attempt_no)


@REGISTRY.case("telegram_user", "delivery")
def delivery_accepts_with_one_call(mode: Mode) -> None:
    session = FakeSession()
    result = _user(mode, session).deliver(_frozen(_user(mode, session)))
    assert (
        result.kind is ResultKind.ACCEPTED
        and result.provider_message_ref
        and len(session.sent) == 1
    )


@REGISTRY.case("telegram_user", "delivery")
def delivery_ambiguity_reissues_once_with_the_same_random_id(mode: Mode) -> None:
    session = FakeSession(["drop", "drop"])
    transport = _user(mode, session)
    assert transport.deliver(_frozen(transport)).kind is ResultKind.OUTCOME_UNKNOWN
    assert len(session.sent) == 2 and len({rid for _p, _t, rid in session.sent}) == 1


@REGISTRY.case("telegram_user", "delivery")
def delivery_is_keyed_so_attempts_dedupe(mode: Mode) -> None:
    session = FakeSession(["drop", "drop"])
    transport = _user(mode, session)
    transport.deliver(_frozen(transport, 1))
    assert transport.deliver(_frozen(transport, 2)).kind is ResultKind.ACCEPTED
    assert len(session.visible) == 1
    assert transport.provider_request_key("ab" * 32) == str(session.sent[0][2])


class _RightsSession:
    def __init__(self, answer: object) -> None:
        self.answer = answer

    def readiness(self) -> None:
        return None

    async def self_rights(self, peer_type: str, peer_id: int, *, timeout: float) -> SelfRights:
        if isinstance(self.answer, Exception):
            raise self.answer
        return self.answer  # type: ignore[return-value]


def _capability(mode: Mode, answer: object) -> UserCapability:
    if mode.live:
        raise Skip("NOT_CONFIGURED")
    return UserCapability(_RightsSession(answer), run=asyncio.run, clock=lambda: NOW)


_GROUP = ProviderTarget("telegram", "telegram_user", "dst_x", CHAT)


@REGISTRY.case("telegram_user", "capability")
def capability_user_reads_history_where_it_can_read(mode: Mode) -> None:
    states = (
        _capability(mode, SelfRights("megagroup", "member"))
        .snapshot("telegram_user", _GROUP)
        .states
    )
    assert states[Capability.HISTORY_SEARCH] is CapabilityState.AVAILABLE


@REGISTRY.case("telegram_user", "capability")
def capability_failed_lookup_is_never_available_for_the_destination(mode: Mode) -> None:
    states = (
        _capability(mode, GatewayError("INTERNAL_ERROR")).snapshot("telegram_user", _GROUP).states
    )
    assert {s for c, s in states.items() if c is not Capability.GROUP_CREATE} == {
        CapabilityState.UNKNOWN
    }


class _AdminSession:
    def __init__(self, answer: ProviderResult) -> None:
        self.answer, self.calls = answer, 0

    async def admin_request(self, capability, peer_type, peer_id, spec, *, timeout):
        self.calls += 1
        return self.answer


def _admin(mode: Mode, answer: ProviderResult) -> tuple[UserAdmin, _AdminSession]:
    if mode.live:
        raise Skip("NOT_CONFIGURED")
    session = _AdminSession(answer)
    return UserAdmin(session, run=asyncio.run, clock=lambda: NOW), session


@REGISTRY.case("telegram_user", "admin")
def admin_single_call_operations_make_one_call(mode: Mode) -> None:
    admin, session = _admin(mode, ProviderResult("SUCCEEDED", None))
    for cap in UserAdmin.operations:
        assert not SEMANTICS[(cap, "telegram_user")].steps
    result = admin.invoke(SemanticOperation(Capability.MEMBER_BAN, {"user_id": 42}), _GROUP, "k")
    assert result.outcome == "SUCCEEDED" and session.calls == 1


@REGISTRY.case("telegram_user", "admin")
def admin_saga_is_never_one_call(mode: Mode) -> None:
    admin, session = _admin(mode, ProviderResult("SUCCEEDED", None))
    try:
        admin.invoke(SemanticOperation(Capability.MEMBER_REMOVE, {"user_id": 42}), _GROUP, "k")
    except ValueError:
        pass
    else:
        raise AssertionError("member.remove ran as one call")
    assert session.calls == 0


class _ReadSession:
    def __init__(self, readiness: str | None = None) -> None:
        self._readiness, self.calls = readiness, 0

    def readiness(self) -> str | None:
        return self._readiness

    async def fetch_history(self, peer_type, peer_id, **kw):
        self.calls += 1
        return [], None

    async def search_peer(self, peer_type, peer_id, query, **kw):
        self.calls += 1
        raise AssertionError("not used")

    async def fetch_participants(self, peer_type, peer_id, *, offset, limit, timeout):
        self.calls += 1
        return [(42, "member", "Ali")], None


def _context(mode: Mode, session: _ReadSession) -> UserContext:
    if mode.live:
        raise Skip("NOT_CONFIGURED")
    return UserContext(session, run=asyncio.run, clock=lambda: NOW)


@REGISTRY.case("telegram_user", "context")
def context_items_are_live_and_untrusted(mode: Mode) -> None:
    page = _context(mode, _ReadSession()).read(ContextQuery(_GROUP, "members"))
    assert page.provenance == "telegram_live"
    assert all(i["source"] == "telegram_live" and "untrusted" in i for i in page.items)


@REGISTRY.case("telegram_user", "context")
def context_refuses_on_an_unusable_session_without_a_call(mode: Mode) -> None:
    session = _ReadSession("SESSION_REVOKED")
    try:
        _context(mode, session).read(ContextQuery(_GROUP, "recent"))
    except ContextRefused as refused:
        assert refused.code == "SESSION_REVOKED"
    else:
        raise AssertionError("read served on a revoked session")
    assert session.calls == 0
