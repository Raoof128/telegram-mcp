"""The WhatsApp Cloud ``DeliveryTransport`` (comms v0.3 Task C23; A22, A23; O8; 5b-4 S3).

``prepare`` reads only the facts the freeze copied into the intent: the mirrored window and
the campaign's template binding. It judges the window at the send time (O8): inside it, a
free-form text carrying ``window_closes_at``; otherwise the frozen template
``{name, language, schema_version, param_digests}`` with its parameters; with no template,
``Skip(TEMPLATE_REQUIRED)``. Missing window state means the template path. ``still_valid(now)``
re-checks the window against the claim time and the template against the catalogue, and a
closed window or an unavailable template skips the job; another template is never swapped in.
``deliver`` is one Graph call, classified by ``META_CODES``; it never retries.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta
from typing import Any

from comms.core import timeutil
from comms.core.campaigns.render import rendered_text
from comms.core.canonical import jcs_dumps
from comms.core.delivery.transport import (
    DeliveryIntent,
    DeliveryResult,
    FrozenDelivery,
    PreparedPayload,
    Skip,
    SkipReason,
)
from comms.transports.whatsapp.cloud.classify import classify_send
from comms.transports.whatsapp.cloud.http import GraphApi, GraphResponse, GraphTransportError
from comms.transports.whatsapp.cloud.templates import TemplateCatalog
from comms.transports.whatsapp.numbers import e164

__all__ = ["MAX_TEXT", "WINDOW", "WhatsAppDelivery"]

MAX_TEXT = 4096
WINDOW = timedelta(hours=24)


class WhatsAppDelivery:
    name = "whatsapp"
    actor = "whatsapp_cloud"

    def __init__(self, api: GraphApi, *, catalog: TemplateCatalog) -> None:
        self._api, self._catalog = api, catalog

    def __repr__(self) -> str:
        return "WhatsAppDelivery(<redacted>)"

    def normalize(self, platform_identity: str) -> str:
        return e164(platform_identity)

    def prepare(self, intent: DeliveryIntent, send_at: datetime) -> PreparedPayload | Skip:
        text = rendered_text(intent.content)
        if text is None or len(text) > MAX_TEXT:
            return Skip(SkipReason.CONTENT_UNSUPPORTED)
        window = intent.facts.get("window")
        if window is not None:
            closes = timeutil.instant(window["last_customer_message_at"]) + WINDOW
            if timeutil.utc(send_at) < closes:
                return _payload(
                    {
                        "kind": "text",
                        "to": intent.identity,
                        "text": text,
                        "window_closes_at": timeutil.iso(closes),
                    }
                )
        template = intent.facts.get("template")
        if template is None:
            return Skip(SkipReason.TEMPLATE_REQUIRED)
        parameters = list(template["parameters"])
        frozen = {
            "name": template["name"],
            "language": template["language"],
            "schema_version": template["schema_version"],
            "param_digests": [hashlib.sha256(p.encode()).hexdigest() for p in parameters],
        }
        return _payload(
            {
                "kind": "template",
                "to": intent.identity,
                "template": frozen,
                "parameters": parameters,
            }
        )

    def still_valid(self, payload: PreparedPayload, now: datetime) -> bool | str:
        body = json.loads(payload.data)
        if body["kind"] == "text":
            return timeutil.utc(now) < timeutil.instant(body["window_closes_at"])
        template = body["template"]
        return self._catalog.available(
            template["name"], template["language"], template["schema_version"]
        )

    def deliver(self, delivery: FrozenDelivery) -> DeliveryResult:
        body = json.loads(delivery.payload.data)
        if body["to"] != delivery.identity:
            raise ValueError("the payload is not for this delivery")
        try:
            outcome: GraphResponse | GraphTransportError = self._api.send_message(_graph_body(body))
        except GraphTransportError as exc:
            outcome = exc
        classified = classify_send(outcome)
        return DeliveryResult(classified.kind, provider_message_ref=classified.provider_message_ref)


def _payload(body: dict[str, Any]) -> PreparedPayload:
    data = jcs_dumps(body)
    return PreparedPayload(data=data, digest=hashlib.sha256(data).hexdigest())


def _graph_body(body: dict[str, Any]) -> dict[str, Any]:
    base = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": body["to"].lstrip("+"),
    }
    if body["kind"] == "text":
        return {**base, "type": "text", "text": {"body": body["text"]}}
    template = body["template"]
    parameters = [{"type": "text", "text": p} for p in body["parameters"]]
    return {
        **base,
        "type": "template",
        "template": {
            "name": template["name"],
            "language": {"code": template["language"]},
            "components": [{"type": "body", "parameters": parameters}],
        },
    }
