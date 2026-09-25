"""comms v0.3 Task C5: every provider fixture carries provenance pinned by hash (design §C.8)."""

import hashlib
import json

import pytest

from tests.conformance.provenance import FIXTURES, check

ENTRY = {
    "provider": "telegram_bot_api",
    "api_version": "Bot API 9.2",
    "source": "https://core.telegram.org/bots/api#sendmessage",
    "captured": "2026-09-25",
}


def _tree(tmp_path, entries, files):
    for name, body in files.items():
        (tmp_path / name).write_bytes(body)
    (tmp_path / "PROVENANCE.json").write_text(json.dumps({"fixtures": entries}))
    return tmp_path


def _entry(body):
    return {**ENTRY, "sha256": hashlib.sha256(body).hexdigest()}


def test_every_provider_fixture_has_provenance():
    assert check(FIXTURES) == []


def test_a_clean_tree_passes(tmp_path):
    assert check(_tree(tmp_path, {"a.json": _entry(b"{}")}, {"a.json": b"{}"})) == []


@pytest.mark.parametrize(
    "entries,files,problem",
    [
        ({}, {"a.json": b"{}"}, "a.json: no provenance entry"),
        ({"a.json": _entry(b"{}")}, {}, "a.json: entry without a file"),
        ({"a.json": _entry(b"{}")}, {"a.json": b"[]"}, "a.json: sha256 mismatch"),
        (
            {"a.json": {k: v for k, v in _entry(b"{}").items() if k != "source"}},
            {"a.json": b"{}"},
            "a.json: fields",
        ),
        (
            {"a.json": {**_entry(b"{}"), "captured": "25/09/2026"}},
            {"a.json": b"{}"},
            "a.json: captured",
        ),
    ],
)
def test_each_defect_is_reported(tmp_path, entries, files, problem):
    assert check(_tree(tmp_path, entries, files)) == [problem]


def test_nested_fixtures_are_covered(tmp_path):
    (tmp_path / "meta").mkdir()
    (tmp_path / "meta" / "send.json").write_bytes(b"{}")
    root = _tree(tmp_path, {}, {})
    assert check(root) == ["meta/send.json: no provenance entry"]
