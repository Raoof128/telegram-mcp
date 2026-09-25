"""comms v0.3 Task D14: message services — actor choice, refs, truthful delete scope (P §23, §72,
§77)."""

import pytest

from comms.core import refs
from comms.core.campaigns.directory import destination_id
from comms.core.errors import CommsError
from comms.core.objects import message_identity, object_ref
from comms.core.providers.capability import Capability as C
from comms.core.providers.capability import CapabilityState as S
from comms.core.providers.protocols import ProviderTarget
from comms.services.context import ContextEngine
from comms.services.messages import MessageService
from tests.core.campaign_helpers import NOW
from tests.services.context_fixtures import Clock, Source
from tests.services.group_fixtures import CTX, fixtures, group_world

WA_CONTACT = ProviderTarget("whatsapp", "whatsapp_cloud", "cpt_w", "+61400000001")


@pytest.fixture
def world(tmp_path):
    return group_world(tmp_path)


def _service(world, **kw):
    capability, executor, admins = fixtures(world, **kw)
    return MessageService(world["conn"], capability, executor), admins


def _both(world):
    return {"telegram_bot": world["bot"], "telegram_user": world["user"]}


def _seen(world, target, message_id=5):
    """The ref the context engine mints for a message it read."""
    conn = world["conn"]
    return object_ref(
        conn,
        "message",
        target.transport,
        target.actor,
        destination_id(conn, target.destination_ref),
        message_identity(target.identity, message_id),
        now=NOW,
    )


def _req():
    return refs.mint("request")


def test_send_as_me_uses_user_actor(world):
    service, admins = _service(world)
    result = service.send(CTX, world["grp"], _both(world), "Salaam", _req(), actor="telegram_user")
    assert result["actor"] == "telegram_user" and result["result"] == "SUCCEEDED"
    assert admins["telegram_user"].calls == [(C.MESSAGE_SEND, {"text": "Salaam"})]
    assert admins["telegram_bot"].calls == [] and result["message"].startswith("cmg_")


def test_send_from_bot_uses_bot_actor(world):
    service, admins = _service(world)
    result = service.send(CTX, world["grp"], _both(world), "Salaam", _req())
    assert result["actor"] == "telegram_bot" and admins["telegram_user"].calls == []


def test_send_as_me_never_falls_back_to_the_bot(world):
    service, admins = _service(world, states={"telegram_user": S.NOT_AUTHORIZED})
    with pytest.raises(CommsError) as refused:
        service.send(CTX, world["grp"], _both(world), "Salaam", _req(), actor="telegram_user")
    assert refused.value.code == "NOT_AUTHORIZED"
    assert admins["telegram_bot"].calls == admins["telegram_user"].calls == []


def test_the_sent_message_ref_is_the_one_context_would_mint(world):
    service, _admins = _service(world)
    result = service.send(CTX, world["grp"], _both(world), "Salaam", _req(), actor="telegram_user")

    class Echo(Source):  # a source that returns exactly the message asked for
        def read(self, query):
            page = super().read(query)
            item = {**page.items[0], "message_id": query.args["message_id"]}
            return type(page)((item,), page.provenance, None)

    engine = ContextEngine(
        world["conn"], {"telegram_user": Echo()}, clock=lambda: NOW, monotonic=Clock()
    )
    sent_id = int(
        world["conn"]
        .execute(
            "SELECT provider_identity FROM provider_objects WHERE ref = ?", (result["message"],)
        )
        .fetchone()[0]
        .rsplit(":", 1)[1]
    )
    page = engine.around_message(world["grp"], world["user"], sent_id, before=0, after=0)
    assert result["message"] in {i["message_ref"] for i in page["items"]}


def test_reply_names_the_message_by_ref(world):
    service, admins = _service(world)
    original = _seen(world, world["bot"], 5)
    service.send(CTX, world["grp"], _both(world), "Merci", _req(), reply_to=original)
    assert admins["telegram_bot"].calls == [
        (C.MESSAGE_SEND, {"text": "Merci", "reply_to_message_id": 5})
    ]


def test_edit_and_pin_by_ref(world):
    service, admins = _service(world)
    message = _seen(world, world["bot"], 9)
    edited = service.edit(CTX, world["grp"], _both(world), message, "Salaam!", _req())
    pinned = service.pin(CTX, world["grp"], _both(world), message, _req())
    unpinned = service.pin(CTX, world["grp"], _both(world), message, _req(), pinned=False)
    assert edited["result"] == pinned["result"] == unpinned["result"] == "SUCCEEDED"
    assert [c for c, _a in admins["telegram_bot"].calls] == [
        C.MESSAGE_EDIT,
        C.MESSAGE_PIN,
        C.MESSAGE_PIN,
    ]
    assert admins["telegram_bot"].calls[0][1] == {"message_id": 9, "text": "Salaam!"}
    assert admins["telegram_bot"].calls[2][1] == {"message_id": 9, "pinned": False}


@pytest.mark.parametrize(
    ("reported", "expected"),
    [({"scope": "everyone"}, "everyone"), ({"scope": "local"}, "local"), ({}, "provider_defined")],
)
def test_delete_scope_reported_truthfully(world, reported, expected):
    service, admins = _service(world, details={C.MESSAGE_DELETE: reported})
    message = _seen(world, world["user"], 7)
    result = service.delete(
        CTX, world["grp"], {"telegram_user": world["user"]}, message, _req(), scope="everyone"
    )
    assert result["result"] == "SUCCEEDED" and result["scope"] == expected  # never over-claimed
    assert admins["telegram_user"].calls == [(C.MESSAGE_DELETE, {"message_id": 7, "revoke": True})]


def test_a_failed_delete_claims_no_scope(world):
    service, _admins = _service(world, outcome="refused")
    message = _seen(world, world["bot"], 7)
    result = service.delete(CTX, world["grp"], _both(world), message, _req())
    assert result["result"] == "FAILED" and result["scope"] is None


def test_a_message_of_another_group_is_not_found(world):
    service, admins = _service(world)
    elsewhere = ProviderTarget("telegram", "telegram_bot", world["bot"].destination_ref, "-78")
    message = _seen(world, elsewhere, 5)
    with pytest.raises(CommsError) as refused:
        service.edit(CTX, world["grp"], _both(world), message, "x", _req())
    assert refused.value.code == "NOT_FOUND" and admins["telegram_bot"].calls == []


def test_mark_read_is_a_write_and_never_hidden_in_a_read(world):
    conn = world["conn"]
    service, admins = _service(world)
    source = Source(provenance="whatsapp_webhook_archive")
    engine = ContextEngine(conn, {"whatsapp_cloud": source}, clock=lambda: NOW, monotonic=Clock())
    page = engine.archive("rcp_x", WA_CONTACT, limit=2)  # reading marks nothing
    assert admins["whatsapp_cloud"].calls == [] and {q.kind for q in source.queries} == {"recent"}
    assert conn.execute("SELECT count(*) FROM mutations").fetchone()[0] == 0
    message = page["items"][0]["message_ref"]
    result = service.mark_read(CTX, "rcp_x", {"whatsapp_cloud": WA_CONTACT}, message, _req())
    assert result["result"] == "SUCCEEDED"
    ((capability, args),) = admins["whatsapp_cloud"].calls
    assert capability is C.MESSAGE_MARK_READ and set(args) == {"message_id"}
    tool = conn.execute("SELECT tool FROM mutations").fetchone()[0]
    assert tool == "comms_message_mark_read"  # an audited write of its own


def test_forward_is_not_offered_yet(world):
    service, _admins = _service(world)
    with pytest.raises(CommsError) as refused:
        service.forward(CTX, world["grp"], _both(world), _seen(world, world["bot"]), _req())
    assert refused.value.code == "PROVIDER_UNSUPPORTED"
