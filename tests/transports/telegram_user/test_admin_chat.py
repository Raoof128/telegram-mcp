"""comms v0.3 Task C19: MTProto chat info, invites, topics, lifecycle, admin log (A27; O7)."""

import asyncio
import threading
from datetime import UTC, datetime

import pytest
from telethon import errors
from telethon.tl import types

from comms.core.providers.capability import Capability as C
from comms.core.providers.protocols import ProviderTarget, SemanticOperation
from comms.core.providers.semantics import SEMANTICS
from comms.transports.telegram.telegram.telethon_adapter import TelegramConfig, TelethonSession
from comms.transports.telegram.user.admin import UserAdmin
from tests.telegram.fake_client import FakeClient

NOW = datetime(2026, 9, 25, tzinfo=UTC)
SUPER = ProviderTarget("telegram", "telegram_user", "dst_s", "-1000000000077")
BASIC = ProviderTarget("telegram", "telegram_user", "dst_b", "-55")
LINK = "https://t.me/+AbCdEf"
OK = types.Updates(updates=[], users=[], chats=[], date=None, seq=0)


def _channel(cid=77, **kw):
    return types.Channel(
        id=cid,
        title="t",
        photo=types.ChatPhotoEmpty(),
        date=None,
        access_hash=5,
        megagroup=True,
        **kw,
    )


def _run(tmp_path, script, act):
    sent = []

    def recorder(answer):
        def respond(request):
            sent.append(request)
            if isinstance(answer, BaseException):
                raise answer
            return answer

        return respond

    fake = FakeClient({name: recorder(answer) for name, answer in script.items()})
    fake.session.remember(_channel())
    fake.session.remember(types.User(id=42, access_hash=9, first_name="Jack"))

    loop = asyncio.new_event_loop()
    thread = threading.Thread(target=loop.run_forever, daemon=True)
    thread.start()

    def run(coro):
        return asyncio.run_coroutine_threadsafe(coro, loop).result(10)

    session = TelethonSession(
        TelegramConfig(api_id=1, session_dir=tmp_path / "s"),
        api_hash="0" * 32,
        client_factory=lambda *a, **k: fake,
    )
    run(session.start())
    try:
        result = act(UserAdmin(session, run=run, clock=lambda: NOW))
    finally:
        run(session.stop())
        loop.call_soon_threadsafe(loop.stop)
        thread.join(5)
        loop.close()
    return result, sent, [c for c in fake.calls if not c.startswith("updates.")]


def _invoke(tmp_path, script, cap, args, target=SUPER):
    return _run(
        tmp_path, script, lambda admin: admin.invoke(SemanticOperation(cap, args), target, "op")
    )


@pytest.mark.parametrize(
    "cap,args,target,rpc,check",
    [
        (
            C.CHAT_SET_TITLE,
            {"title": "New"},
            SUPER,
            "channels.EditTitleRequest",
            lambda r: r.title == "New",
        ),
        (
            C.CHAT_SET_TITLE,
            {"title": "New"},
            BASIC,
            "messages.EditChatTitleRequest",
            lambda r: (r.chat_id, r.title) == (55, "New"),
        ),
        (
            C.CHAT_SET_DESCRIPTION,
            {"description": "d"},
            BASIC,
            "messages.EditChatAboutRequest",
            lambda r: r.about == "d",
        ),
        (
            C.CHAT_SET_PERMISSIONS,
            {"permissions": {"can_send_messages": False}},
            SUPER,
            "messages.EditChatDefaultBannedRightsRequest",
            lambda r: r.banned_rights.send_messages is True and not r.banned_rights.view_messages,
        ),
        (
            C.INVITE_EDIT,
            {"invite_link": LINK, "member_limit": 5},
            SUPER,
            "messages.EditExportedChatInviteRequest",
            lambda r: (r.link, r.usage_limit, r.revoked) == (LINK, 5, None),
        ),
        (
            C.INVITE_REVOKE,
            {"invite_link": LINK},
            SUPER,
            "messages.EditExportedChatInviteRequest",
            lambda r: r.revoked is True,
        ),
        (
            C.JOIN_REQUEST_APPROVE,
            {"user_id": 42},
            SUPER,
            "messages.HideChatJoinRequestRequest",
            lambda r: r.approved is True,
        ),
        (
            C.JOIN_REQUEST_REJECT,
            {"user_id": 42},
            SUPER,
            "messages.HideChatJoinRequestRequest",
            lambda r: not r.approved,
        ),
        (
            C.TOPIC_EDIT,
            {"message_thread_id": 9, "name": "N"},
            SUPER,
            "messages.EditForumTopicRequest",
            lambda r: (r.topic_id, r.title) == (9, "N"),
        ),
        (
            C.TOPIC_CLOSE,
            {"message_thread_id": 9},
            SUPER,
            "messages.EditForumTopicRequest",
            lambda r: r.closed is True,
        ),
        (
            C.TOPIC_REOPEN,
            {"message_thread_id": 9},
            SUPER,
            "messages.EditForumTopicRequest",
            lambda r: r.closed is False,
        ),
    ],
)
def test_each_set_state_operation_is_one_rpc(tmp_path, cap, args, target, rpc, check):
    result, sent, calls = _invoke(tmp_path, {rpc: OK}, cap, args, target)
    assert result.outcome == "SUCCEEDED" and calls == [rpc] and check(sent[0])
    assert SEMANTICS[(cap, "telegram_user")].retry_class == "SET_STATE"


def test_invite_create_returns_the_link_as_ref(tmp_path):
    invite = types.ChatInviteExported(
        link=LINK, admin_id=1, date=None, request_needed=True, title="spring"
    )
    result, sent, _ = _invoke(
        tmp_path,
        {"messages.ExportChatInviteRequest": invite},
        C.INVITE_CREATE,
        {"name": "spring", "creates_join_request": True},
    )
    assert (result.outcome, result.provider_ref) == ("SUCCEEDED", LINK) and LINK not in repr(result)
    assert (sent[0].title, sent[0].request_needed) == ("spring", True)
    result, _sent, _ = _invoke(
        tmp_path, {"messages.ExportChatInviteRequest": OK}, C.INVITE_CREATE, {}
    )
    assert (result.outcome, result.provider_ref) == (
        "OUTCOME_UNKNOWN",
        None,
    )  # created, but unnamed


def test_topic_create_returns_the_topic_id(tmp_path):
    created = types.Updates(
        updates=[
            types.UpdateNewChannelMessage(
                types.MessageService(
                    id=31,
                    peer_id=types.PeerChannel(77),
                    date=None,
                    action=types.MessageActionTopicCreate("T", 7322096),
                ),
                pts=1,
                pts_count=1,
            )
        ],
        users=[],
        chats=[],
        date=None,
        seq=0,
    )
    result, sent, _ = _invoke(
        tmp_path,
        {"messages.CreateForumTopicRequest": created},
        C.TOPIC_CREATE,
        {"name": "T", "icon_color": 7322096},
    )
    assert (result.outcome, result.provider_ref) == ("SUCCEEDED", "31") and sent[0].title == "T"


def test_group_delete_is_create_class_resolve_only(tmp_path):
    semantics = SEMANTICS[(C.GROUP_DELETE, "telegram_user")]
    assert (semantics.retry_class, semantics.ambiguity_policy) == (
        "DESTRUCTIVE_NONIDEMPOTENT",
        "resolve_only",
    )
    result, _sent, calls = _invoke(
        tmp_path, {"channels.DeleteChannelRequest": OK}, C.GROUP_DELETE, {}
    )
    assert result.outcome == "SUCCEEDED" and calls == ["channels.DeleteChannelRequest"]
    result, _sent, calls = _invoke(
        tmp_path, {"messages.DeleteChatRequest": True}, C.GROUP_DELETE, {}, BASIC
    )
    assert result.outcome == "SUCCEEDED" and calls == ["messages.DeleteChatRequest"]
    result, _sent, calls = _invoke(
        tmp_path, {"channels.DeleteChannelRequest": ConnectionError("x")}, C.GROUP_DELETE, {}
    )
    assert (result.outcome, len(calls)) == ("OUTCOME_UNKNOWN", 1)  # never re-sent


def test_group_migrate_is_basic_only_and_names_the_new_supergroup(tmp_path):
    migrated = types.Updates(updates=[], users=[], chats=[_channel(88)], date=None, seq=0)
    result, _sent, calls = _invoke(
        tmp_path, {"messages.MigrateChatRequest": migrated}, C.GROUP_MIGRATE, {}, BASIC
    )
    assert (result.outcome, result.provider_ref, calls) == (
        "SUCCEEDED",
        "-1000000000088",
        ["messages.MigrateChatRequest"],
    )
    result, _sent, calls = _invoke(tmp_path, {}, C.GROUP_MIGRATE, {})
    assert (result.outcome, result.code, calls) == ("FAILED", "UNAVAILABLE", [])


def test_group_create_needs_no_destination(tmp_path):
    created = types.Updates(
        updates=[], users=[], chats=[_channel(99, forum=True)], date=None, seq=0
    )
    result, sent, _ = _run(
        tmp_path,
        {"channels.CreateChannelRequest": created},
        lambda admin: admin.create_group({"title": "Spring", "kind": "supergroup", "forum": True}),
    )
    assert (result.outcome, result.provider_ref) == ("SUCCEEDED", "-1000000000099")
    assert (sent[0].megagroup, sent[0].broadcast, sent[0].forum, sent[0].about) == (
        True,
        None,
        True,
        "",
    )
    semantics = SEMANTICS[(C.GROUP_CREATE, "telegram_user")]
    assert (semantics.retry_class, semantics.ambiguity_policy) == ("CREATE", "resolve_only")
    for bad in (
        {"title": ""},
        {"title": "x", "kind": "gigagroup"},
        {"title": "x", "kind": "broadcast", "forum": True},
    ):
        with pytest.raises(ValueError):
            _run(tmp_path, {}, lambda admin, bad=bad: admin.create_group(bad))


def _log_event(event_id):
    return types.ChannelAdminLogEvent(
        id=event_id,
        date=NOW,
        user_id=42,
        action=types.ChannelAdminLogEventActionChangeTitle("a", "b"),
    )


def test_admin_log_bounded_pages(tmp_path):
    full = types.channels.AdminLogResults(
        events=[_log_event(i) for i in (50, 49)], chats=[], users=[]
    )
    page, sent, calls = _run(
        tmp_path,
        {"channels.GetAdminLogRequest": full},
        lambda admin: admin.read_admin_log(SUPER, limit=2),
    )
    assert calls == ["channels.GetAdminLogRequest"] and (
        sent[0].limit,
        sent[0].max_id,
        sent[0].q,
    ) == (2, 0, "")
    assert page.provenance == "telegram_live" and page.next_cursor == "49"
    assert page.items[0] == {
        "source": "telegram_live",
        "observed_at": "2026-09-25T00:00:00.000000Z",
        "event_id": 50,
        "date": "2026-09-25T00:00:00.000000Z",
        "user_id": 42,
        "action": "ChannelAdminLogEventActionChangeTitle",
    }
    last = types.channels.AdminLogResults(events=[_log_event(48)], chats=[], users=[])
    page, sent, _ = _run(
        tmp_path,
        {"channels.GetAdminLogRequest": last},
        lambda admin: admin.read_admin_log(SUPER, limit=2, cursor="49"),
    )
    assert sent[0].max_id == 49 and page.next_cursor is None
    for bad in ({"limit": 0}, {"limit": 101}, {"cursor": "x"}):
        with pytest.raises(ValueError):
            _run(tmp_path, {}, lambda admin, bad=bad: admin.read_admin_log(SUPER, **bad))
    page, _sent, calls = _run(tmp_path, {}, lambda admin: admin.read_admin_log(BASIC))
    assert page.items == () and calls == []  # basic groups have no admin log


@pytest.mark.parametrize(
    "error,outcome,code",
    [
        (errors.ChatAboutNotModifiedError(request=None), "SUCCEEDED", None),
        (errors.BadRequestError(request=None, message="TOPIC_NOT_MODIFIED"), "SUCCEEDED", None),
        (errors.InviteHashExpiredError(request=None), "FAILED", "TARGET_NOT_FOUND"),
        (errors.HideRequesterMissingError(request=None), "FAILED", "TARGET_NOT_FOUND"),
        (errors.ChatAdminRequiredError(request=None), "FAILED", "NOT_AUTHORIZED"),
        (errors.BadRequestError(request=None, message="SOMETHING_NEW"), "OUTCOME_UNKNOWN", None),
    ],
)
def test_classification_fixtures(tmp_path, error, outcome, code):
    result, _sent, _ = _invoke(
        tmp_path,
        {"messages.EditChatAboutRequest": error},
        C.CHAT_SET_DESCRIPTION,
        {"description": "d"},
    )
    assert (result.outcome, result.code) == (outcome, code)


def test_every_c19_operation_is_offered():
    assert {
        C.CHAT_SET_TITLE,
        C.CHAT_SET_DESCRIPTION,
        C.CHAT_SET_PERMISSIONS,
        C.INVITE_CREATE,
        C.INVITE_EDIT,
        C.INVITE_REVOKE,
        C.JOIN_REQUEST_APPROVE,
        C.JOIN_REQUEST_REJECT,
        C.TOPIC_CREATE,
        C.TOPIC_EDIT,
        C.TOPIC_CLOSE,
        C.TOPIC_REOPEN,
        C.GROUP_DELETE,
        C.GROUP_MIGRATE,
    } <= UserAdmin.operations
