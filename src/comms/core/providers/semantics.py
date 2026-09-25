"""Operation semantics: one machine-readable idempotency table (comms v0.3 Task C3; A27, A41).

``SEMANTICS`` is keyed by ``(capability, transport_actor)`` and answers, for every operation an
actor supports: how it may be retried (``retry_class``), what makes a repeat harmless
(``idempotency_strategy``), and what an ambiguous outcome permits (``ambiguity_policy``).

- ``SET_STATE`` sets an exact state and is idempotent by nature: a repeat of the same key is safe.
- ``CREATE`` makes a new object each time; an ambiguous outcome is resolved, never re-sent.
- ``MESSAGE_SEND`` is idempotent over MTProto only, where ``random_id`` dedupes (A20); the Bot
  API and the Cloud API have no idempotency key.
- ``DESTRUCTIVE_NONIDEMPOTENT`` (O7) cannot be repeated safely and is resolve-only.
- A non-empty ``steps`` makes the operation a compound saga (A41): each step is its own entry
  and the executor persists each one (Task D4); ``step_args`` are the fixed per-step arguments.

``SUPPORT`` names which actor offers which capability. A capability absent from an actor's
provider API (a Bot API method that does not exist) is simply not supported there; the
capability snapshot (A25) reports it and the provider's answer stays final.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Literal

from comms.core.providers.capability import Capability as C

__all__ = ["READS", "SEMANTICS", "SUPPORT", "OperationSemantics", "is_write"]

RetryClass = Literal["SET_STATE", "CREATE", "DESTRUCTIVE_NONIDEMPOTENT", "MESSAGE_SEND", "READ"]


@dataclass(frozen=True)
class OperationSemantics:
    retry_class: RetryClass
    idempotency_strategy: Literal["natural", "provider_random_id", "none"]
    ambiguity_policy: Literal["retry_same_key", "resolve_only"]
    steps: tuple[C, ...] = ()
    step_args: tuple[Mapping[str, Any], ...] = field(default=())


BOT, USER, CLOUD, HOOKS = "telegram_bot", "telegram_user", "whatsapp_cloud", "whatsapp_webhooks"

# Capabilities that change nothing at the provider (inbound webhook deliveries included).
READS = frozenset(
    {
        C.MEMBER_LIST,
        C.MEMBER_GET,
        C.ADMIN_LIST,
        C.ADMIN_LOG_READ,
        C.INVITE_LIST,
        C.JOIN_REQUEST_LIST,
        C.TOPIC_LIST,
        C.HISTORY_READ,
        C.HISTORY_SEARCH,
        C.MEDIA_RETRIEVE,
        C.TEMPLATE_LIST,
        C.TEMPLATE_GET,
        C.WEBHOOK_RECEIVE_MESSAGE,
        C.WEBHOOK_RECEIVE_STATUS,
        C.ACCOUNT_INSPECT,
        C.PHONE_NUMBER_INSPECT,
        C.GROUP_LIST,
        C.GROUP_GET,
        C.GROUP_MEMBERS,
        C.GROUP_INVITE_GET,
    }
)


def is_write(capability: C) -> bool:
    return capability not in READS


_TELEGRAM = (BOT, USER)
# P §11 capabilities the Bot API has no method for; MTProto offers every one.
_USER_ONLY = frozenset(
    {
        C.MEMBER_LIST,
        C.MEMBER_ADD,
        C.ADMIN_LOG_READ,
        C.INVITE_LIST,
        C.JOIN_REQUEST_LIST,
        C.TOPIC_LIST,
        C.HISTORY_READ,
        C.HISTORY_SEARCH,
        C.GROUP_CREATE,
        C.GROUP_DELETE,
        C.GROUP_MIGRATE,
    }
)
_TELEGRAM_CAPS = (
    *(C.MESSAGE_SEND, C.MESSAGE_EDIT, C.MESSAGE_DELETE, C.MESSAGE_FORWARD, C.MESSAGE_PIN),
    *(C.MEMBER_LIST, C.MEMBER_GET, C.MEMBER_ADD, C.MEMBER_REMOVE),
    *(C.MEMBER_BAN, C.MEMBER_UNBAN, C.MEMBER_RESTRICT),
    *(C.ADMIN_LIST, C.ADMIN_PROMOTE, C.ADMIN_DEMOTE, C.ADMIN_LOG_READ),
    *(C.INVITE_CREATE, C.INVITE_EDIT, C.INVITE_REVOKE, C.INVITE_LIST),
    *(C.JOIN_REQUEST_LIST, C.JOIN_REQUEST_APPROVE, C.JOIN_REQUEST_REJECT),
    *(C.CHAT_SET_TITLE, C.CHAT_SET_DESCRIPTION, C.CHAT_SET_PHOTO, C.CHAT_SET_PERMISSIONS),
    *(C.TOPIC_LIST, C.TOPIC_CREATE, C.TOPIC_EDIT, C.TOPIC_CLOSE, C.TOPIC_REOPEN),
    *(C.HISTORY_READ, C.HISTORY_SEARCH, C.GROUP_CREATE, C.GROUP_DELETE, C.GROUP_MIGRATE),
)
_HOOK_CAPS = (C.WEBHOOK_RECEIVE_MESSAGE, C.WEBHOOK_RECEIVE_STATUS)

SUPPORT: Mapping[C, tuple[str, ...]] = MappingProxyType(
    {
        **{c: (USER,) if c in _USER_ONLY else _TELEGRAM for c in _TELEGRAM_CAPS},
        **{c: (CLOUD,) for c in C if c not in _TELEGRAM_CAPS and c not in _HOOK_CAPS},
        **dict.fromkeys(_HOOK_CAPS, (HOOKS,)),
    }
)

_READ = OperationSemantics("READ", "natural", "retry_same_key")
_SET = OperationSemantics("SET_STATE", "natural", "retry_same_key")
_CREATE = OperationSemantics("CREATE", "none", "resolve_only")
_DESTROY = OperationSemantics("DESTRUCTIVE_NONIDEMPOTENT", "none", "resolve_only")
_SEND_MTPROTO = OperationSemantics("MESSAGE_SEND", "provider_random_id", "retry_same_key")
_SEND_NO_KEY = OperationSemantics("MESSAGE_SEND", "none", "resolve_only")
# Removing a member from a supergroup is ban, then lift the ban so the member may rejoin; the
# unban carries only_if_banned so it never un-bans someone banned by another hand meanwhile.
_REMOVE_SAGA = OperationSemantics(
    "SET_STATE",
    "natural",
    "retry_same_key",
    steps=(C.MEMBER_BAN, C.MEMBER_UNBAN),
    step_args=(MappingProxyType({}), MappingProxyType({"only_if_banned": True})),
)

_SENDS = frozenset(
    {
        C.MESSAGE_SEND,
        C.MESSAGE_FORWARD,  # a forward is a new message (MTProto: one random_id per message)
        C.MESSAGE_REPLY,
        C.MESSAGE_SEND_TEXT,
        C.MESSAGE_SEND_IMAGE,
        C.MESSAGE_SEND_VIDEO,
        C.MESSAGE_SEND_AUDIO,
        C.MESSAGE_SEND_DOCUMENT,
        C.MESSAGE_SEND_LOCATION,
        C.MESSAGE_SEND_CONTACTS,
        C.MESSAGE_SEND_INTERACTIVE,
        C.MESSAGE_SEND_TEMPLATE,
        C.GROUP_MESSAGE_SEND,
    }
)
_CREATES = frozenset(
    {
        C.INVITE_CREATE,
        C.TOPIC_CREATE,
        C.GROUP_CREATE,
        C.MEDIA_UPLOAD,
        C.TEMPLATE_CREATE,
        C.CHAT_SET_PHOTO,  # each call adds a new photo to the chat's photo history
    }
)
_DESTROYS = frozenset(
    {
        C.GROUP_DELETE,
        C.GROUP_MIGRATE,
        C.GROUP_INVITE_RESET,  # a repeat revokes the link the first call just issued
        C.MESSAGE_DELETE,
        C.MEDIA_DELETE,
        C.TEMPLATE_DELETE,
    }
)


def _semantics(capability: C, actor: str) -> OperationSemantics:
    if capability in READS:
        return _READ
    if capability in _SENDS:
        return _SEND_MTPROTO if actor == USER else _SEND_NO_KEY
    if capability in _CREATES:
        return _CREATE
    if capability in _DESTROYS:
        return _DESTROY
    if capability is C.MEMBER_REMOVE:
        return _REMOVE_SAGA
    return _SET


SEMANTICS: Mapping[tuple[C, str], OperationSemantics] = MappingProxyType(
    {(c, actor): _semantics(c, actor) for c, actors in SUPPORT.items() for actor in actors}
)
