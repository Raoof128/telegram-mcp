"""comms v0.3 Task D13: group info, invites, join requests, topics and lifecycle (P §25–29)."""

import pytest

from comms.core import refs
from comms.core.errors import CommsError
from comms.core.providers.capability import Capability as C
from comms.core.providers.capability import CapabilityState as S
from comms.services.groups import ADMIN
from tests.services.group_fixtures import CTX, TG_USER, group_service, group_world


@pytest.fixture
def world(tmp_path):
    return group_world(tmp_path)


_REQ = iter(refs.mint("request") for _ in range(10_000))


def _admin(service, world, tool, args=None, *, target="bot", request=None, **kw):
    return service.admin(
        CTX,
        tool,
        world["grp"],
        {world[target].actor: world[target]},
        args or {},
        request or next(_REQ),
        **kw,
    )


def _invite(service, world, target="bot"):
    return _admin(service, world, "group.invite.create", {"name": "spring"}, target=target)


# tool → (arguments, the capability the provider sees, the actor that performs it)
TELEGRAM = {
    "group.info.set_title": ({"title": "Nowruz"}, C.CHAT_SET_TITLE, "bot"),
    "group.info.set_description": ({"description": "Sal-e no"}, C.CHAT_SET_DESCRIPTION, "bot"),
    "group.permissions.set": (
        {"permissions": {"can_send_messages": True}},
        C.CHAT_SET_PERMISSIONS,
        "bot",
    ),
    "group.invite.create": ({"member_limit": 5}, C.INVITE_CREATE, "bot"),
    "group.topic.create": ({"name": "Events"}, C.TOPIC_CREATE, "bot"),
    "group.delete": ({}, C.GROUP_DELETE, "user"),
    "group.migrate": ({}, C.GROUP_MIGRATE, "user"),
}


@pytest.mark.parametrize("tool", sorted(TELEGRAM))
def test_success(world, tool):
    args, capability, target = TELEGRAM[tool]
    service, admins = group_service(world)
    result = _admin(service, world, tool, args, target=target)
    assert result["result"] == "SUCCEEDED" and result["group"] == world["grp"]
    assert result["operation"] == tool and result["op_ref"].startswith("op_")
    assert [c for c, _a in admins[world[target].actor].calls] == [capability]


@pytest.mark.parametrize("tool", sorted(TELEGRAM))
def test_provider_refusal_is_structured(world, tool):
    args, _capability, target = TELEGRAM[tool]
    service, _admins = group_service(world, outcome="refused")
    result = _admin(service, world, tool, args, target=target)
    assert (result["result"], result["code"]) == ("FAILED", "NOT_AUTHORIZED")


@pytest.mark.parametrize("tool", sorted(TELEGRAM))
def test_unsupported_state_is_refused_before_a_call(world, tool):
    args, _capability, target = TELEGRAM[tool]
    service, admins = group_service(world, state=S.PROVIDER_UNSUPPORTED)
    with pytest.raises(CommsError) as refused:
        _admin(service, world, tool, args, target=target)
    assert refused.value.code == "PROVIDER_UNSUPPORTED"
    assert all(a.calls == [] for a in admins.values())


@pytest.mark.parametrize("tool", sorted(TELEGRAM))
def test_ambiguity_is_reported(world, tool):
    args, _capability, target = TELEGRAM[tool]
    service, _admins = group_service(world, outcome="unknown")
    assert _admin(service, world, tool, args, target=target)["result"] == "OUTCOME_UNKNOWN"


@pytest.mark.parametrize(
    ("tool", "kind"), [("group.invite.create", "inv_"), ("group.topic.create", "top_")]
)
def test_a_create_returns_its_object_ref(world, tool, kind):
    args, _capability, _target = TELEGRAM[tool]
    service, _admins = group_service(world)
    result = _admin(service, world, tool, args)
    assert result["object"].startswith(kind)


def test_invite_edit_and_revoke_name_the_invite_by_ref(world):
    service, admins = group_service(world)
    invite = _invite(service, world)["object"]
    edited = _admin(service, world, "group.invite.edit", {"invite": invite, "member_limit": 9})
    revoked = _admin(service, world, "group.invite.revoke", {"invite": invite})
    assert edited["result"] == revoked["result"] == "SUCCEEDED"
    calls = admins["telegram_bot"].calls
    assert [c for c, _a in calls] == [C.INVITE_CREATE, C.INVITE_EDIT, C.INVITE_REVOKE]
    assert calls[1][1]["invite_link"].startswith("https://t.me/+")
    assert calls[1][1]["member_limit"] == 9 and "invite" not in calls[1][1]
    assert "t.me" not in repr((edited, revoked))


def test_an_invite_ref_of_another_group_is_not_found(world):
    service, _admins = group_service(world)
    with pytest.raises(CommsError) as refused:
        _admin(service, world, "group.invite.revoke", {"invite": "inv_" + "z" * 26})
    assert refused.value.code in ("NOT_FOUND", "INVALID_ARGUMENT")


def test_topic_edit_close_reopen_by_ref(world):
    service, admins = group_service(world)
    topic = _admin(service, world, "group.topic.create", {"name": "Events"})["object"]
    for tool, extra in (
        ("group.topic.edit", {"name": "Nowruz events"}),
        ("group.topic.close", {}),
        ("group.topic.reopen", {}),
    ):
        assert _admin(service, world, tool, {"topic": topic, **extra})["result"] == "SUCCEEDED"
    threads = {a["message_thread_id"] for _c, a in admins["telegram_bot"].calls[1:]}
    assert len(threads) == 1 and all(type(t) is int for t in threads)


@pytest.mark.parametrize("tool", ["group.topic.hide", "group.topic.unhide", "group.info.set_photo"])
def test_operations_no_adapter_performs_are_unsupported(world, tool):
    service, admins = group_service(world)
    with pytest.raises(CommsError) as refused:
        _admin(service, world, tool, {})
    assert refused.value.code == "PROVIDER_UNSUPPORTED" and admins["telegram_bot"].calls == []


def test_group_delete_destructive_resolve_only(world):
    service, admins = group_service(world, outcome="unknown")
    request = "req_" + "d" * 26
    first = _admin(service, world, "group.delete", target="user", request=request)
    again = _admin(service, world, "group.delete", target="user", request=request)
    assert first["result"] == again["result"] == "OUTCOME_UNKNOWN" and again["replayed"]
    assert len(admins["telegram_user"].calls) == 1  # never re-sent after an ambiguous outcome
    row = world["conn"].execute("SELECT retry_class, ambiguity_policy FROM mutations").fetchone()
    assert tuple(row) == ("DESTRUCTIVE_NONIDEMPOTENT", "resolve_only")


def test_whatsapp_settings_and_invite_reset(world):
    service, admins = group_service(world)
    title = _admin(service, world, "group.info.set_title", {"title": "Nowruz"}, target="wa")
    revoked = _admin(service, world, "group.invite.revoke", {}, target="wa")
    assert title["result"] == revoked["result"] == "SUCCEEDED"
    assert revoked["object"].startswith("inv_")  # the reset issues the replacement link
    assert admins["whatsapp_cloud"].calls[0] == (C.GROUP_SETTINGS_UPDATE, {"subject": "Nowruz"})


def test_invite_required_returned_with_invite_object(world):
    service, admins = group_service(world, outcome="INVITE_REQUIRED")
    add = service.member(
        CTX,
        "group.member.add",
        world["grp"],
        {"telegram_user": world["user"]},
        world["rcp"],
        {},
        "req_" + "a" * 26,
    )
    assert add["result"] == "INVITE_REQUIRED" and add["invite"] is None
    assert [c for c, _a in admins["telegram_user"].calls] == [C.MEMBER_ADD]  # never an invite
    ok, _ = group_service(world)
    invite = _invite(ok, world)["object"]
    again = service.member(
        CTX,
        "group.member.add",
        world["grp"],
        {"telegram_user": world["user"]},
        world["rcp"],
        {},
        "req_" + "b" * 26,
    )
    assert again["result"] == "INVITE_REQUIRED" and again["invite"] == invite


def test_whatsapp_add_is_invite_required_without_a_call(world):
    service, admins = group_service(world)
    add = service.member(
        CTX,
        "group.member.add",
        world["grp"],
        {"whatsapp_cloud": world["wa"]},
        world["rcp"],
        {},
        "req_" + "w" * 26,
    )
    assert add["result"] == "INVITE_REQUIRED" and admins["whatsapp_cloud"].calls == []


@pytest.mark.parametrize(
    ("tool", "capability"),
    [
        ("group.join_requests.approve", C.JOIN_REQUEST_APPROVE),
        ("group.join_requests.reject", C.JOIN_REQUEST_REJECT),
    ],
)
def test_join_requests_name_the_recipient(world, tool, capability):
    service, admins = group_service(world)
    result = service.member(
        CTX,
        tool,
        world["grp"],
        {"telegram_bot": world["bot"]},
        world["rcp"],
        {},
        "req_" + "j" * 26,
    )
    assert result["result"] == "SUCCEEDED" and result["recipient"] == world["rcp"]
    assert admins["telegram_bot"].calls == [(capability, {"user_id": int(TG_USER)})]


def test_member_invite_is_a_single_use_invite_object(world):
    service, admins = group_service(world)
    result = service.member(
        CTX,
        "group.member.invite",
        world["grp"],
        {"telegram_bot": world["bot"]},
        world["rcp"],
        {},
        "req_" + "i" * 26,
    )
    assert result["result"] == "SUCCEEDED" and result["invite"].startswith("inv_")
    assert admins["telegram_bot"].calls == [(C.INVITE_CREATE, {"member_limit": 1})]


def test_admin_table_names_p26_to_p29():
    assert {
        "group.info.set_title",
        "group.info.set_description",
        "group.info.set_photo",
        "group.permissions.set",
        "group.invite.create",
        "group.invite.edit",
        "group.invite.revoke",
        "group.topic.create",
        "group.topic.edit",
        "group.topic.close",
        "group.topic.reopen",
        "group.topic.hide",
        "group.topic.unhide",
        "group.delete",
        "group.migrate",
    } == set(ADMIN)
