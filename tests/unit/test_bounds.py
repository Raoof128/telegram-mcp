import pytest

from telegram_mcp.consent.challenge import jcs_dumps
from telegram_mcp.disclosure.bounds import (
    DATA_BYTES_MAX,
    NAME_MAX,
    TEXT_MAX,
    clamp,
    fit,
    worst_case,
)

P = "tpr_" + "a" * 26
R = "tgp_" + "b" * 26
M = "tgm_" + "c" * 26


def test_clamp_truncates_by_codepoint_and_repairs_surrogates():
    assert clamp(None, 5) == (None, False)
    assert clamp("héllo", 5) == ("héllo", False)
    assert clamp("héllo!", 5) == ("héllo", True)
    repaired, cut = clamp("a\ud800b", 10)
    assert repaired == "a�b" and cut is False
    jcs_dumps({"t": repaired})  # encodable


def _real_message(text):
    return {
        "message_ref": M,
        "origin_project_refs": [P],
        "sender_kind": "anonymous_admin",
        "sender_display_name": "\x01" * NAME_MAX,
        "sender_peer_ref": R,
        "post_author": "\x01" * NAME_MAX,
        "forum_topic": True,
        "topic_title": None,
        "sent_at": "2026-09-23T00:00:00Z",
        "outgoing": False,
        "text": text,
        "text_truncated": True,
        "reply_to_message_ref": M,
        "has_media": True,
        "media_kind": "\x01" * 32,
        "edited": False,
    }


@pytest.mark.parametrize(
    "level,excerpt,text",
    [
        ("metadata_only", None, None),
        ("excerpt", 64, "\x01" * 64),
        ("full_text", None, "\x01" * TEXT_MAX),
    ],
)
def test_the_message_bound_dominates_the_worst_real_record(level, excerpt, text):
    records, total, project = worst_case(
        "telegram_get_messages",
        limit=1,
        project_ref=P,
        project_display_name="Alpha",
        egress_level=level,
        excerpt_limit=excerpt,
    )
    assert records == 1
    assert project >= len(jcs_dumps(_real_message(text)))
    data = {
        "project": {"project_ref": P, "display_name": "Alpha"},
        "peer": {"peer_ref": R, "display_name": "\x01" * NAME_MAX, "chat_type": "supergroup"},
        "messages": [_real_message(text)],
    }
    assert total >= len(jcs_dumps(data))


def test_byte_bounds_cap_at_the_page_cap_and_metadata_is_cheaper():
    kw = {"project_ref": P, "project_display_name": "Alpha", "excerpt_limit": None}
    full = worst_case("telegram_get_messages", limit=30, egress_level="full_text", **kw)
    assert full[1] == full[2] == DATA_BYTES_MAX
    meta = worst_case("telegram_get_messages", limit=3, egress_level="metadata_only", **kw)
    text = worst_case("telegram_get_messages", limit=3, egress_level="full_text", **kw)
    assert meta[1] < text[1]


def test_fit_keeps_the_longest_prefix_under_the_cap():
    big = {"text": "\x01" * TEXT_MAX}  # about 24.6 KB canonical
    data = {"project": {"project_ref": P}, "messages": [big, big, big]}
    assert fit(data, "messages") == 2
    assert len(data["messages"]) == 1 and len(jcs_dumps(data)) <= DATA_BYTES_MAX
    small = {"project": {}, "messages": [{"a": 1}] * 5}
    assert fit(small, "messages") == 0 and len(small["messages"]) == 5


@pytest.mark.parametrize(
    "tool", ["telegram_list_chats", "telegram_resolve_peer", "telegram_get_unread"]
)
def test_every_project_tool_has_a_bound(tool):
    records, total, project = worst_case(
        tool,
        limit=5,
        project_ref=P,
        project_display_name="Alpha",
        egress_level="full_text",
        excerpt_limit=None,
    )
    assert records == 5 and total > project > 0


def test_an_unbounded_tool_is_refused():
    with pytest.raises(ValueError):
        worst_case(
            "telegram_list_projects",
            limit=1,
            project_ref=P,
            project_display_name="A",
            egress_level="full_text",
            excerpt_limit=None,
        )
