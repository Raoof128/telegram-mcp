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
from comms.transports.whatsapp.cloud.delivery import WhatsAppDelivery
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
