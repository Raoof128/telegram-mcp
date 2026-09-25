"""Template, media, account, capability and identity-inspect tools (comms v0.3 Task D24;
P §32–35, §44).

``comms_admin_identity_inspect`` is the only tool whose output may carry a provider identity,
and only for a ref the owner names; it is read-only. Every other tool here returns refs,
states and counts. Media bytes never pass through a tool call: upload and download answer
PROVIDER_UNSUPPORTED until a staged-file handle exists.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from comms.core.providers.capability import Capability as C
from comms.mcp.schemas import (
    ACTOR,
    ANY_OBJECT,
    BOOL,
    OUTCOMES,
    READ_FAILURES,
    array,
    enum,
    integer,
    nullable,
    obj,
    read,
    ref,
    string,
    write,
)
from comms.mcp.spec import ToolSpec

__all__ = ["ACCOUNT_TOOLS"]

_TRANSPORTS = ("telegram", "whatsapp")
_STATES = {"type": "object", "additionalProperties": {"type": "string", "maxLength": 32}}
_TEMPLATE = obj(
    {
        "name": string(1, 512),
        "language": string(2, 16),
        "status": string(1, 32),
        "category": string(1, 32),
        "schema_version": integer(0),
        "components": array(ANY_OBJECT, high=20),
    },
    ["name", "language", "status", "category", "schema_version", "components"],
)
_PROVIDER_FAILURES = ("NOT_CONFIGURED", "PROVIDER_UNAVAILABLE", "PROVIDER_UNSUPPORTED")
_ACTOR_STATUS = obj(
    {
        "configured": BOOL,
        "available": integer(0),
        "reasons": {"type": "object", "additionalProperties": integer(0)},
    },
    ["configured", "available", "reasons"],
)
_STATUS = obj({"actors": {"type": "object", "additionalProperties": _ACTOR_STATUS}}, ["actors"])


def provider_result(**extra: Mapping[str, Any]) -> dict[str, Any]:
    """An account-level provider write's outcome (no group)."""
    fields = {
        "result": enum(OUTCOMES),
        "code": nullable(string(1, 64)),
        "actor": nullable(ACTOR),
        "op_ref": nullable(ref("operation")),
        "replayed": BOOL,
        **extra,
    }
    return obj(fields, list(fields))


def _status(
    name: str, title: str, description: str, service: str, output: Mapping[str, Any]
) -> ToolSpec:
    return read(
        f"comms_{name}",
        title,
        description,
        service,
        {},
        [],
        output,
        failures=(*READ_FAILURES, *_PROVIDER_FAILURES),
        open_world=True,
    )


ACCOUNT_TOOLS: tuple[ToolSpec, ...] = (
    # -- WhatsApp templates (P §32) ------------------------------------------------------------
    read(
        "comms_whatsapp_template_list",
        "List templates",
        "The business account's message templates: name, language, status and category.",
        "whatsapp.template_list",
        {"limit": integer(1, 100), "cursor": string(1, 256)},
        [],
        obj(
            {"items": array(_TEMPLATE, high=100), "next_cursor": nullable(string(1, 256))},
            ["items", "next_cursor"],
        ),
        failures=(*READ_FAILURES, *_PROVIDER_FAILURES),
        open_world=True,
    ),
    read(
        "comms_whatsapp_template_get",
        "Get a template",
        "One template by name and language.",
        "whatsapp.template_get",
        {"name": string(1, 512), "language": string(2, 16)},
        ["name", "language"],
        _TEMPLATE,
        failures=(*READ_FAILURES, *_PROVIDER_FAILURES),
        open_world=True,
    ),
    write(
        "comms_whatsapp_template_create",
        "Create a template",
        "Submit a template for review; the result names it by a ctp_ ref.",
        "whatsapp.template_create",
        {
            "name": {"type": "string", "pattern": "^[a-z0-9_]{1,512}$"},
            "language": string(2, 16),
            "category": enum(("MARKETING", "UTILITY", "AUTHENTICATION")),
            "components": array(ANY_OBJECT, low=1, high=20),
        },
        ["name", "language", "category", "components"],
        provider_result(template=nullable(ref("template"))),
        capability=C.TEMPLATE_CREATE,
    ),
    write(
        "comms_whatsapp_template_edit",
        "Edit a template",
        "Replace a template's components, by its ref.",
        "whatsapp.template_edit",
        {"template": ref("template"), "components": array(ANY_OBJECT, low=1, high=20)},
        ["template", "components"],
        provider_result(template=ref("template")),
        capability=C.TEMPLATE_EDIT,
        destructive=True,
    ),
    write(
        "comms_whatsapp_template_delete",
        "Delete a template",
        "Delete every language of a template name.",
        "whatsapp.template_delete",
        {"name": {"type": "string", "pattern": "^[a-z0-9_]{1,512}$"}},
        ["name"],
        provider_result(),
        capability=C.TEMPLATE_DELETE,
    ),
    # -- media (P §33) ------------------------------------------------------------------------
    read(
        "comms_media_inspect",
        "Inspect media",
        "A media object's type, size and digest, by its med_ ref.",
        "media.inspect",
        {"media": ref("media")},
        ["media"],
        obj(
            {
                "media": ref("media"),
                "mime": nullable(string(1, 128)),
                "size": nullable(integer(0)),
                "sha256": nullable(string(1, 128)),
            },
            ["media", "mime", "size", "sha256"],
        ),
        failures=(*READ_FAILURES, *_PROVIDER_FAILURES),
        open_world=True,
    ),
    write(
        "comms_media_upload",
        "Upload media",
        "Upload a staged file. Not offered yet: answers PROVIDER_UNSUPPORTED.",
        "media.upload",
        {"file": string(1, 256), "mime": string(1, 128)},
        ["file", "mime"],
        provider_result(media=nullable(ref("media"))),
        capability=C.MEDIA_UPLOAD,
    ),
    read(
        "comms_media_download",
        "Download media",
        "Save a media object to a staged file. Not offered yet: answers PROVIDER_UNSUPPORTED.",
        "media.download",
        {"media": ref("media")},
        ["media"],
        obj({"media": ref("media"), "file": string(1, 256)}, ["media", "file"]),
        failures=(*READ_FAILURES, *_PROVIDER_FAILURES),
        open_world=True,
    ),
    write(
        "comms_media_delete",
        "Delete media",
        "Delete a media object from the provider, by its ref.",
        "media.delete",
        {"media": ref("media")},
        ["media"],
        provider_result(media=ref("media")),
        capability=C.MEDIA_DELETE,
    ),
    # -- account (P §34) ----------------------------------------------------------------------
    _status(
        "account_status",
        "Account status",
        "Each actor: configured or not, how many capabilities are available, and why the "
        "rest are not.",
        "account.status",
        _STATUS,
    ),
    _status(
        "account_profile",
        "Account profile",
        "The accounts in use, by actor and kind — never a phone number or user id.",
        "account.profile",
        obj(
            {
                "actors": {
                    "type": "object",
                    "additionalProperties": obj(
                        {"configured": BOOL, "kind": string(1, 32)}, ["configured", "kind"]
                    ),
                }
            },
            ["actors"],
        ),
    ),
    _status(
        "account_capabilities",
        "Account capabilities",
        "Every capability id each configured actor can ever offer.",
        "account.capabilities",
        {"type": "object", "additionalProperties": array(string(1, 64))},
    ),
    _status(
        "telegram_bot_status",
        "Bot status",
        "The Telegram bot's status.",
        "telegram.bot_status",
        _STATUS,
    ),
    _status(
        "telegram_user_status",
        "User account status",
        "The Telegram user account's status.",
        "telegram.user_status",
        _STATUS,
    ),
    _status(
        "whatsapp_account_status",
        "WhatsApp account status",
        "The WhatsApp business account's status.",
        "whatsapp.account_status",
        _STATUS,
    ),
    _status(
        "whatsapp_phone_status",
        "WhatsApp number status",
        "The business number's quality rating and connection status — not the number.",
        "whatsapp.phone_status",
        obj(
            {"quality_rating": nullable(string(1, 32)), "status": nullable(string(1, 32))},
            ["quality_rating", "status"],
        ),
    ),
    _status(
        "whatsapp_webhook_status",
        "Webhook status",
        "Whether the webhook is configured, and its inbox's pending and completed counts.",
        "whatsapp.webhook_status",
        obj(
            {"configured": BOOL, "pending": integer(0), "completed": integer(0)},
            ["configured", "pending", "completed"],
        ),
    ),
    # -- capability (P §35) -------------------------------------------------------------------
    read(
        "comms_capability_get",
        "Get a capability",
        "One actor's state for one capability at one group.",
        "capability.get",
        {"group": ref("group"), "actor": ACTOR, "capability": string(1, 64)},
        ["group", "actor", "capability"],
        obj(
            {
                "group_ref": ref("group"),
                "actor": ACTOR,
                "capability": string(1, 64),
                "state": string(1, 32),
            },
            ["group_ref", "actor", "capability", "state"],
        ),
        open_world=True,
    ),
    read(
        "comms_capability_for_group",
        "Capabilities for a group",
        "Every configured actor's capability states at one group (P §35).",
        "capability.for_group",
        {"group": ref("group")},
        ["group"],
        obj(
            {
                "group_ref": ref("group"),
                "actors": {"type": "object", "additionalProperties": _STATES},
            },
            ["group_ref", "actors"],
        ),
        open_world=True,
    ),
    read(
        "comms_capability_for_actor",
        "Capabilities for an actor",
        "One actor's capability states across groups.",
        "capability.for_actor",
        {"actor": ACTOR, "groups": array(ref("group"), low=1, high=20)},
        ["actor", "groups"],
        obj(
            {"actor": ACTOR, "groups": {"type": "object", "additionalProperties": _STATES}},
            ["actor", "groups"],
        ),
        open_world=True,
    ),
    read(
        "comms_capability_refresh",
        "Refresh capabilities",
        "Ask the providers again and return the group's fresh capability states.",
        "capability.refresh",
        {"group": ref("group")},
        ["group"],
        obj(
            {
                "group_ref": ref("group"),
                "actors": {"type": "object", "additionalProperties": _STATES},
            },
            ["group_ref", "actors"],
        ),
        open_world=True,
    ),
    # -- identity inspection (P §44) ------------------------------------------------------------
    read(
        "comms_admin_identity_inspect",
        "Inspect identities",
        "Owner troubleshooting only: the provider identities behind one ref — a group, "
        "destination, recipient, contact point or provider object. The only tool that returns "
        "them; nothing else ever does.",
        "admin.identity_inspect",
        {
            "ref": ref(
                "group",
                "destination",
                "recipient",
                "contact_point",
                "message",
                "invite",
                "template",
                "topic",
                "media",
            )
        },
        ["ref"],
        obj(
            {
                "ref": string(1, 64),
                "identities": array(
                    obj(
                        {"transport": enum(_TRANSPORTS), "identity": string(1, 256)},
                        ["transport", "identity"],
                    ),
                    high=50,
                ),
            },
            ["ref", "identities"],
        ),
    ),
)
