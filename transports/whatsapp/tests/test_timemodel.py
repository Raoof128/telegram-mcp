import datetime as dt

import pytest

from whatsvault import timemodel as tm


def test_provider_seconds_become_one_second_interval():
    iv = tm.from_provider_seconds(1603059201)
    assert (iv.ts_lower_ms, iv.ts_upper_ms_exclusive, iv.ts_precision) == (1603059201000, 1603059202000, "s")


def test_minute_interval_is_sixty_seconds_wide():
    iv = tm.from_local_minute(1603059180000)
    assert iv.ts_upper_ms_exclusive - iv.ts_lower_ms == 60000 and iv.ts_precision == "min"


def test_interval_rejects_inverted_bounds():
    with pytest.raises(ValueError):
        tm.Interval(100, 100, "s")


def test_interval_rejects_bad_precision():
    with pytest.raises(ValueError):
        tm.Interval(0, 1000, "weeks")


def test_definitely_before_requires_no_overlap():
    a, b = tm.from_provider_seconds(100), tm.from_provider_seconds(200)
    assert tm.definitely_before(a, b) is True and tm.definitely_before(b, a) is False


def test_overlapping_intervals_are_not_definitely_ordered():
    second = tm.from_provider_seconds(1000)
    minute = tm.from_local_minute(1000000 - (1000000 % 60000))
    assert tm.temporal_overlap(second, minute) is True
    assert tm.definitely_before(second, minute) is False
    assert tm.definitely_before(minute, second) is False


def test_classify_local_detects_nonexistent_spring_forward():
    assert tm.classify_local("America/New_York", dt.datetime(2026, 3, 8, 2, 30)) == tm.DstClass.NONEXISTENT


def test_classify_local_detects_fold_fall_back():
    assert tm.classify_local("America/New_York", dt.datetime(2026, 11, 1, 1, 30)) == tm.DstClass.FOLD


def test_classify_local_ordinary_time_is_unambiguous():
    assert tm.classify_local("America/New_York", dt.datetime(2026, 6, 1, 12, 0)) == tm.DstClass.UNAMBIGUOUS
