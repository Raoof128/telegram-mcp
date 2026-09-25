"""Capability ids and states (comms v0.3 Task C2; P §9, §11, §14, §16).

The ids are exactly the union of the proposal's §11, §14 and §16 lists (a test re-derives
them from the verbatim proposal); §16's group operations are a capability-gated extension.
A state is resolved per actor and destination at call time; ``UNKNOWN`` is never treated as
``AVAILABLE`` (P §9).
"""

from __future__ import annotations

from enum import StrEnum

__all__ = ["Capability", "CapabilityState", "is_available"]


class CapabilityState(StrEnum):
    AVAILABLE = "AVAILABLE"
    UNAVAILABLE = "UNAVAILABLE"
    NOT_AUTHORIZED = "NOT_AUTHORIZED"
    ACCOUNT_INELIGIBLE = "ACCOUNT_INELIGIBLE"
    PROVIDER_UNSUPPORTED = "PROVIDER_UNSUPPORTED"
    NOT_CONFIGURED = "NOT_CONFIGURED"
    TEMPORARILY_UNAVAILABLE = "TEMPORARILY_UNAVAILABLE"
    UNKNOWN = "UNKNOWN"


def is_available(state: CapabilityState) -> bool:
    """Only an explicit ``AVAILABLE``; ``UNKNOWN`` and every other state are not."""
    return state is CapabilityState.AVAILABLE


class Capability(StrEnum):
    # P §11: Telegram capability discovery
    MESSAGE_SEND = "message.send"
    MESSAGE_EDIT = "message.edit"
    MESSAGE_DELETE = "message.delete"
    MESSAGE_FORWARD = "message.forward"
    MESSAGE_PIN = "message.pin"
    MEMBER_LIST = "member.list"
    MEMBER_GET = "member.get"
    MEMBER_ADD = "member.add"
    MEMBER_REMOVE = "member.remove"
    MEMBER_BAN = "member.ban"
    MEMBER_UNBAN = "member.unban"
    MEMBER_RESTRICT = "member.restrict"
    ADMIN_LIST = "admin.list"
    ADMIN_PROMOTE = "admin.promote"
    ADMIN_DEMOTE = "admin.demote"
    ADMIN_LOG_READ = "admin.log.read"
    INVITE_CREATE = "invite.create"
    INVITE_EDIT = "invite.edit"
    INVITE_REVOKE = "invite.revoke"
    INVITE_LIST = "invite.list"
    JOIN_REQUEST_LIST = "join_request.list"
    JOIN_REQUEST_APPROVE = "join_request.approve"
    JOIN_REQUEST_REJECT = "join_request.reject"
    CHAT_SET_TITLE = "chat.set_title"
    CHAT_SET_DESCRIPTION = "chat.set_description"
    CHAT_SET_PHOTO = "chat.set_photo"
    CHAT_SET_PERMISSIONS = "chat.set_permissions"
    TOPIC_LIST = "topic.list"
    TOPIC_CREATE = "topic.create"
    TOPIC_EDIT = "topic.edit"
    TOPIC_CLOSE = "topic.close"
    TOPIC_REOPEN = "topic.reopen"
    HISTORY_READ = "history.read"
    HISTORY_SEARCH = "history.search"
    GROUP_CREATE = "group.create"
    GROUP_DELETE = "group.delete"
    GROUP_MIGRATE = "group.migrate"
    # P §14: WhatsApp baseline capabilities
    MESSAGE_REPLY = "message.reply"
    MESSAGE_MARK_READ = "message.mark_read"
    MESSAGE_SEND_TEXT = "message.send_text"
    MESSAGE_SEND_IMAGE = "message.send_image"
    MESSAGE_SEND_VIDEO = "message.send_video"
    MESSAGE_SEND_AUDIO = "message.send_audio"
    MESSAGE_SEND_DOCUMENT = "message.send_document"
    MESSAGE_SEND_LOCATION = "message.send_location"
    MESSAGE_SEND_CONTACTS = "message.send_contacts"
    MESSAGE_SEND_INTERACTIVE = "message.send_interactive"
    MESSAGE_SEND_TEMPLATE = "message.send_template"
    MEDIA_UPLOAD = "media.upload"
    MEDIA_RETRIEVE = "media.retrieve"
    MEDIA_DELETE = "media.delete"
    TEMPLATE_LIST = "template.list"
    TEMPLATE_GET = "template.get"
    TEMPLATE_CREATE = "template.create"
    TEMPLATE_EDIT = "template.edit"
    TEMPLATE_DELETE = "template.delete"
    WEBHOOK_RECEIVE_MESSAGE = "webhook.receive_message"
    WEBHOOK_RECEIVE_STATUS = "webhook.receive_status"
    ACCOUNT_INSPECT = "account.inspect"
    PHONE_NUMBER_INSPECT = "phone_number.inspect"
    # P §16: WhatsApp groups (capability-gated)
    GROUP_LIST = "group.list"
    GROUP_GET = "group.get"
    GROUP_MEMBERS = "group.members"
    GROUP_MEMBER_REMOVE = "group.member.remove"
    GROUP_INVITE_GET = "group.invite.get"
    GROUP_INVITE_RESET = "group.invite.reset"
    GROUP_SETTINGS_UPDATE = "group.settings.update"
    GROUP_MESSAGE_SEND = "group.message.send"
