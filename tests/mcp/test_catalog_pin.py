"""comms v0.3 Task D25: TOOL_CATALOG pinned by count, order and digests (A29; P §39, §79).

A change to any tool — name, order, schema, annotation, failure modes or service — changes a
pinned digest and fails here until ``catalog_pin.json`` is regenerated on purpose.
"""

import json
import subprocess
import sys
from pathlib import Path

import pytest

from comms.core.providers.capability import Capability as C
from comms.core.providers.semantics import SUPPORT
from comms.mcp.catalog import TOOL_CATALOG, catalog_digest, tool_schema_digest, tools_list_payload

ROOT = Path(__file__).resolve().parents[2]
PIN = json.loads((Path(__file__).parent / "catalog_pin.json").read_text(encoding="utf-8"))
BY_NAME = {spec.name: spec for spec in TOOL_CATALOG}

# P §79: every supported semantic capability has a tool. WhatsApp's per-type sends are the
# campaign path (window and templates enforced there); inbound webhooks and account inspection
# surface through the status tools.
CAPABILITY_TOOLS = {
    C.ACCOUNT_INSPECT: "comms_account_status",
    C.ADMIN_DEMOTE: "comms_group_admin_demote",
    C.ADMIN_LIST: "comms_group_admins_list",
    C.ADMIN_LOG_READ: "comms_group_admin_log",
    C.ADMIN_PROMOTE: "comms_group_admin_promote",
    C.CHAT_SET_DESCRIPTION: "comms_group_info_set_description",
    C.CHAT_SET_PERMISSIONS: "comms_group_permissions_set",
    C.CHAT_SET_PHOTO: "comms_group_info_set_photo",
    C.CHAT_SET_TITLE: "comms_group_info_set_title",
    C.GROUP_CREATE: "comms_group_create",
    C.GROUP_DELETE: "comms_group_delete",
    C.GROUP_GET: "comms_group_get",
    C.GROUP_INVITE_GET: "comms_group_invite_list",
    C.GROUP_INVITE_RESET: "comms_group_invite_revoke",
    C.GROUP_LIST: "comms_group_list",
    C.GROUP_MEMBER_REMOVE: "comms_group_member_remove",
    C.GROUP_MEMBERS: "comms_group_members_list",
    C.GROUP_MESSAGE_SEND: "comms_message_send",
    C.GROUP_MIGRATE: "comms_group_migrate",
    C.GROUP_SETTINGS_UPDATE: "comms_group_info_set_title",
    C.HISTORY_READ: "comms_context_recent",
    C.HISTORY_SEARCH: "comms_context_search",
    C.INVITE_CREATE: "comms_group_invite_create",
    C.INVITE_EDIT: "comms_group_invite_edit",
    C.INVITE_LIST: "comms_group_invite_list",
    C.INVITE_REVOKE: "comms_group_invite_revoke",
    C.JOIN_REQUEST_APPROVE: "comms_group_join_requests_approve",
    C.JOIN_REQUEST_LIST: "comms_group_join_requests_list",
    C.JOIN_REQUEST_REJECT: "comms_group_join_requests_reject",
    C.MEDIA_DELETE: "comms_media_delete",
    C.MEDIA_RETRIEVE: "comms_media_download",
    C.MEDIA_UPLOAD: "comms_media_upload",
    C.MEMBER_ADD: "comms_group_member_add",
    C.MEMBER_BAN: "comms_group_member_ban",
    C.MEMBER_GET: "comms_group_members_get",
    C.MEMBER_LIST: "comms_group_members_list",
    C.MEMBER_REMOVE: "comms_group_member_remove",
    C.MEMBER_RESTRICT: "comms_group_member_restrict",
    C.MEMBER_UNBAN: "comms_group_member_unban",
    C.MESSAGE_DELETE: "comms_message_delete",
    C.MESSAGE_EDIT: "comms_message_edit",
    C.MESSAGE_FORWARD: "comms_message_forward",
    C.MESSAGE_MARK_READ: "comms_message_mark_read",
    C.MESSAGE_PIN: "comms_message_pin",
    C.MESSAGE_REPLY: "comms_message_reply",
    C.MESSAGE_SEND: "comms_message_send",
    **{
        c: "comms_campaign_send"
        for c in (
            C.MESSAGE_SEND_AUDIO,
            C.MESSAGE_SEND_CONTACTS,
            C.MESSAGE_SEND_DOCUMENT,
            C.MESSAGE_SEND_IMAGE,
            C.MESSAGE_SEND_INTERACTIVE,
            C.MESSAGE_SEND_LOCATION,
            C.MESSAGE_SEND_TEMPLATE,
            C.MESSAGE_SEND_TEXT,
            C.MESSAGE_SEND_VIDEO,
        )
    },
    C.PHONE_NUMBER_INSPECT: "comms_whatsapp_phone_status",
    C.TEMPLATE_CREATE: "comms_whatsapp_template_create",
    C.TEMPLATE_DELETE: "comms_whatsapp_template_delete",
    C.TEMPLATE_EDIT: "comms_whatsapp_template_edit",
    C.TEMPLATE_GET: "comms_whatsapp_template_get",
    C.TEMPLATE_LIST: "comms_whatsapp_template_list",
    C.TOPIC_CLOSE: "comms_group_topic_close",
    C.TOPIC_CREATE: "comms_group_topic_create",
    C.TOPIC_EDIT: "comms_group_topic_edit",
    C.TOPIC_LIST: "comms_group_topic_list",
    C.TOPIC_REOPEN: "comms_group_topic_reopen",
    C.WEBHOOK_RECEIVE_MESSAGE: "comms_whatsapp_webhook_status",
    C.WEBHOOK_RECEIVE_STATUS: "comms_whatsapp_webhook_status",
}

_DIGEST_SCRIPT = (
    "import json; from comms.mcp.catalog import catalog_digest, tools_list_payload; "
    "print(json.dumps({'digest': catalog_digest(), 'payload': tools_list_payload()}, "
    "sort_keys=True, separators=(',', ':')))"
)


def test_catalog_count_and_order_pinned():
    assert len(TOOL_CATALOG) == PIN["count"] == len(PIN["names"])
    assert [spec.name for spec in TOOL_CATALOG] == PIN["names"]


@pytest.mark.parametrize("name", PIN["names"])
def test_per_tool_schema_digests_pinned(name):
    assert tool_schema_digest(BY_NAME[name]) == PIN["tools"][name]


def test_overall_catalog_digest_pinned():
    assert catalog_digest() == PIN["catalog"]


def test_canonical_catalog_serialization_byte_identical_across_processes():
    runs = [
        subprocess.run(
            [sys.executable, "-c", _DIGEST_SCRIPT],
            cwd=ROOT,
            capture_output=True,
            check=True,
            env={"PYTHONHASHSEED": seed, "PATH": "/usr/bin:/bin"},
        ).stdout
        for seed in ("0", "1", "4242")
    ]
    assert runs[0] == runs[1] == runs[2]
    assert json.loads(runs[0])["digest"] == PIN["catalog"]


def test_tools_list_payload_structurally_identical_ignoring_jsonrpc_framing():
    payload = tools_list_payload()
    framed = {"jsonrpc": "2.0", "id": 7, "result": {"tools": payload}}
    unframed = json.loads(json.dumps(framed))["result"]["tools"]
    assert unframed == payload == tools_list_payload()
    assert [t["name"] for t in payload] == PIN["names"]
    for entry in payload:
        assert set(entry) == {
            "name",
            "title",
            "description",
            "inputSchema",
            "outputSchema",
            "annotations",
        }


def test_every_semantic_capability_has_a_tool():
    assert set(CAPABILITY_TOOLS) == set(SUPPORT), set(SUPPORT) ^ set(CAPABILITY_TOOLS)
    missing = {c.value: t for c, t in CAPABILITY_TOOLS.items() if t not in BY_NAME}
    assert missing == {}
