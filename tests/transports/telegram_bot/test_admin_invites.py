"""comms v0.3 Task C11: Bot API invites, join requests, forum topics (A27; P §27, §28)."""

import json
from datetime import UTC, datetime

import httpx
import pytest

from comms.core.providers.capability import Capability as C
from comms.core.providers.capability import CapabilityState as S
from comms.core.providers.protocols import ProviderTarget, SemanticOperation
from comms.core.providers.semantics import SEMANTICS
from comms.transports.telegram.bot.admin import BotAdmin
from comms.transports.telegram.bot.admin_invites import INVITE_REQUESTS
from comms.transports.telegram.bot.capability import BotCapability
from comms.transports.telegram.bot.http import BOT_METHODS, BotApi
from tests.transports.telegram_bot.helpers import Secrets, routed

GROUP = ProviderTarget("telegram", "telegram_bot", "dst_" + "g" * 26, "-1001234567890")
CHAT = -1001234567890
LINK = "https://t.me/+FixtureInviteAAAA"
NOW = datetime(2026, 9, 25, tzinfo=UTC)
METHODS = (
    "createChatInviteLink",
    "editChatInviteLink",
    "revokeChatInviteLink",
    "approveChatJoinRequest",
    "declineChatJoinRequest",
    "createForumTopic",
    "editForumTopic",
    "closeForumTopic",
    "reopenForumTopic",
)


def _call(cap, args, answer="admin_true"):
    seen = []
    routes = dict.fromkeys(METHODS, answer) if not isinstance(answer, dict) else answer
    admin = BotAdmin(BotApi(Secrets(), version=1, transport=routed(routes, seen)))
    return admin.invoke(SemanticOperation(cap, args), GROUP, "k"), seen


def _sent(seen):
    (request,) = seen
    return request.url.path.rsplit("/", 1)[1], json.loads(request.content)


def test_invite_create_returns_the_link_as_the_opaque_ref():
    result, seen = _call(
        C.INVITE_CREATE, {"name": "spring", "member_limit": 50}, "createChatInviteLink_ok"
    )
    assert (result.outcome, result.provider_ref) == ("SUCCEEDED", LINK)
    assert LINK not in repr(result)
    assert _sent(seen) == (
        "createChatInviteLink",
        {"chat_id": CHAT, "name": "spring", "member_limit": 50},
    )


def test_invite_create_is_create_class_resolve_only_on_ambiguity():
    semantics = SEMANTICS[(C.INVITE_CREATE, "telegram_bot")]
    assert (semantics.retry_class, semantics.ambiguity_policy) == ("CREATE", "resolve_only")
    for answer in (httpx.ReadTimeout, "create_ok_without_ref", "sendMessage_500_envelope"):
        result, seen = _call(C.INVITE_CREATE, {}, answer)
        assert (result.outcome, result.provider_ref, len(seen)) == ("OUTCOME_UNKNOWN", None, 1), (
            answer
        )


def test_topic_create_returns_the_thread_id():
    result, seen = _call(
        C.TOPIC_CREATE, {"name": "Announcements", "icon_color": 7322096}, "createForumTopic_ok"
    )
    assert (result.outcome, result.provider_ref) == ("SUCCEEDED", "77")
    assert _sent(seen) == (
        "createForumTopic",
        {"chat_id": CHAT, "name": "Announcements", "icon_color": 7322096},
    )


@pytest.mark.parametrize(
    "cap,args,expected",
    [
        (
            C.INVITE_EDIT,
            {"invite_link": LINK, "member_limit": 10},
            ("editChatInviteLink", {"chat_id": CHAT, "invite_link": LINK, "member_limit": 10}),
        ),
        (
            C.INVITE_REVOKE,
            {"invite_link": LINK},
            ("revokeChatInviteLink", {"chat_id": CHAT, "invite_link": LINK}),
        ),
        (
            C.JOIN_REQUEST_APPROVE,
            {"user_id": 42},
            ("approveChatJoinRequest", {"chat_id": CHAT, "user_id": 42}),
        ),
        (
            C.JOIN_REQUEST_REJECT,
            {"user_id": 42},
            ("declineChatJoinRequest", {"chat_id": CHAT, "user_id": 42}),
        ),
        (
            C.TOPIC_EDIT,
            {"message_thread_id": 77, "name": "News"},
            ("editForumTopic", {"chat_id": CHAT, "message_thread_id": 77, "name": "News"}),
        ),
        (
            C.TOPIC_CLOSE,
            {"message_thread_id": 77},
            ("closeForumTopic", {"chat_id": CHAT, "message_thread_id": 77}),
        ),
        (
            C.TOPIC_REOPEN,
            {"message_thread_id": 77},
            ("reopenForumTopic", {"chat_id": CHAT, "message_thread_id": 77}),
        ),
    ],
)
def test_each_set_state_operation_is_one_call(cap, args, expected):
    result, seen = _call(cap, args)
    assert result.outcome == "SUCCEEDED" and _sent(seen) == expected
    assert SEMANTICS[(cap, "telegram_bot")].retry_class == "SET_STATE"


@pytest.mark.parametrize(
    "cap,args",
    [
        (C.INVITE_CREATE, {"name": "x" * 33}),
        (C.INVITE_CREATE, {"member_limit": 0}),
        (C.INVITE_CREATE, {"member_limit": 100000}),
        (
            C.INVITE_CREATE,
            {"member_limit": 5, "creates_join_request": True},
        ),  # Telegram forbids both
        (C.INVITE_EDIT, {"invite_link": "https://evil.example/+x"}),
        (C.INVITE_EDIT, {"invite_link": LINK}),  # nothing to change
        (C.INVITE_REVOKE, {"invite_link": 5}),
        (C.TOPIC_CREATE, {"name": ""}),
        (C.TOPIC_CREATE, {"name": "t", "icon_color": 123}),
        (C.TOPIC_EDIT, {"message_thread_id": 77}),  # nothing to change
        (C.TOPIC_CLOSE, {"message_thread_id": 0}),
    ],
)
def test_malformed_arguments_are_refused(cap, args):
    with pytest.raises(ValueError):
        _call(cap, args)


def test_the_bot_cannot_list_invites():
    assert (
        C.INVITE_LIST not in BotAdmin.operations and C.JOIN_REQUEST_LIST not in BotAdmin.operations
    )
    assert "telegram_bot" not in {a for (c, a) in SEMANTICS if c is C.INVITE_LIST}


def test_topic_ops_only_on_forum_chats():
    routes = {
        "getMe": "getMe_ok",
        "getChat": "getChat_supergroup_muted",
        "getChatMember": "getChatMember_creator",
    }
    provider = BotCapability.from_api(
        BotApi(Secrets(), version=1, transport=routed(routes)), clock=lambda: NOW
    )
    states = provider.snapshot("telegram_bot", GROUP).states
    assert all(
        states[c] is S.UNAVAILABLE
        for c in (C.TOPIC_CREATE, C.TOPIC_EDIT, C.TOPIC_CLOSE, C.TOPIC_REOPEN)
    )
    result, _seen = _call(C.TOPIC_CREATE, {"name": "t"}, "topic_400_not_a_forum")
    assert (result.outcome, result.code) == ("FAILED", "UNAVAILABLE")


@pytest.mark.parametrize(
    "cap,args,answer,outcome,code",
    [
        (C.TOPIC_CLOSE, {"message_thread_id": 77}, "topic_400_not_modified", "SUCCEEDED", None),
        (
            C.JOIN_REQUEST_APPROVE,
            {"user_id": 42},
            "join_400_hide_requester_missing",
            "FAILED",
            "TARGET_NOT_FOUND",
        ),
        (
            C.INVITE_REVOKE,
            {"invite_link": LINK},
            "admin_400_not_enough_rights_promote",
            "FAILED",
            "NOT_AUTHORIZED",
        ),
    ],
)
def test_classification_fixtures(cap, args, answer, outcome, code):
    result, _seen = _call(cap, args, answer)
    assert (result.outcome, result.code) == (outcome, code)


def test_every_request_method_is_in_the_closed_set():
    assert set(METHODS) <= BOT_METHODS
    assert set(INVITE_REQUESTS) <= BotAdmin.operations
