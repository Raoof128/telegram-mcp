"""Conformance cases for ``whatsapp_cloud`` (A18), over hand-built Meta envelopes. Live mode
needs the operator's Meta test number (C.9)."""

from __future__ import annotations

from datetime import UTC, datetime

import httpx

from comms.core.delivery.transport import (
    DeliveryIntent,
    FrozenDelivery,
    PreparedPayload,
    ResultKind,
)
from comms.core.providers.capability import Capability, CapabilityState
from comms.core.providers.protocols import ProviderTarget, SemanticOperation
from comms.transports.whatsapp.cloud.account import WhatsAppCapability
from comms.transports.whatsapp.cloud.delivery import WhatsAppDelivery
from comms.transports.whatsapp.cloud.groups import GROUP_CAPABILITIES, GroupDiscovery, WhatsAppAdmin
from comms.transports.whatsapp.cloud.http import GraphApi
from comms.transports.whatsapp.cloud.templates import TemplateCatalog
from tests.conformance.registry import REGISTRY
from tests.conformance.runner import Mode, Skip
from tests.transports.whatsapp_cloud.helpers import Secrets, fixture_transport, raising

SEND_AT = datetime(2026, 9, 24, 12, tzinfo=UTC)
TEMPLATE = {"name": "t", "language": "en", "schema_version": 1, "parameters": []}


def _wa(mode: Mode, inner: httpx.BaseTransport, seen: list) -> WhatsAppDelivery:
    if mode.live:
        raise Skip("NOT_CONFIGURED")

    def spy(request):
        seen.append(request)
        return inner.handle_request(request)

    api = GraphApi(
        Secrets(), version=1, phone_number_id="106540352242922", transport=httpx.MockTransport(spy)
    )
    catalog = TemplateCatalog()
    catalog.replace({("t", "en"): ("APPROVED", 1)})
    return WhatsAppDelivery(api, catalog=catalog)


def _send(transport: WhatsAppDelivery):
    payload = transport.prepare(
        DeliveryIntent("whatsapp", "+61400000001", {"canonical": "hi"}, {"template": TEMPLATE}),
        SEND_AT,
    )
    assert isinstance(payload, PreparedPayload)
    return transport.deliver(
        FrozenDelivery("djb_x", "gen_x", "whatsapp", "+61400000001", payload, "k" * 64)
    )


@REGISTRY.case("whatsapp_cloud", "delivery")
def delivery_accepts_with_one_call(mode: Mode) -> None:
    seen: list = []
    result = _send(_wa(mode, fixture_transport("send_ok"), seen))
    assert result.kind is ResultKind.ACCEPTED and len(seen) == 1


@REGISTRY.case("whatsapp_cloud", "delivery")
def delivery_ambiguity_is_unknown_and_never_retried(mode: Mode) -> None:
    seen: list = []
    result = _send(_wa(mode, raising(httpx.ReadTimeout), seen))
    assert result.kind is ResultKind.OUTCOME_UNKNOWN and len(seen) == 1


_GROUP = ProviderTarget("whatsapp", "whatsapp_cloud", "dst_g", "group:120363049891234567")


def _gated(
    mode: Mode, groups_status: int, groups_body: dict, seen: list
) -> tuple[GraphApi, GroupDiscovery]:
    if mode.live:
        raise Skip("NOT_CONFIGURED")

    def handler(request):
        seen.append(request)
        if request.url.path.endswith("/groups"):
            return httpx.Response(groups_status, json=groups_body)
        return httpx.Response(200, json={"status": "CONNECTED", "quality_rating": "GREEN"})

    api = GraphApi(
        Secrets(),
        version=1,
        phone_number_id="106540352242922",
        transport=httpx.MockTransport(handler),
    )
    discovery = GroupDiscovery(api)
    discovery.discover()
    return api, discovery


_REFUSED = {"error": {"message": "(#10) no permission", "code": 10}}


@REGISTRY.case("whatsapp_cloud", "capability")
def capability_groups_follow_discovery_never_assumed(mode: Mode) -> None:
    seen: list = []
    api, discovery = _gated(mode, 403, _REFUSED, seen)
    states = (
        WhatsAppCapability(api, discovery, clock=lambda: SEND_AT)
        .snapshot("whatsapp_cloud", _GROUP)
        .states
    )
    assert {states[c] for c in GROUP_CAPABILITIES} == {CapabilityState.ACCOUNT_INELIGIBLE}


@REGISTRY.case("whatsapp_cloud", "capability")
def capability_without_credentials_is_not_configured(mode: Mode) -> None:
    if mode.live:
        raise Skip("NOT_CONFIGURED")
    states = (
        WhatsAppCapability(None, None, clock=lambda: SEND_AT)
        .snapshot("whatsapp_cloud", _GROUP)
        .states
    )
    assert set(states.values()) == {CapabilityState.NOT_CONFIGURED}


@REGISTRY.case("whatsapp_cloud", "admin")
def admin_gated_group_operation_is_never_simulated(mode: Mode) -> None:
    seen: list = []
    api, discovery = _gated(mode, 403, _REFUSED, seen)
    before = len(seen)
    result = WhatsAppAdmin(api, discovery).invoke(
        SemanticOperation(Capability.GROUP_INVITE_RESET, {}), _GROUP, "k"
    )
    assert (result.outcome, result.code) == ("FAILED", "ACCOUNT_INELIGIBLE") and len(seen) == before


@REGISTRY.case("whatsapp_cloud", "admin")
def admin_available_group_operation_is_one_call(mode: Mode) -> None:
    seen: list = []
    api, discovery = _gated(mode, 200, {"data": []}, seen)
    before = len(seen)
    result = WhatsAppAdmin(api, discovery).invoke(
        SemanticOperation(Capability.GROUP_SETTINGS_UPDATE, {"subject": "New"}), _GROUP, "k"
    )
    assert result.outcome == "SUCCEEDED" and len(seen) == before + 1
