"""No argument, retry or project cycle beats a hard ceiling (Gate P, design §5.6)."""

import pytest

from comms.transports.telegram.disclosure.budget import (
    GLOBAL,
    PROJECT,
    BucketKey,
    BudgetError,
    BudgetLedger,
    Usage,
    subject_digest,
)
from comms.transports.telegram.keys.store import provision_missing
from comms.transports.telegram.storage.db import open_db
from comms.transports.telegram.storage.migrations import migrate


def _ledger(tmp_path):
    provision_missing(tmp_path / "keys", phases=(2,))
    conn = open_db(tmp_path / "meta.db")
    migrate(conn)
    return BudgetLedger(conn)


def test_retrying_does_not_accumulate_past_the_ceiling(tmp_path):
    ledger = _ledger(tmp_path)
    key = BucketKey(1, GLOBAL, subject_digest(GLOBAL))

    for i in range(1):  # 1 x 800 reserved, 800 live
        ledger.reserve(
            client_id=1,
            security_epoch=1,
            project_scope_digest="d",
            binding_digest="c",
            request_nonce=f"n{i}",
            worst_case={key: Usage(800, 0)},
            ttl_seconds=600,
        )
    # A client that simply asks again is refused: live reservations count.
    with pytest.raises(BudgetError):
        ledger.reserve(
            client_id=1,
            security_epoch=1,
            project_scope_digest="d",
            binding_digest="c",
            request_nonce="retry",
            worst_case={key: Usage(800, 0)},
            ttl_seconds=600,
        )


def test_cycling_projects_still_hits_the_client_global_ceiling(tmp_path):
    ledger = _ledger(tmp_path)
    glob = BucketKey(1, GLOBAL, subject_digest(GLOBAL))

    # Each call names a different project, so every per-project bucket stays
    # under its own 500-record ceiling. The global bucket does not care:
    # 3 x 400 = 1200 passes, and the fourth call reaches 1600 >= 1500.
    for i in range(3):
        project = BucketKey(1, PROJECT, subject_digest(PROJECT, f"tpr_{chr(97 + i) * 26}"))
        ledger.reserve(
            client_id=1,
            security_epoch=1,
            project_scope_digest="d",
            binding_digest="c",
            request_nonce=f"n{i}",
            worst_case={glob: Usage(400, 0), project: Usage(400, 0)},
            ttl_seconds=600,
        )

    project = BucketKey(1, PROJECT, subject_digest(PROJECT, "tpr_" + "z" * 26))
    with pytest.raises(BudgetError):
        ledger.reserve(
            client_id=1,
            security_epoch=1,
            project_scope_digest="d",
            binding_digest="c",
            request_nonce="n3",
            worst_case={glob: Usage(700, 0), project: Usage(700, 0)},
            ttl_seconds=600,
        )


def test_a_different_client_has_its_own_ceiling(tmp_path):
    ledger = _ledger(tmp_path)
    first = BucketKey(1, GLOBAL, subject_digest(GLOBAL))
    second = BucketKey(2, GLOBAL, subject_digest(GLOBAL))

    ledger.reserve(
        client_id=1,
        security_epoch=1,
        project_scope_digest="d",
        binding_digest="c",
        request_nonce="n1",
        worst_case={first: Usage(1_400, 0)},
        ttl_seconds=600,
    )
    # Budgets are per authenticated client; client 2 is unaffected.
    ledger.reserve(
        client_id=2,
        security_epoch=1,
        project_scope_digest="d",
        binding_digest="c",
        request_nonce="n2",
        worst_case={second: Usage(1_400, 0)},
        ttl_seconds=600,
    )
