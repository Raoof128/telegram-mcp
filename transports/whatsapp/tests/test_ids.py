import re

import pytest

from whatsvault import ids


def test_registry_covers_every_schema_entity():
    for p in (
        "acc",
        "cnt",
        "cnv",
        "src",
        "msg",
        "rev",
        "att",
        "evt",
        "drf",
        "apv",
        "atm",
        "cap",
        "dev",
        "bat",
        "aud",
    ):
        assert p in ids.PREFIXES


def test_new_id_has_prefix_and_26_char_ulid():
    v = ids.new_id("msg")
    assert v.startswith("msg_")
    body = v[len("msg_") :]
    assert re.fullmatch(r"[0-9A-HJKMNP-TV-Z]{26}", body), body


def test_new_id_rejects_unknown_prefix():
    with pytest.raises(ids.IdError):
        ids.new_id("zzz")


def test_ids_are_unique():
    made = [ids.new_id("evt") for _ in range(50)]
    assert len(set(made)) == 50


def test_ids_are_orderable_without_error():
    made = [ids.new_id("evt") for _ in range(10)]
    ordered = sorted(made)
    assert len(ordered) == len(made)


def test_validate_accepts_good_and_rejects_wrong_prefix():
    good = ids.new_id("cnt")
    assert ids.validate("cnt", good) == good
    with pytest.raises(ids.IdError):
        ids.validate("msg", good)


def test_validate_rejects_malformed_body():
    with pytest.raises(ids.IdError):
        ids.validate("msg", "msg_not-a-ulid")
