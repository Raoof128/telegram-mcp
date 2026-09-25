"""comms v0.3 Task D14: message writes in each admin adapter (P §23, §72, §77)."""

import asyncio
import hashlib
import json

import pytest
from telethon.tl import types

from comms.core.providers.capability import Capability as C
from comms.core.providers.protocols import ProviderTarget, SemanticOperation
from comms.transports.telegram.bot.admin import BotAdmin
from comms.transports.telegram.bot.http import BotApi
from comms.transports.telegram.telegram.telethon_adapter import TelegramConfig, TelethonSession
from comms.transports.telegram.user.admin import UserAdmin
from comms.transports.telegram.user.send import random_id_for
from comms.transports.whatsapp.cloud.groups import GroupDiscovery, WhatsAppAdmin
from comms.transports.whatsapp.cloud.http import GraphApi
from tests.conformance.meta_oracle import oracle, oracle_transport
from tests.transports.telegram_bot.helpers import Secrets as BotSecrets
from tests.transports.telegram_bot.helpers import routed
from tests.transports.telegram_user.test_admin_members import BASIC, SUPER, _world
from tests.transports.whatsapp_cloud.helpers import Secrets as MetaSecrets

BOT_GROUP = ProviderTarget("telegram", "telegram_bot", "dst_g", "-1001234567890")
OP_KEY = hashlib.sha256(b"op").hexdigest()


def _bot(answer, seen):
    methods = ("sendMessage", "editMessageText", "deleteMessage")
    return BotAdmin(
        BotApi(BotSecrets(), version=1, transport=routed(dict.fromkeys(methods, answer), seen))
    )


def test_bot_send_returns_the_message_id_and_replies_by_parameters():
    seen = []
    op = SemanticOperation(C.MESSAGE_SEND, {"text": "Salaam", "reply_to_message_id": 5})
    result = _bot("sendMessage_ok", seen).invoke(op, BOT_GROUP, OP_KEY)
    assert result.outcome == "SUCCEEDED" and result.provider_ref
    body = json.loads(seen[0].content)
    assert body == {
        "chat_id": -1001234567890,
        "text": "Salaam",
        "reply_parameters": {"message_id": 5},
    }


def test_bot_edit_and_delete_one_call_each_and_delete_reports_everyone():
    seen = []
    admin = _bot("admin_true", seen)
    edit = admin.invoke(
        SemanticOperation(C.MESSAGE_EDIT, {"message_id": 9, "text": "x"}), BOT_GROUP, OP_KEY
    )
    delete = admin.invoke(SemanticOperation(C.MESSAGE_DELETE, {"message_id": 9}), BOT_GROUP, OP_KEY)
    assert edit.outcome == delete.outcome == "SUCCEEDED" and delete.detail == {"scope": "everyone"}
    assert [r.url.path.rsplit("/", 1)[1] for r in seen] == ["editMessageText", "deleteMessage"]


def test_bot_cannot_delete_only_for_itself():
    seen = []
    op = SemanticOperation(C.MESSAGE_DELETE, {"message_id": 9, "revoke": False})
    with pytest.raises(NotImplementedError):
        _bot("admin_true", seen).validate(op, BOT_GROUP)
    assert seen == []


def _user(tmp_path, script, op, target=SUPER):
    fake, sent = _world(script)

    async def go():
        session = TelethonSession(
            TelegramConfig(api_id=1, session_dir=tmp_path / "s"),
            api_hash="0" * 32,
            client_factory=lambda *a, **k: fake,
        )
        await session.start()
        loop = asyncio.get_running_loop()
        try:  # as in the daemon: invoke off the loop, the RPC on the session's loop
            admin = UserAdmin(
                session,
                run=lambda coro: asyncio.run_coroutine_threadsafe(coro, loop).result(),
                clock=lambda: None,
            )
            return await loop.run_in_executor(None, lambda: admin.invoke(op, target, OP_KEY))
        finally:
            await session.stop()

    return asyncio.run(go()), sent


def test_user_send_is_keyed_by_the_operation_and_replies(tmp_path):
    sent_ok = types.UpdateShortSentMessage(out=True, id=321, pts=1, pts_count=1, date=None)
    op = SemanticOperation(C.MESSAGE_SEND, {"text": "Salaam", "reply_to_message_id": 5})
    result, sent = _user(tmp_path, {"messages.SendMessageRequest": sent_ok}, op)
    assert (result.outcome, result.provider_ref) == ("SUCCEEDED", "321")
    (request,) = sent
    assert request.random_id == random_id_for(OP_KEY) and request.reply_to.reply_to_msg_id == 5


@pytest.mark.parametrize(
    ("target", "revoke", "rpc", "scope"),
    [
        (SUPER, False, "channels.DeleteMessagesRequest", "everyone"),
        (BASIC, True, "messages.DeleteMessagesRequest", "everyone"),
        (BASIC, False, "messages.DeleteMessagesRequest", "local"),
    ],
)
def test_user_delete_reports_the_scope_it_performed(tmp_path, target, revoke, rpc, scope):
    affected = types.messages.AffectedMessages(pts=1, pts_count=1)
    op = SemanticOperation(C.MESSAGE_DELETE, {"message_id": 9, "revoke": revoke})
    result, sent = _user(tmp_path, {rpc: affected}, op, target)
    assert result.outcome == "SUCCEEDED" and result.detail == {"scope": scope}
    assert [type(r).__name__ for r in sent] == [rpc.split(".")[1]]


def test_user_pin_and_unpin(tmp_path):
    ok = types.Updates(updates=[], users=[], chats=[], date=None, seq=0)
    for pinned in (True, False):
        op = SemanticOperation(C.MESSAGE_PIN, {"message_id": 9, "pinned": pinned})
        result, sent = _user(tmp_path, {"messages.UpdatePinnedMessageRequest": ok}, op)
        assert result.outcome == "SUCCEEDED" and bool(sent[0].unpin) is (not pinned)


def test_whatsapp_mark_read_is_one_call_to_the_contact():
    graph = oracle()
    api = GraphApi(
        MetaSecrets(),
        version=1,
        phone_number_id=graph.phone_number_id,
        waba_id=graph.waba_id,
        transport=oracle_transport(graph),
    )
    admin = WhatsAppAdmin(api, GroupDiscovery(api))
    contact = ProviderTarget("whatsapp", "whatsapp_cloud", "cpt_x", "+61400000001")
    op = SemanticOperation(C.MESSAGE_MARK_READ, {"message_id": "wamid.ABC123"})
    assert admin.invoke(op, contact, OP_KEY).outcome == "SUCCEEDED"
    assert graph.read == ["wamid.ABC123"] and graph.sent == []
    with pytest.raises(ValueError):  # a group is not a contact
        admin.validate(op, ProviderTarget("whatsapp", "whatsapp_cloud", "d", "group:1203630"))
