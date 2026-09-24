"""Search coverage proof (design §3.3; frozen spec §23D)."""

import pytest

from comms.transports.telegram.disclosure.coverage import (
    PARTIAL_REASONS,
    CoverageError,
    build_coverage,
    coverage_digest,
    validate_coverage,
)

_A, _B = "tpr_" + "a" * 26, "tpr_" + "b" * 26


def _complete():
    return build_coverage(
        complete=True,
        eligible_peers=3,
        peers_scanned=3,
        telegram_rpcs=5,
        hits_examined=40,
        hits_returned=7,
        partial_reasons=[],
        project_coverage=[
            {"project_ref": _A, "eligible_peers": 2, "peers_scanned": 2},
            {"project_ref": _B, "eligible_peers": 2, "peers_scanned": 2},
        ],
    )


def test_partial_reasons_is_the_closed_six():
    assert PARTIAL_REASONS == (
        "deadline",
        "rpc_budget",
        "hit_budget",
        "peer_budget",
        "response_limit",
        "telegram_partial",
    )


def test_complete_requires_no_cursor_no_partial_no_reasons():
    validate_coverage(_complete(), next_cursor=None, partial=False)


def test_complete_with_a_cursor_is_refused():
    with pytest.raises(CoverageError):
        validate_coverage(_complete(), next_cursor="tgc_" + "a" * 26, partial=False)


def test_complete_with_partial_true_is_refused():
    with pytest.raises(CoverageError):
        validate_coverage(_complete(), next_cursor=None, partial=True)


def test_a_cursor_requires_response_limit_in_reasons():
    bounded = build_coverage(
        complete=False,
        eligible_peers=3,
        peers_scanned=1,
        telegram_rpcs=2,
        hits_examined=10,
        hits_returned=5,
        partial_reasons=["deadline"],
        project_coverage=[{"project_ref": _A, "eligible_peers": 2, "peers_scanned": 1}],
    )
    with pytest.raises(CoverageError):
        validate_coverage(bounded, next_cursor="tgc_" + "a" * 26, partial=True)


def test_unknown_partial_reason_is_refused():
    with pytest.raises(CoverageError):
        build_coverage(
            complete=False,
            eligible_peers=1,
            peers_scanned=0,
            telegram_rpcs=0,
            hits_examined=0,
            hits_returned=0,
            partial_reasons=["ran_out_of_patience"],
            project_coverage=[{"project_ref": _A, "eligible_peers": 1, "peers_scanned": 0}],
        )


def test_coverage_dedups_globally_while_projects_may_double_count():
    # One canonical peer shared by both projects: global totals count it
    # once, each project counts it locally. This is the OPPOSITE of the
    # exposure-accounting rule, and the two must never be swapped.
    coverage = build_coverage(
        complete=True,
        eligible_peers=1,
        peers_scanned=1,
        telegram_rpcs=1,
        hits_examined=2,
        hits_returned=2,
        partial_reasons=[],
        project_coverage=[
            {"project_ref": _A, "eligible_peers": 1, "peers_scanned": 1},
            {"project_ref": _B, "eligible_peers": 1, "peers_scanned": 1},
        ],
    )
    validate_coverage(coverage, next_cursor=None, partial=False)
    assert coverage["eligible_peers"] == 1
    assert sum(p["eligible_peers"] for p in coverage["project_coverage"]) == 2


def test_project_coverage_must_hold_one_to_eight_entries():
    with pytest.raises(CoverageError):
        build_coverage(
            complete=True,
            eligible_peers=0,
            peers_scanned=0,
            telegram_rpcs=0,
            hits_examined=0,
            hits_returned=0,
            partial_reasons=[],
            project_coverage=[],
        )


def test_digest_is_null_for_non_search_tools():
    assert coverage_digest(None) is None


def test_digest_is_lowercase_hex_and_order_stable():
    digest = coverage_digest(_complete())
    assert len(digest) == 64 and digest == digest.lower()
    assert digest == coverage_digest(_complete())
