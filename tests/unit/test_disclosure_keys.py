"""verification_keys registry and historical export (design §4.2, §8)."""

import pytest

from comms.transports.telegram.disclosure.keys import (
    current_verification_key,
    export_verification_keys,
    lookup_verification_key,
    publish_verification_key,
    retire_verification_key,
)
from comms.transports.telegram.storage.db import open_db
from comms.transports.telegram.storage.migrations import migrate

_OLD = "ed25519:sha256:" + "a" * 64
_NEW = "ed25519:sha256:" + "b" * 64


@pytest.fixture
def conn(tmp_path):
    connection = open_db(tmp_path / "meta.db")
    migrate(connection)
    return connection


def _publish(conn, key_id, activated_at):
    publish_verification_key(
        conn,
        key_id=key_id,
        purpose="disclosure_proof",
        algorithm="Ed25519",
        public_key_b64url="A" * 43,
        activated_at=activated_at,
    )


def test_current_key_is_the_unretired_one(conn):
    _publish(conn, _OLD, "2026-01-01T00:00:00Z")
    retire_verification_key(conn, key_id=_OLD, retired_at="2026-06-01T00:00:00Z")
    _publish(conn, _NEW, "2026-06-01T00:00:00Z")

    assert current_verification_key(conn, "disclosure_proof")["key_id"] == _NEW


def test_a_retired_key_stays_exportable_for_historical_receipts(conn):
    _publish(conn, _OLD, "2026-01-01T00:00:00Z")
    retire_verification_key(conn, key_id=_OLD, retired_at="2026-06-01T00:00:00Z")

    row = lookup_verification_key(conn, _OLD)
    assert row["retired_at"] == "2026-06-01T00:00:00Z"
    assert row["public_key_b64url"] == "A" * 43


def test_export_returns_current_and_historical(conn):
    _publish(conn, _OLD, "2026-01-01T00:00:00Z")
    retire_verification_key(conn, key_id=_OLD, retired_at="2026-06-01T00:00:00Z")
    _publish(conn, _NEW, "2026-06-01T00:00:00Z")

    exported = export_verification_keys(conn)
    assert {row["key_id"] for row in exported} == {_OLD, _NEW}
    assert [row["key_id"] for row in export_verification_keys(conn, key_id=_OLD)] == [_OLD]


def test_export_never_emits_private_material(conn):
    _publish(conn, _OLD, "2026-01-01T00:00:00Z")
    for row in export_verification_keys(conn):
        assert set(row) == {
            "key_id",
            "purpose",
            "algorithm",
            "public_key_b64url",
            "activated_at",
            "retired_at",
        }


def test_unknown_purpose_is_refused(conn):
    with pytest.raises(ValueError):
        publish_verification_key(
            conn,
            key_id=_OLD,
            purpose="general_vibes",
            algorithm="Ed25519",
            public_key_b64url="A" * 43,
            activated_at="2026-01-01T00:00:00Z",
        )
