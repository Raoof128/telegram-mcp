"""comms v0.3 Task D20: group inspection and membership tools (P §24–25, §73)."""

import pytest

from comms.core import refs
from comms.core.providers.capability import Capability as C
from comms.mcp.tools.groups import GROUP_TOOLS
from comms.services.context import ContextEngine
from comms.services.groups import GroupService
from tests.core.campaign_helpers import NOW
from tests.mcp import family
from tests.services.context_fixtures import Clock, Source
from tests.services.group_fixtures import CTX, fixtures, group_world

BY_NAME = {spec.name: spec for spec in GROUP_TOOLS}
NAMES = sorted(BY_NAME)
MEMBER = {
    "add": C.MEMBER_ADD,
    "invite": C.INVITE_CREATE,
    "remove": C.MEMBER_REMOVE,
    "ban": C.MEMBER_BAN,
    "unban": C.MEMBER_UNBAN,
    "restrict": C.MEMBER_RESTRICT,
    "unrestrict": C.MEMBER_RESTRICT,
}
CAPABILITY = {f"comms_group_member_{k}": v for k, v in MEMBER.items()}
EXTRA = {
    "restrict": {"permissions": {"can_send_messages": False}},
    "ban": {"until_date": 0, "revoke_messages": False},
}


@pytest.fixture(scope="module")
def results(tmp_path_factory):
    world = group_world(tmp_path_factory.mktemp("grp"))
    conn, grp, rcp, bot, user = (world[k] for k in ("conn", "grp", "rcp", "bot", "user"))
    capability, executor, _admins = fixtures(world)
    service = GroupService(conn, capability, executor)
    engine = ContextEngine(conn, {"telegram_user": Source()}, clock=lambda: NOW, monotonic=Clock())

    def untokened(page):
        return {**page, "next_cursor": None}

    context = engine.get(grp, user, include=("messages", "members", "admins"), message_limit=2)
    context = {k: untokened(v) if isinstance(v, dict) else v for k, v in context.items()}
    produced = {
        "comms_group_list": service.list(),
        "comms_group_get": service.get(grp),
        "comms_group_context": context,
        "comms_group_capabilities": capability.for_group(grp, {"telegram_bot": bot}),
        "comms_group_members_list": context["members"],
        "comms_group_members_get": {"group": grp, "recipient": rcp, "role": None, "status": None},
        "comms_group_admins_list": context["admins"],
    }
    examples = {
        "comms_group_list": {"limit": 10},
        "comms_group_get": {"group": grp},
        "comms_group_context": {"group": grp, "message_limit": 2},
        "comms_group_capabilities": {"group": grp},
        "comms_group_members_list": {"group": grp},
        "comms_group_members_get": {"group": grp, "recipient": rcp},
        "comms_group_admins_list": {"group": grp},
    }
    for tool in MEMBER:
        name = f"comms_group_member_{tool}"
        args = EXTRA.get(tool, {})
        produced[name] = service.member(
            CTX,
            f"group.member.{tool}",
            grp,
            {"telegram_user": user} if tool == "add" else {"telegram_bot": bot},
            rcp,
            args,
            refs.mint("request"),
        )
        examples[name] = {"group": grp, "recipient": rcp, **args}
    return {"results": produced, "examples": examples}


def test_the_family_is_p24_and_p25():
    reads = ("list", "get", "context", "capabilities", "members_list", "members_get", "admins_list")
    expected = [f"comms_group_{r}" for r in reads] + [f"comms_group_member_{m}" for m in MEMBER]
    assert NAMES == sorted(expected)


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


def test_restrict_requires_explicit_permissions(results):
    example = {
        k: v
        for k, v in results["examples"]["comms_group_member_restrict"].items()
        if k != "permissions"
    }
    with pytest.raises(AssertionError):
        family.write_requires_request_id(BY_NAME["comms_group_member_restrict"], example)


def test_a_member_is_named_by_recipient_ref_never_by_provider_id():
    for tool in MEMBER:
        properties = BY_NAME[f"comms_group_member_{tool}"].input_schema["properties"]
        assert "recipient" in properties
        assert not {"user_id", "wa_id", "identity", "phone"} & set(properties)


def test_group_views_carry_no_identity(results):
    text = repr([results["results"]["comms_group_list"], results["results"]["comms_group_get"]])
    assert "-77" not in text and "group:77" not in text
