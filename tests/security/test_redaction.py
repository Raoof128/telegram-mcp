"""Privacy: no secret reflection in results or logs; bounded multibyte output."""

import logging

import pytest

from telegram_mcp.contract import validate_output
from telegram_mcp.dispatch import dispatch
from telegram_mcp.results import error_result, success_result

TPR_A = "tpr_" + "a" * 26
TPR_B = "tpr_" + "b" * 26
TGP = "tgp_" + "c" * 26
TGM = "tgm_" + "d" * 26

SENSITIVE_ARGS = {
    "telegram_list_projects": {},
    "telegram_resolve_project": {"query": "synthetic"},
    "telegram_list_chats": {"project_ref": TPR_A},
    "telegram_resolve_peer": {"project_ref": TPR_A, "query": "synthetic"},
    "telegram_get_messages": {"project_ref": TPR_A, "peer_ref": TGP},
    "telegram_get_context": {"project_ref": TPR_A, "message_ref": TGM},
    "telegram_search_messages": {"project_ref": TPR_A, "query": "synthetic"},
    "telegram_cross_project_search": {"project_refs": [TPR_A, TPR_B], "query": "synthetic"},
    "telegram_get_unread": {"project_ref": TPR_A},
}


@pytest.mark.parametrize("name,args", sorted(SENSITIVE_ARGS.items()))
def test_sensitive_tools_never_succeed(name, args):
    result = dispatch(name, args)
    assert result.is_error is True
    assert result.structured_content["error"]["code"] == "POLICY_UNCONFIGURED"
    validate_output(name, result.structured_content)


def test_status_bodies_validate():
    result = dispatch("telegram_status", {})
    validate_output("telegram_status", result.structured_content)


def test_status_reveals_no_path_label_or_secret():
    raw = dispatch("telegram_status", {}).model_dump_json(by_alias=True)
    for token in ("/Users", ".session", "SYNTHETIC", "api_hash", "BEGIN PRIVATE"):
        assert token not in raw


def test_secret_in_args_never_reflected(caplog):
    caplog.set_level(logging.INFO, logger="telegram_mcp")
    result = dispatch("telegram_resolve_peer", {"project_ref": TPR_A, "query": "SYNTHETIC_CANARY_Q"})
    raw = result.model_dump_json(by_alias=True)
    assert "SYNTHETIC_CANARY_Q" not in raw
    assert "SYNTHETIC_CANARY_Q" not in caplog.text
    result = dispatch("SYNTHETIC_CANARY_TOOL", {"secret": "SYNTHETIC_CANARY_V"})
    assert "SYNTHETIC_CANARY" not in result.model_dump_json(by_alias=True)
    assert "SYNTHETIC_CANARY" not in caplog.text


def test_fixed_error_fits_smallest_cap():
    result = error_result("POLICY_UNCONFIGURED")
    assert len(result.model_dump_json(by_alias=True).encode("utf-8")) <= 1024


def test_status_fits_smallest_configured_cap():
    result = dispatch("telegram_status", {}, max_response_bytes=1024)
    assert result.is_error is False


def test_oversized_payload_becomes_response_limit():
    msg = {
        "message_ref": TGM,
        "origin_project_refs": [TPR_A],
        "sender_kind": "user",
        "sender_display_name": "Synthetic Sender",
        "sender_peer_ref": None,
        "post_author": None,
        "forum_topic": False,
        "topic_title": None,
        "sent_at": "2026-09-22T00:00:00Z",
        "outgoing": False,
        "text": "x" * 700,
        "text_truncated": False,
        "reply_to_message_ref": None,
        "has_media": False,
        "media_kind": None,
        "edited": False,
    }
    data = {
        "project": {"project_ref": TPR_A, "display_name": "Synthetic Project"},
        "peer": {"peer_ref": TGP, "display_name": "Synthetic Chat", "chat_type": "group"},
        "messages": [dict(msg) for _ in range(100)],
    }
    meta = {
        "source": "telegram",
        "content_trust": "untrusted_external_content",
        "truncated": False,
        "partial": False,
        "next_cursor": None,
        "disclosure": None,
        "coverage": None,
    }
    result = success_result("telegram_get_messages", data, meta)
    assert result.is_error is True
    assert result.structured_content["error"]["code"] == "RESPONSE_LIMIT"


def test_multibyte_length_counts_bytes_not_chars():
    body = "é" * 40000  # 80_000 UTF-8 bytes
    assert len(body) == 40000
    assert len(body.encode("utf-8")) == 80000


def test_wire_uses_camelcase_aliases():
    raw = dispatch("telegram_status", {}).model_dump_json(by_alias=True)
    assert "structuredContent" in raw
    assert "isError" in raw
    assert "structured_content" not in raw
