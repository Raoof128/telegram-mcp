"""Admin promotion rights and the P §74 profiles, shared by the bot and user actors.

Rights are named as P §26 names them (the Bot API's ``can_…`` names); ``MTPROTO_RIGHT`` maps each
to its MTProto ``ChatAdminRights`` flag, one to one. A promotion names a profile or, with
``custom``, its exact rights (at least one granted); "make admin" alone is refused. Every
promotion is the full exact set: unnamed rights are false.
"""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType
from typing import Any

from comms.transports.telegram.args import positive_int, take

__all__ = ["ADMIN_RIGHTS", "MTPROTO_RIGHT", "PROFILES", "promotion"]

# Bot API promoteChatMember rights (P §26 names).
ADMIN_RIGHTS = frozenset(
    {
        "is_anonymous",
        "can_manage_chat",
        "can_delete_messages",
        "can_manage_video_chats",
        "can_restrict_members",
        "can_promote_members",
        "can_change_info",
        "can_invite_users",
        "can_post_stories",
        "can_edit_stories",
        "can_delete_stories",
        "can_post_messages",
        "can_edit_messages",
        "can_pin_messages",
        "can_manage_topics",
    }
)


def _profile(*granted: str) -> Mapping[str, bool]:
    assert set(granted) <= ADMIN_RIGHTS
    return MappingProxyType({right: right in granted for right in sorted(ADMIN_RIGHTS)})


PROFILES: Mapping[str, Mapping[str, bool]] = MappingProxyType(
    {
        "moderator": _profile(
            "can_manage_chat", "can_delete_messages", "can_restrict_members", "can_pin_messages"
        ),
        "event_admin": _profile(
            "can_manage_chat",
            "can_invite_users",
            "can_pin_messages",
            "can_manage_video_chats",
            "can_manage_topics",
        ),
        "full_admin": _profile(*(ADMIN_RIGHTS - {"is_anonymous"})),
    }
)


def _rights(value: object) -> bool:
    return (
        isinstance(value, Mapping)
        and set(value) <= ADMIN_RIGHTS
        and all(type(v) is bool for v in value.values())
        and any(value.values())
    )


MTPROTO_RIGHT: Mapping[str, str] = MappingProxyType(
    {
        "is_anonymous": "anonymous",
        "can_manage_chat": "other",
        "can_delete_messages": "delete_messages",
        "can_manage_video_chats": "manage_call",
        "can_restrict_members": "ban_users",
        "can_promote_members": "add_admins",
        "can_change_info": "change_info",
        "can_invite_users": "invite_users",
        "can_post_stories": "post_stories",
        "can_edit_stories": "edit_stories",
        "can_delete_stories": "delete_stories",
        "can_post_messages": "post_messages",
        "can_edit_messages": "edit_messages",
        "can_pin_messages": "pin_messages",
        "can_manage_topics": "manage_topics",
    }
)
assert set(MTPROTO_RIGHT) == ADMIN_RIGHTS


def promotion(args: Mapping[str, Any]) -> tuple[int, dict[str, bool]]:
    """``(user_id, the exact rights)`` of a promotion request, or ``ValueError``."""
    if args.get("profile") == "custom":
        required = {"user_id": positive_int, "profile": lambda v: v == "custom", "rights": _rights}
        fields = take(args, required, {})
        return fields["user_id"], {r: fields["rights"].get(r, False) for r in sorted(ADMIN_RIGHTS)}
    fields = take(
        args,
        {"user_id": positive_int, "profile": lambda v: isinstance(v, str) and v in PROFILES},
        {},
    )
    return fields["user_id"], dict(PROFILES[fields["profile"]])
