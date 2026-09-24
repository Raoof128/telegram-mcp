"""Egress transformation (design §3.1; frozen spec §23B.2)."""

import pytest

from comms.transports.telegram.disclosure.egress import (
    effective_egress_level,
    intersect_profiles,
    transform_record,
)

# Codepoints, not bytes: an RTL string plus an astral emoji.
_RTL = "مرحبا \U0001f600 hello world"


def test_most_restrictive_profile_and_smallest_excerpt_win():
    assert intersect_profiles([("full_text", None), ("excerpt", 200)]) == ("excerpt", 200)
    assert intersect_profiles([("excerpt", 200), ("excerpt", 64)]) == ("excerpt", 64)
    assert intersect_profiles([("excerpt", 64), ("metadata_only", None)]) == ("metadata_only", None)


def test_metadata_only_drops_text_and_never_flags_truncation():
    out = transform_record({"message_ref": "tgm_" + "a" * 26, "text": _RTL}, "metadata_only", None)
    assert out["text"] is None
    assert out["text_truncated"] is False


def test_excerpt_truncates_by_codepoints_and_flags_it():
    out = transform_record({"message_ref": "tgm_" + "a" * 26, "text": _RTL}, "excerpt", 8)
    assert out["text"] == _RTL[:8]
    assert len(out["text"]) == 8
    assert out["text_truncated"] is True


def test_excerpt_shorter_than_limit_is_not_truncated():
    out = transform_record({"message_ref": "tgm_" + "a" * 26, "text": "hi"}, "excerpt", 64)
    assert out["text"] == "hi"
    assert out["text_truncated"] is False


def test_transformation_never_sanitises_message_text():
    # Bidi and C0 controls survive verbatim: sanitising a body would corrupt
    # the evidence the model reads (design §3.1).
    # Built with chr() rather than a literal: ruff's PLE2502 trojan-source
    # rule rejects a bidi control in source, and it is right to -- the point
    # here is that the transformer passes the character through, not that
    # this file contains one.
    hostile = "a" + chr(0x202E) + "b" + chr(0x07) + "c"
    out = transform_record({"message_ref": "tgm_" + "a" * 26, "text": hostile}, "full_text", None)
    assert out["text"] == hostile


def test_transformation_is_deterministic():
    record = {"message_ref": "tgm_" + "a" * 26, "text": _RTL}
    assert transform_record(record, "excerpt", 5) == transform_record(record, "excerpt", 5)


def test_effective_level_is_what_is_present_not_what_was_permitted():
    # Every record came back metadata-only under a full_text grant.
    records = [{"text": None, "text_truncated": False}, {"text": None, "text_truncated": False}]
    assert effective_egress_level(records) == "metadata_only"
    assert effective_egress_level([{"text": "x", "text_truncated": True}]) == "excerpt"
    assert effective_egress_level([{"text": "x", "text_truncated": False}]) == "full_text"
    assert effective_egress_level([]) == "metadata_only"


def test_unknown_level_is_rejected():
    with pytest.raises(ValueError):
        transform_record({"text": "x"}, "everything", None)
