"""comms v0.3 Task D19: the message tools (P §23, §72, §77)."""

import pytest

from comms.core import refs
from comms.core.campaigns.directory import destination_id
from comms.core.objects import message_identity, object_ref
from comms.core.providers.capability import Capability as C
from comms.core.providers.protocols import ProviderTarget
from comms.mcp.tools.messages import MESSAGE_TOOLS
from comms.services.context import ContextEngine
from comms.services.messages import MessageService
from tests.core.campaign_helpers import NOW
from tests.mcp import family
from tests.services.context_fixtures import Clock, Source
from tests.services.group_fixtures import CTX, fixtures, group_world

BY_NAME = {spec.name: spec for spec in MESSAGE_TOOLS}
NAMES = sorted(BY_NAME)
CAPABILITY = {
    "comms_message_send": C.MESSAGE_SEND,
    "comms_message_reply": C.MESSAGE_SEND,
    "comms_message_edit": C.MESSAGE_EDIT,
    "comms_message_delete": C.MESSAGE_DELETE,
    "comms_message_forward": C.MESSAGE_FORWARD,
    "comms_message_pin": C.MESSAGE_PIN,
    "comms_message_unpin": C.MESSAGE_PIN,
    "comms_message_mark_read": C.MESSAGE_MARK_READ,
}
WA_CONTACT = ProviderTarget("whatsapp", "whatsapp_cloud", "rct_w", "+61400000001")


@pytest.fixture(scope="module")
def results(tmp_path_factory):
    world = group_world(tmp_path_factory.mktemp("msg"))
    conn = world["conn"]
    capability, executor, _admins = fixtures(world)
    service = MessageService(conn, capability, executor)
    grp, bot, user = world["grp"], world["bot"], world["user"]
    both = {"telegram_bot": bot, "telegram_user": user}

    def seen(target, message_id):
        return object_ref(
            conn,
            "message",
            target.transport,
            target.actor,
            destination_id(conn, target.destination_ref),
            message_identity(target.identity, message_id),
            now=NOW,
        )

    def req():
        return refs.mint("request")

    message = seen(bot, 5)
    engine = ContextEngine(
        conn,
        {"telegram_bot": Source(provenance="telegram_local")},
        clock=lambda: NOW,
        monotonic=Clock(),
    )
    page = engine.recent(grp, bot, limit=2)
    page = {**page, "next_cursor": None}
    sent = service.send(CTX, grp, both, "Salaam", req())
    archive = ContextEngine(
        conn,
        {"whatsapp_cloud": Source(provenance="whatsapp_webhook_archive")},
        clock=lambda: NOW,
        monotonic=Clock(),
    )
    wa_message = archive.archive("rcp_" + "w" * 26, WA_CONTACT, limit=1)["items"][0]["message_ref"]
    produced = {
        "comms_message_get": page,
        "comms_message_recent": page,
        "comms_message_search": {
            "items": page["items"],
            "stopped_by": None,
            "requests": 1,
            "groups": 1,
        },
        "comms_message_context": page,
        "comms_message_send": sent,
        "comms_message_reply": service.send(CTX, grp, both, "Merci", req(), reply_to=message),
        "comms_message_edit": service.edit(CTX, grp, both, message, "Salaam!", req()),
        "comms_message_delete": service.delete(CTX, grp, both, message, req()),
        "comms_message_forward": sent,  # never produced: the service answers PROVIDER_UNSUPPORTED
        "comms_message_pin": service.pin(CTX, grp, both, message, req()),
        "comms_message_unpin": service.pin(CTX, grp, both, message, req(), pinned=False),
        "comms_message_mark_read": service.mark_read(
            CTX, "rcp_" + "w" * 26, {"whatsapp_cloud": WA_CONTACT}, wa_message, req()
        ),
    }
    examples = {
        "comms_message_get": {"group": grp, "message": message},
        "comms_message_recent": {"group": grp, "limit": 2},
        "comms_message_search": {"groups": [grp], "query": "hello"},
        "comms_message_context": {"group": grp, "message": message, "after": 2},
        "comms_message_send": {"group": grp, "text": "Salaam", "actor": "telegram_user"},
        "comms_message_reply": {"group": grp, "message": message, "text": "Merci"},
        "comms_message_edit": {"group": grp, "message": message, "text": "Salaam!"},
        "comms_message_delete": {"group": grp, "message": message, "scope": "everyone"},
        "comms_message_forward": {"group": grp, "message": message, "to_group": grp},
        "comms_message_pin": {"group": grp, "message": message},
        "comms_message_unpin": {"group": grp, "message": message},
        "comms_message_mark_read": {"conversation": "rcp_" + "w" * 26, "message": wa_message},
    }
    return {"results": produced, "examples": examples}


def test_the_family_is_p23():
    tools = (
        "get",
        "recent",
        "search",
        "context",
        "send",
        "reply",
        "edit",
        "delete",
        "forward",
        "pin",
        "unpin",
        "mark_read",
    )
    assert NAMES == sorted(f"comms_message_{t}" for t in tools)


@pytest.mark.parametrize("name", NAMES)
def test_schema_valid_json_schema_2020_12(name):
    family.schema_valid(BY_NAME[name])


@pytest.mark.parametrize("name", NAMES)
def test_output_schema_matches_service_result(name, results):
    family.output_matches(BY_NAME[name], results["results"][name])


@pytest.mark.parametrize("name", NAMES)
def test_annotations(name):
    family.annotations(BY_NAME[name], CAPABILITY.get(name))
    assert BY_NAME[name].read_only == (name not in CAPABILITY)


@pytest.mark.parametrize("name", sorted(CAPABILITY))
def test_write_requires_request_id(name, results):
    family.write_requires_request_id(BY_NAME[name], results["examples"][name])


@pytest.mark.parametrize("name", NAMES)
def test_dispatch_reaches_its_service(name, results):
    family.dispatch_reaches_its_service(
        BY_NAME[name], results["examples"][name], results["results"][name]
    )


def test_delete_is_destructive_and_mark_read_is_not():
    assert BY_NAME["comms_message_delete"].destructive
    assert not BY_NAME["comms_message_mark_read"].destructive
    assert BY_NAME["comms_message_edit"].idempotent  # a set-state write
    assert BY_NAME["comms_message_edit"].destructive  # that overwrites the old text


def test_a_delete_claiming_an_unknown_scope_fails_the_schema(results):
    claimed = {**results["results"]["comms_message_delete"], "scope": "global"}
    with pytest.raises(AssertionError):
        family.output_matches(BY_NAME["comms_message_delete"], claimed)
