"""4c worst-case shapes dominate the widest real records."""

import pytest

from comms.core.canonical import jcs_dumps
from comms.transports.telegram.disclosure.bounds import (
    DATA_BYTES_MAX,
    NAME_MAX,
    TEXT_CODEPOINTS_MAX,
    TEXT_MAX,
    PageBudget,
    fit,
    text_codepoints,
    worst_case,
)

P, Q = "tpr_" + "a" * 26, "tpr_" + "b" * 26
R, M = "tgp_" + "b" * 26, "tgm_" + "c" * 26
W = "\x01"


def _hit(origins, matched=None):
    hit = {
        "origin_project_refs": origins,
        "peer_ref": R,
        "peer_display_name": W * NAME_MAX,
        "message_ref": M,
        "sender_kind": "anonymous_admin",
        "sender_display_name": W * NAME_MAX,
        "sent_at": "2026-09-23T00:00:00Z",
        "text": W * 64,
        "text_truncated": True,
        "has_context": True,
    }
    if matched is not None:
        hit["matched_projects"] = matched
    return hit


def test_search_bound_dominates_a_real_hit():
    records, total, project = worst_case(
        "telegram_search_messages",
        limit=1,
        project_ref=P,
        project_display_name="Alpha",
        egress_level="excerpt",
        excerpt_limit=64,
    )
    data = {
        "project": {"project_ref": P, "display_name": "Alpha"},
        "results": [_hit([P])],
        "search_scope": "project",
    }
    assert records == 1 and project >= len(jcs_dumps(_hit([P]))) and total >= len(jcs_dumps(data))


def test_cross_bound_names_every_selected_project():
    projects = [(P, "Alpha"), (Q, "انجمن")]
    matched = [{"project_ref": r, "display_name": d} for r, d in projects]
    _records, total, project = worst_case(
        "telegram_cross_project_search",
        limit=1,
        project_ref="",
        project_display_name="",
        egress_level="excerpt",
        excerpt_limit=64,
        projects=projects,
    )
    data = {
        "projects": matched,
        "results": [_hit([P, Q], matched)],
        "search_scope": "cross_project",
    }
    assert project >= len(jcs_dumps(_hit([P, Q], matched))) and total >= len(jcs_dumps(data))


def test_context_bound_covers_101_full_messages_at_the_page_cap():
    records, total, project = worst_case(
        "telegram_get_context",
        limit=101,
        project_ref=P,
        project_display_name="Alpha",
        egress_level="full_text",
        excerpt_limit=None,
    )
    assert records == 101 and total == project == DATA_BYTES_MAX
    small = worst_case(
        "telegram_get_context",
        limit=1,
        project_ref=P,
        project_display_name="Alpha",
        egress_level="full_text",
        excerpt_limit=None,
    )
    assert small[2] >= TEXT_MAX * 6  # one full-length message fits in the bound


@pytest.mark.parametrize(
    "unit",
    [
        "a",  # ASCII: the codepoint cap binds before the byte cap
        "س",  # Persian: two bytes per codepoint
        "é",  # a combining mark is its own codepoint
        "👍🏽",  # emoji with a skin-tone modifier: two codepoints, eight bytes
        "‌",  # zero-width non-joiner
    ],
)
def test_a_page_holds_both_caps_for_every_script(unit):
    """Spec §13.2: combined text <= 32,000 codepoints AND data <= the byte cap."""
    text = unit * (4000 // len(unit))
    data = {
        "project": {"project_ref": P, "display_name": "Alpha"},
        "messages": [{"message_ref": M, "text": text} for _ in range(12)],
    }
    fit(data, "messages")
    assert 1 <= len(data["messages"]) < 12
    assert text_codepoints(data) <= TEXT_CODEPOINTS_MAX
    assert len(jcs_dumps(data)) <= DATA_BYTES_MAX


def test_the_page_budget_refuses_the_record_that_would_cross_either_cap():
    budget = PageBudget({"results": []})
    taken = 0
    while budget.take({"text": "a" * 4000}):
        taken += 1
    assert taken == 8  # exactly 32,000 codepoints is allowed; the ninth record crosses it
