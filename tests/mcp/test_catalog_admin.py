"""comms v0.3 Task D21: admin, permission, info, invite, join-request, topic and lifecycle tools
(P §26–29, §74)."""

import pytest

from comms.core import refs
from comms.core.providers.capability import Capability as C
from comms.mcp.tools.admin import ADMIN_TOOLS
from comms.services.groups import GroupService
from tests.mcp import family
from tests.services.group_fixtures import CTX, fixtures, group_world

BY_NAME = {spec.name: spec for spec in ADMIN_TOOLS}
NAMES = sorted(BY_NAME)
# tool → (capability, service tool, service args, is a member operation)
WRITES = {
    "comms_group_admin_promote": (
        C.ADMIN_PROMOTE,
        "group.admin.promote",
        {"profile": "moderator"},
        True,
    ),
    "comms_group_admin_update_rights": (
        C.ADMIN_PROMOTE,
        "group.admin.update_rights",
        {"profile": "custom", "rights": {"can_pin_messages": True}},
        True,
    ),
    "comms_group_admin_demote": (C.ADMIN_DEMOTE, "group.admin.demote", {}, True),
    "comms_group_permissions_set": (
        C.CHAT_SET_PERMISSIONS,
        "group.permissions.set",
        {"permissions": {"can_send_messages": True}},
        False,
    ),
    "comms_group_info_set_title": (
        C.CHAT_SET_TITLE,
        "group.info.set_title",
        {"title": "Nowruz"},
        False,
    ),
    "comms_group_info_set_description": (
        C.CHAT_SET_DESCRIPTION,
        "group.info.set_description",
        {"description": "Sal-e no"},
        False,
    ),
    "comms_group_info_set_photo": (C.CHAT_SET_PHOTO, None, {"media": "med_" + "a" * 26}, False),
    "comms_group_invite_create": (
        C.INVITE_CREATE,
        "group.invite.create",
        {"member_limit": 5},
        False,
    ),
    "comms_group_invite_edit": (C.INVITE_EDIT, "group.invite.edit", {"member_limit": 9}, False),
    "comms_group_invite_revoke": (C.INVITE_REVOKE, "group.invite.revoke", {}, False),
    "comms_group_join_requests_approve": (
        C.JOIN_REQUEST_APPROVE,
        "group.join_requests.approve",
        {},
        True,
    ),
    "comms_group_join_requests_reject": (
        C.JOIN_REQUEST_REJECT,
        "group.join_requests.reject",
        {},
        True,
    ),
    "comms_group_topic_create": (C.TOPIC_CREATE, "group.topic.create", {"name": "Events"}, False),
    "comms_group_topic_edit": (C.TOPIC_EDIT, "group.topic.edit", {"name": "Nowruz events"}, False),
    "comms_group_topic_close": (C.TOPIC_CLOSE, "group.topic.close", {}, False),
    "comms_group_topic_reopen": (C.TOPIC_REOPEN, "group.topic.reopen", {}, False),
    "comms_group_delete": (C.GROUP_DELETE, "group.delete", {}, False),
    "comms_group_migrate": (C.GROUP_MIGRATE, "group.migrate", {}, False),
}
USER_ONLY = {"comms_group_delete", "comms_group_migrate"}


@pytest.fixture(scope="module")
def results(tmp_path_factory):
    world = group_world(tmp_path_factory.mktemp("adm"))
    grp, rcp, bot, user = (world[k] for k in ("grp", "rcp", "bot", "user"))
    capability, executor, _admins = fixtures(world)
    service = GroupService(world["conn"], capability, executor)

    def req():
        return refs.mint("request")

    invite = service.admin(CTX, "group.invite.create", grp, {"telegram_bot": bot}, {}, req())[
        "object"
    ]
    topic = service.admin(
        CTX, "group.topic.create", grp, {"telegram_bot": bot}, {"name": "T"}, req()
    )["object"]
    objects = {
        "comms_group_invite_edit": {"invite": invite},
        "comms_group_invite_revoke": {"invite": invite},
        "comms_group_topic_edit": {"topic": topic},
        "comms_group_topic_close": {"topic": topic},
        "comms_group_topic_reopen": {"topic": topic},
    }
    produced, examples = {}, {}
    for name, (_cap, tool, args, member) in WRITES.items():
        args = {**args, **objects.get(name, {})}
        targets = {"telegram_user": user} if name in USER_ONLY else {"telegram_bot": bot}
        if tool is None:  # not offered: the result a success would carry
            produced[name] = {
                **produced["comms_group_info_set_title"],
                "operation": "group.info.set_photo",
            }
        elif member:
            produced[name] = service.member(CTX, tool, grp, targets, rcp, args, req())
        else:
            produced[name] = service.admin(CTX, tool, grp, targets, args, req())
        examples[name] = {"group": grp, **({"recipient": rcp} if member else {}), **args}
    listed = {"group": grp, "items": [], "next_cursor": None}
    for name in (
        "comms_group_invite_list",
        "comms_group_join_requests_list",
        "comms_group_topic_list",
        "comms_group_admin_log",
    ):
        produced[name], examples[name] = listed, {"group": grp}
    produced["comms_group_permissions_get"] = {
        "group": grp,
        "permissions": {"can_send_messages": True},
    }
    examples["comms_group_permissions_get"] = {"group": grp}
    produced["comms_group_topic_get"] = {"group": grp, "topic": topic, "name": "T", "closed": False}
    examples["comms_group_topic_get"] = {"group": grp, "topic": topic}
    return {"results": produced, "examples": examples}


def test_the_family_covers_p26_to_p29():
    assert set(NAMES) == set(WRITES) | {
        "comms_group_permissions_get",
        "comms_group_invite_list",
        "comms_group_join_requests_list",
        "comms_group_topic_list",
        "comms_group_topic_get",
        "comms_group_admin_log",
    }


@pytest.mark.parametrize("name", NAMES)
def test_schema_valid_json_schema_2020_12(name):
    family.schema_valid(BY_NAME[name])


@pytest.mark.parametrize("name", NAMES)
def test_output_schema_matches_service_result(name, results):
    family.output_matches(BY_NAME[name], results["results"][name])


@pytest.mark.parametrize("name", NAMES)
def test_annotations(name):
    family.annotations(BY_NAME[name], WRITES[name][0] if name in WRITES else None)
    assert BY_NAME[name].read_only == (name not in WRITES)


@pytest.mark.parametrize("name", sorted(WRITES))
def test_write_requires_request_id(name, results):
    family.write_requires_request_id(BY_NAME[name], results["examples"][name])


@pytest.mark.parametrize("name", NAMES)
def test_dispatch_reaches_its_service(name, results):
    family.dispatch_reaches_its_service(
        BY_NAME[name], results["examples"][name], results["results"][name]
    )


@pytest.mark.parametrize(
    "name",
    [
        "comms_group_admin_demote",
        "comms_group_admin_update_rights",
        "comms_group_permissions_set",
        "comms_group_info_set_title",
        "comms_group_info_set_description",
        "comms_group_info_set_photo",
        "comms_group_invite_revoke",
        "comms_group_join_requests_reject",
        "comms_group_delete",
        "comms_group_migrate",
    ],
)
def test_the_part_a_destructive_list_is_annotated(name):
    assert BY_NAME[name].destructive


def test_promote_requires_explicit_rights_profile(results):
    spec = BY_NAME["comms_group_admin_promote"]
    example = {
        k: v for k, v in results["examples"]["comms_group_admin_promote"].items() if k != "profile"
    }
    with pytest.raises(AssertionError):
        family.write_requires_request_id(spec, example)
    for bad in ("admin", "all", "everything"):
        with pytest.raises(AssertionError):
            family.write_requires_request_id(spec, {**example, "profile": bad})


def test_invites_and_topics_are_named_by_ref(results):
    edit = BY_NAME["comms_group_invite_edit"].input_schema["properties"]
    assert "invite" in edit and "invite_link" not in edit
    topic = BY_NAME["comms_group_topic_close"].input_schema["properties"]
    assert "topic" in topic and "message_thread_id" not in topic
    assert results["results"]["comms_group_invite_create"]["object"].startswith("inv_")
