"""Bot API invites, join requests and forum topics (comms v0.3 Task C11; A27; P §27, §28).

``invite.create`` and ``topic.create`` are ``CREATE`` (resolve-only on ambiguity); their opaque
provider ref is the new link or thread id, and a success without it is malformed and therefore
``OUTCOME_UNKNOWN`` (A19). Everything else sets a state. The bot cannot list invites or join
requests; those capabilities are ``telegram_user`` only. Hiding the General topic has no
capability id in P and is not offered.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from types import MappingProxyType
from typing import Any

from comms.core.providers.capability import Capability as C
from comms.transports.telegram import chat_specs as specs

__all__ = ["INVITE_REQUESTS", "REF_FIELDS"]

REF_FIELDS: Mapping[C, str] = MappingProxyType(
    {C.INVITE_CREATE: "invite_link", C.TOPIC_CREATE: "message_thread_id"}
)
Request = Callable[[int, Mapping[str, Any]], tuple[str, dict[str, Any]]]


def _bot(method: str, spec: Callable[[Mapping[str, Any]], dict[str, Any]]) -> Request:
    def build(chat_id: int, args: Mapping[str, Any]) -> tuple[str, dict[str, Any]]:
        return method, {"chat_id": chat_id, **spec(args)}

    return build


INVITE_REQUESTS = {
    C.INVITE_CREATE: _bot("createChatInviteLink", specs.invite_create),
    C.INVITE_EDIT: _bot("editChatInviteLink", specs.invite_edit),
    C.INVITE_REVOKE: _bot("revokeChatInviteLink", specs.invite_revoke),
    C.JOIN_REQUEST_APPROVE: _bot("approveChatJoinRequest", specs.member),
    C.JOIN_REQUEST_REJECT: _bot("declineChatJoinRequest", specs.member),
    C.TOPIC_CREATE: _bot("createForumTopic", specs.topic_create),
    C.TOPIC_EDIT: _bot("editForumTopic", specs.topic_edit),
    C.TOPIC_CLOSE: _bot("closeForumTopic", specs.thread),
    C.TOPIC_REOPEN: _bot("reopenForumTopic", specs.thread),
}
