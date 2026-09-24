"""comms v0.3 Task D12: group membership and admin services (P §25, §26, §73, §74)."""

import pytest

from comms.core.campaigns import directory as d
from comms.core.errors import CommsError
from comms.core.providers.capability import Capability as C
from comms.core.providers.capability import CapabilityState as S
from comms.services.groups import MEMBERSHIP
from tests.core.campaign_helpers import NOW
from tests.services.group_fixtures import CTX, TG_USER, group_service, group_world


@pytest.fixture
def world(tmp_path):
    return group_world(tmp_path)


def _service(world, *, state=S.AVAILABLE, outcome="ok"):
    return group_service(world, state=state, outcome=outcome)


ARGS = {
    "group.member.remove": {},
    "group.member.ban": {},
    "group.member.unban": {},
    "group.member.restrict": {"permissions": {"can_send_messages": False}},
    "group.member.unrestrict": {},
    "group.admin.promote": {"profile": "moderator"},
    "group.admin.update_rights": {"profile": "custom", "rights": {"can_pin_messages": True}},
    "group.admin.demote": {},
}
BOT_TOOLS = sorted(ARGS)


def _call(service, world, tool, *, request="a", target="bot", **kw):
    return service.member(
        CTX,
        tool,
        world["grp"],
        {world[target].actor: world[target]},
        world["rcp"],
        ARGS.get(tool, {}),
        "req_" + request * 26,
        **kw,
    )


@pytest.mark.parametrize("tool", BOT_TOOLS)
def test_success(world, tool):
    service, admins = _service(world)
    result = _call(service, world, tool)
    assert result["result"] == "SUCCEEDED" and result["actor"] == "telegram_bot"
    assert result["group"] == world["grp"] and result["recipient"] == world["rcp"]
    assert result["op_ref"].startswith("op_") and admins["telegram_bot"].calls
    assert all(args["user_id"] == int(TG_USER) for _c, args in admins["telegram_bot"].calls)


@pytest.mark.parametrize("tool", BOT_TOOLS)
def test_provider_refusal_is_not_authorized(world, tool):
    service, _admins = _service(world, outcome="refused")
    result = _call(service, world, tool)
    assert (result["result"], result["code"]) == ("FAILED", "NOT_AUTHORIZED")


@pytest.mark.parametrize("tool", BOT_TOOLS)
def test_provider_unsupported_is_refused_before_a_call(world, tool):
    service, admins = _service(world, state=S.PROVIDER_UNSUPPORTED)
    with pytest.raises(CommsError) as refused:
        _call(service, world, tool)
    assert refused.value.code == "PROVIDER_UNSUPPORTED" and admins["telegram_bot"].calls == []


@pytest.mark.parametrize("tool", BOT_TOOLS)
def test_ambiguity_is_reported_not_guessed(world, tool):
    service, _admins = _service(world, outcome="unknown")
    assert _call(service, world, tool)["result"] == "OUTCOME_UNKNOWN"


def test_member_remove_result_is_structured_truth(world):
    service, admins = _service(world)
    result = _call(service, world, "group.member.remove")
    assert {k: result[k] for k in ("group", "recipient", "operation", "result", "actor")} == {
        "group": world["grp"],
        "recipient": world["rcp"],
        "operation": "remove",
        "result": "SUCCEEDED",
        "actor": "telegram_bot",
    }
    assert [c for c, _a in admins["telegram_bot"].calls] == [C.MEMBER_BAN, C.MEMBER_UNBAN]
    refused, _ = _service(world, outcome="refused")
    again = _call(refused, world, "group.member.remove", request="b")
    assert again["result"] == "FAILED" and again["code"] == "NOT_AUTHORIZED"  # never false success


def test_whatsapp_member_remove(world):
    service, admins = _service(world)
    result = _call(service, world, "group.member.remove", target="wa")
    assert result["result"] == "SUCCEEDED" and result["actor"] == "whatsapp_cloud"
    assert admins["whatsapp_cloud"].calls == [(C.GROUP_MEMBER_REMOVE, {"wa_id": "61400000001"})]


def test_whatsapp_has_no_ban(world):
    service, admins = _service(world)
    with pytest.raises(CommsError) as refused:
        _call(service, world, "group.member.ban", target="wa")
    assert refused.value.code == "PROVIDER_UNSUPPORTED" and admins["whatsapp_cloud"].calls == []


@pytest.mark.parametrize(
    "args",
    [{}, {"profile": "admin"}, {"profile": "custom"}, {"profile": "custom", "rights": {}}],
)
def test_promote_requires_explicit_rights_profile(world, args):
    service, admins = _service(world)
    with pytest.raises(CommsError) as refused:
        service.member(
            CTX,
            "group.admin.promote",
            world["grp"],
            {"telegram_bot": world["bot"]},
            world["rcp"],
            args,
            "req_" + "p" * 26,
        )
    assert refused.value.code == "INVALID_ARGUMENT" and admins["telegram_bot"].calls == []
    assert world["conn"].execute("SELECT count(*) FROM mutations").fetchone()[0] == 0


def test_unrestrict_grants_every_permission_again(world):
    service, admins = _service(world)
    _call(service, world, "group.member.unrestrict")
    ((capability, args),) = admins["telegram_bot"].calls
    assert capability is C.MEMBER_RESTRICT and args["permissions"] == "all"


def test_member_add_never_becomes_another_operation(world):
    service, admins = _service(world)
    with pytest.raises(CommsError) as refused:  # the bot cannot add; it is never an invite
        _call(service, world, "group.member.add")
    assert refused.value.code == "PROVIDER_UNSUPPORTED" and admins["telegram_bot"].calls == []


def test_a_recipient_without_an_identity_on_the_transport_is_not_found(world):
    conn = world["conn"]
    lonely = d.add_recipient(conn, now=NOW)
    service, _admins = _service(world)
    with pytest.raises(CommsError) as refused:
        service.member(
            CTX,
            "group.member.ban",
            world["grp"],
            {"telegram_bot": world["bot"]},
            lonely,
            {},
            "req_" + "l" * 26,
        )
    assert refused.value.code == "NOT_FOUND"


def test_unknown_tool_is_refused(world):
    service, _admins = _service(world)
    with pytest.raises(CommsError) as refused:
        _call(service, world, "group.member.teleport")
    assert refused.value.code == "INVALID_ARGUMENT"


def test_membership_table_names_the_p25_p27_member_operations():
    assert set(MEMBERSHIP) == {
        "group.member.add",
        "group.member.invite",
        "group.member.remove",
        "group.member.ban",
        "group.member.unban",
        "group.member.restrict",
        "group.member.unrestrict",
        "group.admin.promote",
        "group.admin.demote",
        "group.admin.update_rights",
        "group.join_requests.approve",
        "group.join_requests.reject",
    }


def test_an_actor_that_can_never_perform_the_operation_is_never_chosen(world):
    """Even if a snapshot says AVAILABLE, the bot has no member.add: the user actor is chosen."""
    service, admins = _service(world)
    targets = {"telegram_bot": world["bot"], "telegram_user": world["user"]}
    result = service.member(
        CTX, "group.member.add", world["grp"], targets, world["rcp"], {}, "req_" + "x" * 26
    )
    assert result["actor"] == "telegram_user" and admins["telegram_bot"].calls == []
