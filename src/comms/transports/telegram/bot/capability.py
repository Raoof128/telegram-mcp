"""Bot API capability discovery from actual rights (comms v0.3 Task C8; P §9–12; A25).

A snapshot reads ``getChat`` and ``getChatMember(me)`` and maps the bot's real status and
rights to a state per P §11 capability. A capability the Bot API has no method for is
``PROVIDER_UNSUPPORTED`` whatever the rights (P §12: the bot never claims history). Missing or
malformed credentials give ``NOT_CONFIGURED``. A lookup that fails gives ``NOT_AUTHORIZED`` for
a documented refusal, ``TEMPORARILY_UNAVAILABLE`` for a documented back-off or a connection
never made, and ``UNKNOWN`` otherwise; none is ever ``AVAILABLE``. Snapshots are advisory: the
provider's answer to a mutation is final (A25). Network: never inside a comms.db transaction.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import datetime
from typing import Any

from comms.core import timeutil
from comms.core.delivery.transport import ResultKind
from comms.core.keys.secrets import SecretStore, SecretStoreError
from comms.core.providers.capability import Capability as C
from comms.core.providers.capability import CapabilityState as S
from comms.core.providers.protocols import CapabilitySnapshot, ProviderTarget
from comms.core.providers.semantics import SUPPORT
from comms.transports.telegram.bot.classify import LookupFailed, lookup
from comms.transports.telegram.bot.http import BotApi, BotRefused
from comms.transports.telegram.capabilities import TELEGRAM_CAPABILITIES

__all__ = ["TELEGRAM_CAPABILITIES", "BotCapability"]

ACTOR = "telegram_bot"
_BOT = frozenset(c for c in TELEGRAM_CAPABILITIES if ACTOR in SUPPORT[c])
_MESSAGES = frozenset({C.MESSAGE_SEND, C.MESSAGE_EDIT, C.MESSAGE_FORWARD})
_PRESENT = frozenset({C.MEMBER_GET, C.ADMIN_LIST})
# The administrator right each bot capability needs (Bot API ChatMemberAdministrator).
_RIGHT = {
    C.MESSAGE_DELETE: "can_delete_messages",
    C.MESSAGE_PIN: "can_pin_messages",
    C.MEMBER_REMOVE: "can_restrict_members",
    C.MEMBER_BAN: "can_restrict_members",
    C.MEMBER_UNBAN: "can_restrict_members",
    C.MEMBER_RESTRICT: "can_restrict_members",
    C.CHAT_SET_PERMISSIONS: "can_restrict_members",
    C.ADMIN_PROMOTE: "can_promote_members",
    C.ADMIN_DEMOTE: "can_promote_members",
    C.INVITE_CREATE: "can_invite_users",
    C.INVITE_EDIT: "can_invite_users",
    C.INVITE_REVOKE: "can_invite_users",
    C.JOIN_REQUEST_APPROVE: "can_invite_users",
    C.JOIN_REQUEST_REJECT: "can_invite_users",
    C.CHAT_SET_TITLE: "can_change_info",
    C.CHAT_SET_DESCRIPTION: "can_change_info",
    C.CHAT_SET_PHOTO: "can_change_info",
    C.TOPIC_CREATE: "can_manage_topics",
    C.TOPIC_EDIT: "can_manage_topics",
    C.TOPIC_CLOSE: "can_manage_topics",
    C.TOPIC_REOPEN: "can_manage_topics",
}
_TOPICS = frozenset({C.TOPIC_CREATE, C.TOPIC_EDIT, C.TOPIC_CLOSE, C.TOPIC_REOPEN})
assert _BOT == _MESSAGES | _PRESENT | set(_RIGHT)  # every bot capability has exactly one rule


_FAILURE_STATE = {
    ResultKind.FAILED_PERMANENT: S.NOT_AUTHORIZED,
    ResultKind.FAILED_TRANSIENT: S.TEMPORARILY_UNAVAILABLE,
}


def _object(api: BotApi, method: str, params: Mapping[str, Any]) -> Mapping[str, Any]:
    result = lookup(api, method, params)
    if not isinstance(result, dict):
        raise LookupFailed(ResultKind.OUTCOME_UNKNOWN)
    return result


class BotCapability:
    def __init__(self, api: BotApi | None, *, clock: Callable[[], datetime]) -> None:
        self._api, self._clock = api, clock
        self._me: int | None = None

    @classmethod
    def from_api(cls, api: BotApi, *, clock: Callable[[], datetime]) -> BotCapability:
        return cls(api, clock=clock)

    @classmethod
    def from_secrets(
        cls, secrets: SecretStore, *, version: int, clock: Callable[[], datetime]
    ) -> BotCapability:
        try:
            api: BotApi | None = BotApi(secrets, version=version)
        except (SecretStoreError, BotRefused):
            api = None
        return cls(api, clock=clock)

    def __repr__(self) -> str:
        return "BotCapability(<redacted>)"

    def snapshot(self, actor: str, destination: ProviderTarget) -> CapabilitySnapshot:
        if actor != ACTOR or destination.actor != ACTOR:
            raise ValueError("not a telegram_bot destination")
        states = {c: S.PROVIDER_UNSUPPORTED for c in TELEGRAM_CAPABILITIES}
        states.update(self._bot_states(int(destination.identity)))
        return CapabilitySnapshot(
            ACTOR, destination.destination_ref, states, timeutil.iso(self._clock())
        )

    def _bot_states(self, chat_id: int) -> dict[C, S]:
        if self._api is None:
            return dict.fromkeys(_BOT, S.NOT_CONFIGURED)
        try:
            if self._me is None:
                me = _object(self._api, "getMe", {}).get("id")
                if type(me) is not int:
                    raise LookupFailed(ResultKind.OUTCOME_UNKNOWN)
                self._me = me
            chat = _object(self._api, "getChat", {"chat_id": chat_id})
            member = _object(self._api, "getChatMember", {"chat_id": chat_id, "user_id": self._me})
        except LookupFailed as failed:
            return dict.fromkeys(_BOT, _FAILURE_STATE.get(failed.kind, S.UNKNOWN))
        return {c: _state(c, chat, member) for c in _BOT}


def _state(cap: C, chat: Mapping[str, Any], member: Mapping[str, Any]) -> S:
    status = member.get("status")
    if status not in ("creator", "administrator", "member", "restricted"):
        return S.NOT_AUTHORIZED  # left, kicked, or anything undocumented
    kind = chat.get("type")
    if cap in _MESSAGES:
        return S.AVAILABLE if _can_send(kind, status, chat, member) else S.NOT_AUTHORIZED
    if kind == "private":
        return S.UNAVAILABLE
    if cap in _PRESENT:
        return S.AVAILABLE
    if cap in _TOPICS and chat.get("is_forum") is not True:
        return S.UNAVAILABLE
    granted = status == "creator" or (status == "administrator" and member.get(_RIGHT[cap]) is True)
    return S.AVAILABLE if granted else S.NOT_AUTHORIZED


def _can_send(
    kind: object, status: str, chat: Mapping[str, Any], member: Mapping[str, Any]
) -> bool:
    if status == "creator" or kind == "private":
        return True
    if kind == "channel":
        return status == "administrator" and member.get("can_post_messages") is True
    if status == "administrator":
        return True
    if status == "restricted":
        return member.get("can_send_messages") is True
    permissions = chat.get("permissions")
    return isinstance(permissions, dict) and permissions.get("can_send_messages") is True
