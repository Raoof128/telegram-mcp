"""comms v0.3 Task C27: WhatsVault's fake_meta extended into the Meta contract oracle (design §C.8)."""

import hashlib
import hmac
import inspect
import json

import pytest

from comms.transports.whatsapp.cloud.account import WhatsAppCapability
from comms.transports.whatsapp.cloud.groups import GroupDiscovery
from comms.transports.whatsapp.cloud.http import GraphApi
from comms.transports.whatsapp.cloud.media import MediaOps
from comms.transports.whatsapp.cloud.templates import TemplateCatalog, TemplateOps
from tests.conformance.meta_oracle import APP_SECRET, PHONE_ID, WABA_ID, oracle, oracle_transport
from tests.transports.whatsapp_cloud.helpers import Secrets

TEXT = {
    "messaging_product": "whatsapp",
    "recipient_type": "individual",
    "to": "61400000001",
    "type": "text",
    "text": {"body": "hi"},
}


def _api(graph):
    return GraphApi(
        Secrets(),
        version=1,
        phone_number_id=PHONE_ID,
        waba_id=WABA_ID,
        transport=oracle_transport(graph),
    )


def test_oracle_serves_every_endpoint_the_adapter_calls():
    graph = oracle()
    api = _api(graph)
    template = {
        "name": "spring",
        "language": "en",
        "category": "MARKETING",
        "components": [{"type": "BODY", "text": "Hi"}],
    }
    calls = {
        "send_message": lambda: api.send_message(TEXT),
        "list_templates": lambda: api.list_templates(limit=10),
        "create_template": lambda: api.create_template(template),
        "edit_template": lambda: api.edit_template(
            "1000", {"components": [{"type": "BODY", "text": "Hello"}]}
        ),
        "delete_template": lambda: api.delete_template("spring"),
        "upload_media": lambda: api.upload_media(b"\x89PNG", "image/png"),
        "media_info": lambda: api.media_info("2000"),
        "delete_media": lambda: api.delete_media("2000"),
        "phone_info": lambda: api.phone_info(),
        "list_groups": lambda: api.list_groups(limit=1),
        "remove_group_participant": lambda: api.remove_group_participant(
            "120363049891234567", "61400000009"
        ),
        "reset_group_invite": lambda: api.reset_group_invite("120363049891234567"),
        "update_group": lambda: api.update_group("120363049891234567", {"subject": "New"}),
        "mark_read": lambda: api.mark_read("wamid.HBgLNjE0MDAwMDAwMDEVAgASGBQ"),
    }
    public = {
        name
        for name, member in inspect.getmembers(GraphApi, inspect.isfunction)
        if not name.startswith("_") and name not in ("bearer", "close")
    }
    assert set(calls) == public  # a new adapter call without oracle coverage fails here
    for name, call in calls.items():
        response = call()
        assert response.http_status == 200, (name, response.envelope)
    assert graph.unknown_routes == []


def test_the_oracle_drives_the_adapter_modules_end_to_end():
    graph = oracle()
    api = _api(graph)
    catalog = TemplateCatalog()
    assert TemplateOps(api).refresh(catalog) >= 1
    media = MediaOps(api, download_transport=oracle_transport(graph))
    uploaded = media.upload(b"\x89PNG oracle", "image/png")
    assert media.retrieve(uploaded.provider_ref).data == b"\x89PNG oracle"
    discovery = GroupDiscovery(api)
    assert set(discovery.discover().values()) == {"AVAILABLE"}
    assert (
        WhatsAppCapability(api, discovery, clock=lambda: None).inspect_phone()["status"]
        == "CONNECTED"
    )


@pytest.mark.parametrize(
    "mode,state", [("ineligible", "ACCOUNT_INELIGIBLE"), ("unsupported", "PROVIDER_UNSUPPORTED")]
)
def test_the_oracle_models_unavailable_groups(mode, state):
    graph = oracle(groups=mode)
    assert set(GroupDiscovery(_api(graph)).discover().values()) == {state}


def test_webhook_signature_emitted_correctly():
    graph = oracle()
    wamid = _api(graph).send_message(TEXT).envelope["messages"][0]["id"]
    raw, headers = graph.webhook_for_statuses()
    expected = "sha256=" + hmac.new(APP_SECRET, raw, hashlib.sha256).hexdigest()
    assert headers == {"X-Hub-Signature-256": expected, "Content-Type": "application/json"}
    body = json.loads(raw)
    statuses = body["entry"][0]["changes"][0]["value"]["statuses"]
    assert [(s["id"], s["status"]) for s in statuses] == [(wamid, "sent"), (wamid, "delivered")]
    assert (
        json.loads(graph.webhook_for_statuses()[0])["entry"][0]["changes"][0]["value"]["statuses"]
        == []
    )  # drained
    tampered = raw.replace(b"delivered", b"read")
    assert (
        hmac.new(APP_SECRET, tampered, hashlib.sha256).hexdigest()
        not in headers["X-Hub-Signature-256"]
    )


def test_the_oracle_is_deterministic():
    first, second = oracle(), oracle()
    assert _api(first).send_message(TEXT).envelope == _api(second).send_message(TEXT).envelope
    assert first.webhook_for_statuses() == second.webhook_for_statuses()
