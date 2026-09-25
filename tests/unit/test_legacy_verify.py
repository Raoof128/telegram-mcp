"""comms v0.3 A3: historical verification stands alone — receipts, the legacy chain, key coverage."""

from comms.transports.telegram.legacy_verify import (
    missing_verification_keys,
    verify_legacy_chain,
    verify_receipt_v1,
    verify_receipt_v2,
)
from comms.transports.telegram.storage.migrations import migrate
from tests.unit.test_receipt_migration import CHAIN, v1_world  # noqa: F401 -- pytest fixture
from tests.unit.test_receipts_v2 import _insert_v2, world  # noqa: F401 -- pytest fixture


def test_v1_receipts_verify_only_as_v1(v1_world):  # noqa: F811 -- the imported fixture
    conn, refs = v1_world
    migrate(conn)
    assert all(verify_receipt_v1(conn, r) for r in refs)
    assert not any(verify_receipt_v2(conn, r) for r in refs)


def test_v2_receipts_verify_only_as_v2(world):  # noqa: F811 -- the imported fixture
    conn, seed, kid = world
    ref = _insert_v2(conn, seed, kid, soft=False)
    assert verify_receipt_v2(conn, ref) and not verify_receipt_v1(conn, ref)
    assert not verify_receipt_v2(conn, "tdr_" + "z" * 26)


def test_the_legacy_chain_verifies_and_a_tamper_fails(v1_world):  # noqa: F811
    conn, _refs = v1_world
    migrate(conn)
    assert verify_legacy_chain(conn, CHAIN)
    assert not verify_legacy_chain(conn, b"x" * 32)


def test_historical_key_coverage_is_complete(v1_world):  # noqa: F811
    conn, _refs = v1_world
    migrate(conn)
    assert missing_verification_keys(conn) == set()
    (kid,) = conn.execute("SELECT DISTINCT proof_key_id FROM disclosure_receipts").fetchone()
    conn.execute("PRAGMA foreign_keys = OFF")
    conn.execute("DELETE FROM verification_keys WHERE key_id = ?", (kid,))
    assert missing_verification_keys(conn) == {kid}
