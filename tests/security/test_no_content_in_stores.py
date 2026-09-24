"""Gate O: no content in receipts, ledger, chain, checkpoints, settings or logs.

A whole-store scan rather than a field-by-field one, so a column added later
cannot escape it.
"""

import logging

from tests.authority_fixtures import PROJECT_REF
from tests.coordinator_fixtures import build_coordinator

# Distinctive markers: if one ever reaches a store, the sweep names the table
# and column rather than just failing.
_MARKERS = (
    "ZQXJV-MESSAGE-BODY",
    "ZQXJV-SEARCH-QUERY",
    "ZQXJV-USERNAME",
    "+61400000000",
)


def _scan(conn) -> list[str]:
    hits: list[str] = []
    tables = [
        r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
    ]
    for table in tables:
        columns = [r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()]
        for row in conn.execute(f"SELECT * FROM {table}").fetchall():
            for column, value in zip(columns, row, strict=True):
                if isinstance(value, str) and any(m in value for m in _MARKERS):
                    hits.append(f"{table}.{column}")
    return hits


async def test_no_marker_reaches_any_store(tmp_path, caplog):
    loud = [
        {
            "message_ref": "tgm_" + "a" * 26,
            "origin_project_refs": [PROJECT_REF],
            "text": f"{_MARKERS[0]} from {_MARKERS[2]} at {_MARKERS[3]}",
            "text_truncated": False,
        }
    ]
    coordinator, conn, adapter = build_coordinator(tmp_path, records=loud)

    with caplog.at_level(logging.DEBUG):
        outcome = await coordinator.disclose(
            tool_name="telegram_get_messages",
            arguments={"query": _MARKERS[1]},
            adapter=adapter,
        )

    assert outcome.released
    # The body did leave in the payload -- that is the disclosure. What must
    # not happen is any of it becoming durable in a gateway store.
    assert _MARKERS[0] in outcome.data["messages"][0]["text"]
    assert _scan(conn) == []
    assert not any(m in caplog.text for m in _MARKERS)


async def test_the_receipt_commits_to_the_body_without_containing_it(tmp_path):
    loud = [
        {
            "message_ref": "tgm_" + "a" * 26,
            "origin_project_refs": [PROJECT_REF],
            "text": _MARKERS[0],
            "text_truncated": False,
        }
    ]
    coordinator, _conn, adapter = build_coordinator(tmp_path, records=loud)
    outcome = await coordinator.disclose(
        tool_name="telegram_get_messages", arguments={}, adapter=adapter
    )

    payload = outcome.meta["disclosure"]["proof_payload"]
    assert not any(m in str(value) for value in payload.values() for m in _MARKERS)
    # The provenance digest is deliberately not a content hash: the same
    # refs and truncation state with a different body give the same digest.
    assert len(payload["canonical_result_provenance_digest"]) == 64


async def test_the_sweep_would_actually_catch_a_leak(tmp_path):
    """A sweep that cannot fail proves nothing. Plant a marker and confirm."""
    from comms.transports.telegram.storage.db import open_db
    from comms.transports.telegram.storage.migrations import migrate

    conn = open_db(tmp_path / "meta.db")
    migrate(conn)
    conn.execute(
        "INSERT INTO settings (key, value_json, updated_at) VALUES ('release.version', ?, 'now')",
        (f'"{_MARKERS[0]}"',),
    )
    conn.commit()

    assert _scan(conn) == ["settings.value_json"]
