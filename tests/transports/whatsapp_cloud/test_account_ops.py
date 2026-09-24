"""comms v0.3 Task D17: account-level WhatsApp writes (templates, media delete) and media info."""

import pytest

from comms.core.providers.capability import Capability as C
from comms.core.providers.protocols import ProviderTarget, SemanticOperation
from comms.transports.whatsapp.cloud.groups import GroupDiscovery, WhatsAppAdmin
from comms.transports.whatsapp.cloud.http import GraphApi
from comms.transports.whatsapp.cloud.media import MediaOps
from tests.conformance.meta_oracle import PHONE_ID, WABA_ID, oracle, oracle_transport
from tests.transports.whatsapp_cloud.helpers import Secrets

ACCOUNT = ProviderTarget("whatsapp", "whatsapp_cloud", "acct", f"waba:{WABA_ID}")
GROUP = ProviderTarget("whatsapp", "whatsapp_cloud", "dst_g", "group:120363049891234567")
DEFINITION = {
    "name": "autumn",
    "language": "en",
    "category": "MARKETING",
    "components": [{"type": "BODY", "text": "Hi"}],
}


def _world():
    graph = oracle()
    api = GraphApi(
        Secrets(),
        version=1,
        phone_number_id=PHONE_ID,
        waba_id=WABA_ID,
        transport=oracle_transport(graph),
    )
    return graph, api, WhatsAppAdmin(api, GroupDiscovery(api))


def test_template_create_edit_delete_through_the_admin_adapter():
    _graph, _api, admin = _world()
    created = admin.invoke(SemanticOperation(C.TEMPLATE_CREATE, DEFINITION), ACCOUNT, "k")
    assert created.outcome == "SUCCEEDED" and created.provider_ref.isdigit()
    edit = {"template_id": created.provider_ref, "components": [{"type": "BODY", "text": "Yo"}]}
    assert admin.invoke(SemanticOperation(C.TEMPLATE_EDIT, edit), ACCOUNT, "k").outcome == (
        "SUCCEEDED"
    )
    deleted = admin.invoke(SemanticOperation(C.TEMPLATE_DELETE, {"name": "autumn"}), ACCOUNT, "k")
    assert deleted.outcome == "SUCCEEDED"


def test_media_delete_and_info():
    _graph, api, admin = _world()
    op = SemanticOperation(C.MEDIA_DELETE, {"media_id": "2000"})
    info = MediaOps(api).info("2000")
    assert info["mime_type"] == "image/png" and "url" not in info
    assert admin.invoke(op, ACCOUNT, "k").outcome == "SUCCEEDED"


@pytest.mark.parametrize(
    ("op", "target"),
    [
        (
            SemanticOperation(C.TEMPLATE_CREATE, DEFINITION),
            GROUP,
        ),  # an account write, not a group's
        (SemanticOperation(C.TEMPLATE_CREATE, {**DEFINITION, "category": "SPAM"}), ACCOUNT),
        (SemanticOperation(C.TEMPLATE_EDIT, {"template_id": "x", "components": []}), ACCOUNT),
        (SemanticOperation(C.TEMPLATE_DELETE, {"name": "Bad Name"}), ACCOUNT),
        (SemanticOperation(C.MEDIA_DELETE, {"media_id": "../1"}), ACCOUNT),
    ],
)
def test_malformed_account_writes_are_refused_without_a_call(op, target):
    graph, _api, admin = _world()
    with pytest.raises(ValueError):
        admin.validate(op, target)
    assert graph.unknown_routes == []
