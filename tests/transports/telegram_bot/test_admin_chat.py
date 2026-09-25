"""comms v0.3 Task C10: Bot API admin rights, chat info, pins (P §26, §74; A27)."""

import json

import pytest

from comms.core.providers.capability import Capability as C
from comms.core.providers.protocols import ProviderTarget, SemanticOperation
from comms.core.providers.semantics import SEMANTICS
from comms.transports.telegram.bot.admin import BotAdmin
from comms.transports.telegram.bot.admin_chat import ADMIN_RIGHTS, CHAT_REQUESTS, PROFILES
from comms.transports.telegram.bot.http import BOT_METHODS, BotApi
from tests.transports.telegram_bot.helpers import Secrets, routed

GROUP = ProviderTarget("telegram", "telegram_bot", "dst_" + "g" * 26, "-1001234567890")
CHAT = -1001234567890
METHODS = (
    "promoteChatMember",
    "setChatTitle",
    "setChatDescription",
    "setChatPermissions",
    "pinChatMessage",
    "unpinChatMessage",
)


def _call(cap, args, answer="admin_true"):
    seen = []
    admin = BotAdmin(
        BotApi(Secrets(), version=1, transport=routed(dict.fromkeys(METHODS, answer), seen))
    )
    result = admin.invoke(SemanticOperation(cap, args), GROUP, "k")
    return result, seen


def _sent(seen):
    (request,) = seen
    return request.url.path.rsplit("/", 1)[1], json.loads(request.content)


def test_profiles_are_exact_and_complete():
    assert set(PROFILES) == {"moderator", "event_admin", "full_admin"}
    for rights in PROFILES.values():
        assert set(rights) == ADMIN_RIGHTS and all(type(v) is bool for v in rights.values())
    assert PROFILES["full_admin"]["is_anonymous"] is False
    assert PROFILES["moderator"]["can_promote_members"] is False


@pytest.mark.parametrize("profile", ["moderator", "event_admin", "full_admin"])
def test_promote_sends_every_right_of_the_profile(profile):
    result, seen = _call(C.ADMIN_PROMOTE, {"user_id": 42, "profile": profile})
    assert result.outcome == "SUCCEEDED"
    assert _sent(seen) == (
        "promoteChatMember",
        {"chat_id": CHAT, "user_id": 42, **PROFILES[profile]},
    )


def test_custom_rights_are_exact_with_the_rest_denied():
    rights = {"can_delete_messages": True, "can_invite_users": True}
    _result, seen = _call(C.ADMIN_PROMOTE, {"user_id": 42, "profile": "custom", "rights": rights})
    _method, params = _sent(seen)
    assert {k for k in ADMIN_RIGHTS if params[k]} == set(rights) and set(params) - {
        "chat_id",
        "user_id",
    } == ADMIN_RIGHTS


@pytest.mark.parametrize(
    "args",
    [
        {"user_id": 42},  # "make admin": no profile
        {"user_id": 42, "profile": "admin"},
        {"user_id": 42, "profile": ["moderator"]},
        {"user_id": 42, "profile": "custom"},
        {"user_id": 42, "profile": "custom", "rights": {}},
        {
            "user_id": 42,
            "profile": "custom",
            "rights": {"can_delete_messages": False},
        },  # grants nothing
        {"user_id": 42, "profile": "custom", "rights": {"can_everything": True}},
        {"user_id": 42, "profile": "moderator", "rights": {"can_delete_messages": True}},
        {"user_id": 42, "profile": "custom", "rights": {"can_delete_messages": 1}},
    ],
)
def test_promote_requires_explicit_rights_or_profile(args):
    with pytest.raises(ValueError):
        _call(C.ADMIN_PROMOTE, args)


def test_demote_sets_every_right_false():
    _result, seen = _call(C.ADMIN_DEMOTE, {"user_id": 42})
    assert _sent(seen) == (
        "promoteChatMember",
        {"chat_id": CHAT, "user_id": 42, **dict.fromkeys(ADMIN_RIGHTS, False)},
    )


@pytest.mark.parametrize(
    "cap,args,expected",
    [
        (
            C.CHAT_SET_TITLE,
            {"title": "New title"},
            ("setChatTitle", {"chat_id": CHAT, "title": "New title"}),
        ),
        (
            C.CHAT_SET_DESCRIPTION,
            {"description": ""},
            ("setChatDescription", {"chat_id": CHAT, "description": ""}),
        ),
        (
            C.CHAT_SET_PERMISSIONS,
            {"permissions": {"can_send_messages": True, "can_send_polls": False}},
            (
                "setChatPermissions",
                {
                    "chat_id": CHAT,
                    "permissions": {"can_send_messages": True, "can_send_polls": False},
                },
            ),
        ),
        (
            C.MESSAGE_PIN,
            {"message_id": 7, "pinned": True, "disable_notification": True},
            ("pinChatMessage", {"chat_id": CHAT, "message_id": 7, "disable_notification": True}),
        ),
        (
            C.MESSAGE_PIN,
            {"message_id": 7, "pinned": False},
            ("unpinChatMessage", {"chat_id": CHAT, "message_id": 7}),
        ),
    ],
)
def test_each_chat_operation_is_one_call(cap, args, expected):
    result, seen = _call(cap, args)
    assert result.outcome == "SUCCEEDED" and _sent(seen) == expected


@pytest.mark.parametrize(
    "cap,args",
    [
        (C.CHAT_SET_TITLE, {"title": ""}),
        (C.CHAT_SET_TITLE, {"title": "x" * 129}),
        (C.CHAT_SET_DESCRIPTION, {"description": "x" * 256}),
        (C.CHAT_SET_PERMISSIONS, {"permissions": {}}),
        (C.MESSAGE_PIN, {"message_id": 7}),
        (C.MESSAGE_PIN, {"message_id": 7, "pinned": False, "disable_notification": True}),
    ],
)
def test_malformed_chat_arguments_are_refused(cap, args):
    with pytest.raises(ValueError):
        _call(cap, args)


def test_set_state_operations_idempotent_by_semantics():
    assert set(CHAT_REQUESTS) == {
        C.ADMIN_PROMOTE,
        C.ADMIN_DEMOTE,
        C.CHAT_SET_TITLE,
        C.CHAT_SET_DESCRIPTION,
        C.CHAT_SET_PERMISSIONS,
        C.MESSAGE_PIN,
    }
    for cap in CHAT_REQUESTS:
        semantics = SEMANTICS[(cap, "telegram_bot")]
        assert (
            semantics.retry_class,
            semantics.idempotency_strategy,
            semantics.ambiguity_policy,
        ) == (
            "SET_STATE",
            "natural",
            "retry_same_key",
        )
    assert set(CHAT_REQUESTS) <= BotAdmin.operations and set(METHODS) <= BOT_METHODS


@pytest.mark.parametrize(
    "answer,outcome,code",
    [
        ("admin_400_not_enough_rights_promote", "FAILED", "NOT_AUTHORIZED"),
        ("admin_400_not_modified", "SUCCEEDED", None),  # the exact state already holds
        ("sendMessage_429", "FAILED", "RATE_LIMITED"),
        ("sendMessage_400_undocumented", "OUTCOME_UNKNOWN", None),
    ],
)
def test_chat_operations_classify_named_cases(answer, outcome, code):
    result, _seen = _call(C.CHAT_SET_DESCRIPTION, {"description": "d"}, answer)
    assert (result.outcome, result.code) == (outcome, code)
