"""comms 5b-4 §1: the core prefix registry and the UTC rules (R16)."""

from datetime import UTC, datetime, timedelta, timezone

import pytest

from comms.core import refs, timeutil


def test_core_prefixes_are_disjoint_from_telegram_and_whatsvault():
    from whatsvault.ids import PREFIXES

    from comms.transports.telegram.authority.refs import REF_PREFIXES

    others = set(REF_PREFIXES) | {"tgu_"} | {p + "_" for p in PREFIXES}
    assert not set(refs.CORE_PREFIXES.values()) & others


def test_core_prefixes_are_unique():
    values = list(refs.CORE_PREFIXES.values())
    assert (
        len(values) == len(set(values)) == 25
    )  # 10 from 5b-4 + 12 (Task A1b) + aev_/ack_ (Task A4, R-002)


def test_mint_check_and_kind_of_round_trip_and_refuse_wrong_kind():
    for kind in refs.CORE_PREFIXES:
        ref = refs.mint(kind)
        assert refs.check(ref, kind) == ref
        assert refs.kind_of(ref) == kind
    job = refs.mint("job")
    for bad in (
        lambda: refs.check(job, "campaign"),
        lambda: refs.check("djb_short", "job"),
        lambda: refs.kind_of("tpr_" + "a" * 26),
        lambda: refs.mint("nope"),
        lambda: refs.check(12345, "job"),
    ):
        with pytest.raises(ValueError, match="unexpected ref"):
            bad()


def test_naive_datetimes_are_refused():
    with pytest.raises(ValueError):
        timeutil.utc(datetime(2026, 10, 1, 8))  # noqa: DTZ001 -- the naive input under test
    with pytest.raises(ValueError):
        timeutil.iso(datetime(2026, 10, 1, 8))  # noqa: DTZ001 -- the naive input under test


def test_offsets_normalize_to_utc():
    sydney = datetime(2026, 10, 1, 18, tzinfo=timezone(timedelta(hours=10)))
    assert timeutil.iso(sydney) == "2026-10-01T08:00:00.000000Z"
    assert timeutil.utc(sydney).tzinfo is UTC


def test_iso_parse_round_trip():
    now = datetime(2026, 9, 24, 5, 6, 7, 891011, tzinfo=UTC)
    assert timeutil.parse(timeutil.iso(now)) == now


@pytest.mark.parametrize(
    "text",
    [
        "2026-10-01T08:00:00Z",
        "2026-10-01T08:00:00.000000+00:00",
        "2026-10-01 08:00:00.000000Z",
        "2026-10-01T08:00:00.000000z",
        " 2026-10-01T08:00:00.000000Z",
        20261001,
    ],
)
def test_parse_refuses_non_canonical_text(text):
    with pytest.raises(ValueError):
        timeutil.parse(text)
