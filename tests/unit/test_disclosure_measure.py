"""The single measurement authority (design §5.1, §5.2; Gate P)."""

import pytest

from telegram_mcp.disclosure.measure import (
    RECORD_ELEMENT,
    MeasurementError,
    bytes_disclosed,
    container_bytes,
    project_bytes,
    records_disclosed,
)

_CROSS = {
    "projects": [{"project_ref": "tpr_" + "a" * 26}, {"project_ref": "tpr_" + "b" * 26}],
    "search_scope": {"mode": "cross_project"},
    "results": [
        {"message_ref": "tgm_" + "a" * 26, "origin_project_refs": ["tpr_" + "a" * 26]},
        {
            "message_ref": "tgm_" + "b" * 26,
            "origin_project_refs": ["tpr_" + "a" * 26, "tpr_" + "b" * 26],
        },
    ],
}


def test_record_element_is_read_from_the_frozen_contracts():
    # resolve_* emit matches[], get_unread emits chats[]; guessing peers[]
    # or unread[] would count nothing at all.
    assert RECORD_ELEMENT["telegram_resolve_peer"] == "matches"
    assert RECORD_ELEMENT["telegram_resolve_project"] == "matches"
    assert RECORD_ELEMENT["telegram_get_unread"] == "chats"
    # cross-project search also carries a projects[] array that is scope
    # metadata, not records.
    assert RECORD_ELEMENT["telegram_cross_project_search"] == "results"
    assert "telegram_status" not in RECORD_ELEMENT


def test_records_disclosed_counts_the_record_element_only():
    assert records_disclosed("telegram_cross_project_search", _CROSS) == 2


def test_status_is_not_measurable():
    with pytest.raises(MeasurementError):
        records_disclosed("telegram_status", {"connected": False})


def test_bytes_disclosed_is_canonical_bytes_of_data():
    assert bytes_disclosed({"a": 1}) == len(b'{"a":1}')


def test_shared_record_is_charged_to_every_contributing_project():
    per = project_bytes("telegram_cross_project_search", _CROSS)
    a, b = "tpr_" + "a" * 26, "tpr_" + "b" * 26
    assert set(per) == {a, b}
    # The two-origin record is counted whole in BOTH buckets. Assert that
    # directly: the sum of project bytes exceeds the sum of record bytes.
    # Do NOT compare against bytes_disclosed -- the global figure also
    # carries container overhead (projects[], search_scope), so the
    # comparison can go either way and would fail on this very fixture.
    from telegram_mcp.consent.challenge import jcs_dumps

    record_bytes = sum(len(jcs_dumps(r)) for r in _CROSS["results"])
    assert sum(per.values()) > record_bytes
    assert per[a] > per[b]  # the shared record lands in a as well as b


def test_container_overhead_is_global_and_reconciles_for_one_project():
    data = {
        "project": {"project_ref": "tpr_" + "c" * 26},
        "messages": [
            {"message_ref": "tgm_" + "c" * 26, "origin_project_refs": ["tpr_" + "c" * 26]}
        ],
    }
    per = project_bytes("telegram_get_messages", data)
    assert sum(per.values()) + container_bytes("telegram_get_messages", data) == bytes_disclosed(
        data
    )
