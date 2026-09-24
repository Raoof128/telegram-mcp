"""comms v0.3 Task C18: MTProto membership and admin rights (P §25, §26, §74; A27, A41)."""

import asyncio

import pytest
from telethon import errors
from telethon.tl import types

from comms.core.providers.capability import Capability as C
from comms.core.providers.protocols import ProviderTarget, SemanticOperation
from comms.core.providers.semantics import SEMANTICS
from comms.transports.telegram.admin_profiles import MTPROTO_RIGHT, PROFILES
from comms.transports.telegram.telegram.telethon_adapter import TelegramConfig, TelethonSession
from comms.transports.telegram.user.admin import UserAdmin
from tests.conformance.registry import REGISTRY
from tests.conformance.runner import run_suite
from tests.telegram.fake_client import FakeClient

SUPER = ProviderTarget("telegram", "telegram_user", "dst_s", "-1000000000077")
BASIC = ProviderTarget("telegram", "telegram_user", "dst_b", "-55")
OK = types.Updates(updates=[], users=[], chats=[], date=None, seq=0)


def _world(script):
    sent = []

    def recorder(answer):
        def respond(request):
            sent.append(request)
            if isinstance(answer, BaseException):
                raise answer
            return answer

        return respond

    fake = FakeClient({name: recorder(answer) for name, answer in script.items()})
    fake.session.remember(
        types.Channel(
            id=77, title="t", photo=types.ChatPhotoEmpty(), date=None, access_hash=5, megagroup=True
        )
    )
    fake.session.remember(types.User(id=42, access_hash=9, first_name="Jack"))
    return fake, sent


def _invoke(tmp_path, script, cap, args, target=SUPER):
    fake, sent = _world(script)

    async def go():
        session = TelethonSession(
            TelegramConfig(api_id=1, session_dir=tmp_path / "s"),
            api_hash="0" * 32,
            client_factory=lambda *a, **k: fake,
        )
        await session.start()
        try:
            admin = UserAdmin(session, run=lambda coro: coro)
            return await admin.invoke(SemanticOperation(cap, args), target, "op")
        finally:
            await session.stop()

    return asyncio.run(go()), sent, [c for c in fake.calls if not c.startswith("updates.")]


def test_member_add_never_silently_becomes_invite(tmp_path):
    result, _sent, calls = _invoke(
        tmp_path,
        {"channels.InviteToChannelRequest": errors.UserPrivacyRestrictedError(request=None)},
        C.MEMBER_ADD,
        {"user_id": 42},
    )
    assert (result.outcome, result.code) == ("FAILED", "INVITE_REQUIRED")
    assert calls == ["channels.InviteToChannelRequest"]  # no invite link was made in its place
    missing = types.messages.InvitedUsers(
        updates=OK, missing_invitees=[types.MissingInvitee(user_id=42)]
    )
    result, _sent, calls = _invoke(
        tmp_path, {"channels.InviteToChannelRequest": missing}, C.MEMBER_ADD, {"user_id": 42}
    )
    assert (result.outcome, result.code) == ("FAILED", "INVITE_REQUIRED") and calls == [
        "channels.InviteToChannelRequest"
    ]


def test_member_add_direct_and_in_a_basic_group(tmp_path):
    added = types.messages.InvitedUsers(updates=OK, missing_invitees=[])
    result, sent, _ = _invoke(
        tmp_path, {"channels.InviteToChannelRequest": added}, C.MEMBER_ADD, {"user_id": 42}
    )
    assert result.outcome == "SUCCEEDED" and sent[0].users[0].user_id == 42
    result, sent, _ = _invoke(
        tmp_path, {"messages.AddChatUserRequest": added}, C.MEMBER_ADD, {"user_id": 42}, BASIC
    )
    assert result.outcome == "SUCCEEDED" and (sent[0].chat_id, sent[0].user_id.user_id) == (55, 42)


def test_ban_unban_restrict_in_a_supergroup(tmp_path):
    result, sent, _ = _invoke(
        tmp_path, {"channels.EditBannedRequest": OK}, C.MEMBER_BAN, {"user_id": 42}
    )
    assert result.outcome == "SUCCEEDED" and sent[0].banned_rights.view_messages is True
    result, sent, _ = _invoke(
        tmp_path,
        {"channels.EditBannedRequest": OK},
        C.MEMBER_UNBAN,
        {"user_id": 42, "only_if_banned": True},
    )
    rights = sent[0].banned_rights.to_dict()
    assert result.outcome == "SUCCEEDED" and not any(v is True for v in rights.values())
    result, sent, _ = _invoke(
        tmp_path,
        {"channels.EditBannedRequest": OK},
        C.MEMBER_RESTRICT,
        {"user_id": 42, "permissions": {"can_send_messages": False, "can_send_polls": True}},
    )
    rights = sent[0].banned_rights
    assert (
        result.outcome == "SUCCEEDED"
        and rights.send_messages is True
        and not rights.send_polls
        and not rights.view_messages
    )


def test_basic_group_membership_has_its_own_rpcs(tmp_path):
    result, _sent, calls = _invoke(
        tmp_path, {"messages.DeleteChatUserRequest": OK}, C.MEMBER_BAN, {"user_id": 42}, BASIC
    )
    assert result.outcome == "SUCCEEDED" and calls == ["messages.DeleteChatUserRequest"]
    result, _sent, calls = _invoke(tmp_path, {}, C.MEMBER_UNBAN, {"user_id": 42}, BASIC)
    assert (
        result.outcome == "SUCCEEDED" and result.detail == {"no_op": True} and calls == []
    )  # no ban list
    result, _sent, calls = _invoke(
        tmp_path,
        {},
        C.MEMBER_RESTRICT,
        {"user_id": 42, "permissions": {"can_send_messages": False}},
        BASIC,
    )
    assert (result.outcome, result.code, calls) == ("FAILED", "UNAVAILABLE", [])


@pytest.mark.parametrize("profile", ["moderator", "event_admin", "full_admin"])
def test_exact_rights_set_state_idempotent(tmp_path, profile):
    result, sent, _ = _invoke(
        tmp_path,
        {"channels.EditAdminRequest": OK},
        C.ADMIN_PROMOTE,
        {"user_id": 42, "profile": profile},
    )
    held = {k for k, v in sent[0].admin_rights.to_dict().items() if v is True}
    assert held == {MTPROTO_RIGHT[r] for r, granted in PROFILES[profile].items() if granted}
    assert (
        result.outcome == "SUCCEEDED"
        and SEMANTICS[(C.ADMIN_PROMOTE, "telegram_user")].retry_class == "SET_STATE"
    )
    again, _sent, _ = _invoke(
        tmp_path,
        {"channels.EditAdminRequest": errors.ChatNotModifiedError(request=None)},
        C.ADMIN_PROMOTE,
        {"user_id": 42, "profile": profile},
    )
    assert (again.outcome, again.detail) == ("SUCCEEDED", {"already_set": True})


def test_demote_and_basic_group_admin(tmp_path):
    _result, sent, _ = _invoke(
        tmp_path, {"channels.EditAdminRequest": OK}, C.ADMIN_DEMOTE, {"user_id": 42}
    )
    assert not any(v is True for v in sent[0].admin_rights.to_dict().values())
    result, sent, _ = _invoke(
        tmp_path,
        {"messages.EditChatAdminRequest": True},
        C.ADMIN_PROMOTE,
        {"user_id": 42, "profile": "full_admin"},
        BASIC,
    )
    assert result.outcome == "SUCCEEDED" and sent[0].is_admin is True
    result, _sent, calls = _invoke(
        tmp_path, {}, C.ADMIN_PROMOTE, {"user_id": 42, "profile": "moderator"}, BASIC
    )
    assert (result.outcome, result.code, calls) == ("FAILED", "RIGHTS_NOT_EXPRESSIBLE", [])


@pytest.mark.parametrize(
    "error,outcome,code",
    [
        (errors.ChatAdminRequiredError(request=None), "FAILED", "NOT_AUTHORIZED"),
        (errors.RightForbiddenError(request=None), "FAILED", "NOT_AUTHORIZED"),
        (errors.UserAdminInvalidError(request=None), "FAILED", "NOT_AUTHORIZED"),
        (errors.UserNotParticipantError(request=None), "FAILED", "TARGET_NOT_FOUND"),
        (errors.UserIdInvalidError(request=None), "FAILED", "TARGET_NOT_FOUND"),
        (errors.FloodWaitError(request=None, capture=8), "FAILED", "RATE_LIMITED"),
        (errors.ServerError(request=None, message="x", code=500), "OUTCOME_UNKNOWN", None),
        (ConnectionError("dropped"), "OUTCOME_UNKNOWN", None),
        (errors.RPCError(request=None, message="WEIRD", code=400), "OUTCOME_UNKNOWN", None),
    ],
)
def test_classification_per_rpc_error(tmp_path, error, outcome, code):
    result, _sent, calls = _invoke(
        tmp_path, {"channels.EditBannedRequest": error}, C.MEMBER_BAN, {"user_id": 42}
    )
    assert (result.outcome, result.code, calls) == (outcome, code, ["channels.EditBannedRequest"])
    if code == "RATE_LIMITED":
        assert result.detail == {"retry_after": 8}


def test_unknown_peers_fail_without_a_call(tmp_path):
    result, _sent, calls = _invoke(tmp_path, {}, C.MEMBER_BAN, {"user_id": 43})
    assert (result.outcome, result.code, calls) == ("FAILED", "TARGET_NOT_FOUND", [])
    other = ProviderTarget("telegram", "telegram_user", "dst_o", "-1000000000078")
    result, _sent, calls = _invoke(tmp_path, {}, C.MEMBER_BAN, {"user_id": 42}, other)
    assert (result.outcome, result.code, calls) == ("FAILED", "DESTINATION_NOT_FOUND", [])


def test_member_remove_is_never_one_call(tmp_path):
    assert C.MEMBER_REMOVE not in UserAdmin.operations
    assert SEMANTICS[(C.MEMBER_REMOVE, "telegram_user")].steps == (C.MEMBER_BAN, C.MEMBER_UNBAN)
    with pytest.raises(ValueError):
        _invoke(tmp_path, {}, C.MEMBER_REMOVE, {"user_id": 42})


@pytest.mark.parametrize(
    "cap,args",
    [
        (C.MEMBER_BAN, {"user_id": 42, "revoke_messages": True}),
        (C.MEMBER_ADD, {}),
        (C.ADMIN_PROMOTE, {"user_id": 42}),
        (C.MEMBER_RESTRICT, {"user_id": 42, "permissions": {"can_fly": True}}),
    ],
)
def test_malformed_arguments_are_refused(tmp_path, cap, args):
    with pytest.raises(ValueError):
        _invoke(tmp_path, {}, cap, args)


def test_a_private_chat_is_not_a_group(tmp_path):
    with pytest.raises(ValueError):
        _invoke(
            tmp_path,
            {},
            C.MEMBER_BAN,
            {"user_id": 42},
            ProviderTarget("telegram", "telegram_user", "dst_p", "42"),
        )


def test_the_conformance_admin_contract_passes():
    report = run_suite(
        REGISTRY.subset("telegram_user", "admin"), {"telegram_user": frozenset({"admin"})}
    )
    assert report.ok and report.passed == 2, report.failures
