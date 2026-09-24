"""Conformance cases for ``telegram_bot`` (A18), over recorded Bot API envelopes.

Live mode needs the operator's disposable bot (C.9); without it each case reports
``NOT_CONFIGURED``.
"""

from __future__ import annotations

import socket
from datetime import UTC, datetime

import httpx

from comms.core.delivery.transport import DeliveryIntent, FrozenDelivery, ResultKind
from comms.core.providers.capability import Capability, CapabilityState
from comms.core.providers.protocols import ProviderTarget
from comms.transports.telegram.bot.capability import BotCapability
from comms.transports.telegram.bot.delivery import BotDelivery
from comms.transports.telegram.bot.http import BotApi
from tests.conformance.registry import REGISTRY
from tests.conformance.runner import Mode, Skip
from tests.transports.telegram_bot.helpers import Secrets, fixture_transport, raising, routed

NOW = datetime(2026, 9, 25, tzinfo=UTC)


def _bot(mode: Mode, inner: httpx.BaseTransport, seen: list) -> BotDelivery:
    if mode.live:
        raise Skip("NOT_CONFIGURED")

    def spy(request):
        seen.append(request)
        return inner.handle_request(request)

    return BotDelivery(BotApi(Secrets(), version=1, transport=httpx.MockTransport(spy)))


def _send(transport: BotDelivery):
    payload = transport.prepare(DeliveryIntent("telegram", "-1001", {"text": "hi"}), NOW)
    return transport.deliver(
        FrozenDelivery("djb_x", "gen_x", "telegram", "-1001", payload, "k" * 64)
    )


@REGISTRY.case("telegram_bot", "delivery")
def delivery_accepts_with_one_call(mode: Mode) -> None:
    seen: list = []
    result = _send(_bot(mode, fixture_transport("sendMessage_ok"), seen))
    assert result.kind is ResultKind.ACCEPTED and result.provider_message_ref and len(seen) == 1


@REGISTRY.case("telegram_bot", "delivery")
def delivery_ambiguity_is_unknown_and_never_retried(mode: Mode) -> None:
    for exc_type in (httpx.ReadTimeout, httpx.RemoteProtocolError):
        seen: list = []
        result = _send(_bot(mode, raising(exc_type), seen))
        assert result.kind is ResultKind.OUTCOME_UNKNOWN and len(seen) == 1


@REGISTRY.case("telegram_bot", "delivery")
def delivery_prepare_and_still_valid_touch_nothing(mode: Mode) -> None:
    seen: list = []
    transport = _bot(mode, fixture_transport("sendMessage_ok"), seen)
    real_connect = socket.socket.connect

    def refuse(*_a, **_k):
        raise AssertionError("network touched")

    socket.socket.connect = refuse  # type: ignore[method-assign]
    try:
        transport.normalize("channel:1")
        payload = transport.prepare(DeliveryIntent("telegram", "-1001", {"text": "hi"}), NOW)
        transport.still_valid(payload, NOW)  # type: ignore[arg-type]
    finally:
        socket.socket.connect = real_connect  # type: ignore[method-assign]
    assert seen == []


def _capability(mode: Mode, routes: dict) -> BotCapability:
    if mode.live:
        raise Skip("NOT_CONFIGURED")
    return BotCapability.from_api(
        BotApi(Secrets(), version=1, transport=routed(routes)), clock=lambda: NOW
    )


_GROUP = ProviderTarget("telegram", "telegram_bot", "dst_x", "-1001234567890")


@REGISTRY.case("telegram_bot", "capability")
def capability_never_claims_what_the_bot_api_lacks(mode: Mode) -> None:
    routes = {
        "getMe": "getMe_ok",
        "getChat": "getChat_supergroup_forum",
        "getChatMember": "getChatMember_creator",
    }
    states = _capability(mode, routes).snapshot("telegram_bot", _GROUP).states
    for cap in (Capability.HISTORY_READ, Capability.HISTORY_SEARCH, Capability.GROUP_DELETE):
        assert states[cap] is CapabilityState.PROVIDER_UNSUPPORTED


@REGISTRY.case("telegram_bot", "capability")
def capability_failed_lookup_is_never_available(mode: Mode) -> None:
    routes = {
        "getMe": "getMe_ok",
        "getChat": httpx.ReadTimeout,
        "getChatMember": "getChatMember_creator",
    }
    states = _capability(mode, routes).snapshot("telegram_bot", _GROUP).states
    assert CapabilityState.AVAILABLE not in states.values()
