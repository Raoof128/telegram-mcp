"""comms v0.3 Task D7: target resolution with ambiguity refusal (P §81)."""

import pytest

from comms.core.campaigns import directory as d
from comms.core.errors import CommsError
from comms.services.resolve import Resolution, resolve_group, resolve_message, resolve_person
from tests.core import schema_fixtures as fx
from tests.core.campaign_helpers import NOW


@pytest.fixture
def conn(tmp_path):
    return fx.migrated(tmp_path)


def _people(conn, *names):
    return [d.add_recipient(conn, now=NOW, display_name=name) for name in names]


def _group(conn, location, name, identity):
    loc = d.add_location(conn, location, now=NOW)
    return d.add_destination(conn, loc, "telegram", identity, name, normalize=fx.tg, now=NOW)


def test_one_match_resolves(conn):
    ali, sara = _people(conn, "Ali Rezaei", "Sara Karimi")
    assert resolve_person(conn, "  sara ") == Resolution(sara, ())
    assert resolve_person(conn, "Ali Rezaei").ref == ali


def test_two_people_named_ali_is_ambiguous(conn):
    first, second = _people(conn, "Ali Rezaei", "Ali Karimi")
    with pytest.raises(CommsError) as ambiguous:
        resolve_person(conn, "Ali")
    assert ambiguous.value.code == "AMBIGUOUS_TARGET"
    candidates = resolve_person(conn, "Ali", refuse=False).candidates
    assert {c.ref for c in candidates} == {first, second}
    assert {c.label for c in candidates} == {"Ali Rezaei", "Ali Karimi"}


def test_an_exact_name_does_not_silently_win(conn):
    _people(conn, "Ali", "Ali Karimi")
    with pytest.raises(CommsError) as ambiguous:
        resolve_person(conn, "ali")
    assert ambiguous.value.code == "AMBIGUOUS_TARGET"


def test_two_mq_groups_ambiguous(conn):
    _group(conn, "MQ", "MQ Persian Society", "group:55")
    _group(conn, "MQ Campus", "MQ Nowruz 2026", "channel:1234567890")
    with pytest.raises(CommsError) as ambiguous:
        resolve_group(conn, "mq", now=NOW)
    assert ambiguous.value.code == "AMBIGUOUS_TARGET"
    one = resolve_group(conn, "nowruz", now=NOW)
    assert one.ref.startswith("grp_")


def test_private_chats_are_not_groups(conn):
    _group(conn, "Home", "Ali DM", "user:42")
    with pytest.raises(CommsError) as missing:
        resolve_group(conn, "ali", now=NOW)
    assert missing.value.code == "NOT_FOUND"


def test_candidates_carry_refs_not_identities(conn):
    _people(conn, "Ali Rezaei", "Ali Karimi")
    first = d.add_recipient(conn, now=NOW, display_name="Ali Other")
    d.add_contact_point(conn, first, "whatsapp", "+61400000001", normalize=fx.wa, now=NOW)
    candidates = resolve_person(conn, "Ali", refuse=False).candidates
    assert all(c.ref.startswith("rcp_") for c in candidates)
    assert "61400000001" not in repr(candidates)
    _group(conn, "MQ", "MQ One", "group:55")
    _group(conn, "MQ", "MQ Two", "group:56")
    groups = resolve_group(conn, "mq", now=NOW, refuse=False).candidates
    assert all(c.ref.startswith("grp_") for c in groups) and "55" not in repr(groups)
    assert {c.label for c in groups} == {"MQ One · MQ", "MQ Two · MQ"}


def test_nothing_found_and_bad_queries(conn):
    _people(conn, "Ali")
    for query in ("zoe", "  "):
        with pytest.raises(CommsError) as refused:
            resolve_person(conn, query)
        assert refused.value.code in ("NOT_FOUND", "INVALID_ARGUMENT")


def test_disabled_people_are_not_candidates(conn):
    (ali,) = _people(conn, "Ali")
    d.set_enabled(conn, ali, False, now=NOW)
    with pytest.raises(CommsError) as refused:
        resolve_person(conn, "ali")
    assert refused.value.code == "NOT_FOUND"


def test_multiple_matching_messages_is_ambiguous():
    items = [
        {
            "message_ref": "cmg_" + "a" * 26,
            "sent_at": "2026-09-24T10:00:00Z",
            "untrusted": {"text": "Nowruz party"},
        },
        {
            "message_ref": "cmg_" + "b" * 26,
            "sent_at": "2026-09-24T11:00:00Z",
            "untrusted": {"text": "Nowruz again"},
        },
    ]
    with pytest.raises(CommsError) as ambiguous:
        resolve_message(items)
    assert ambiguous.value.code == "AMBIGUOUS_TARGET"
    assert resolve_message(items[:1]).ref == "cmg_" + "a" * 26
    labels = {c.label for c in resolve_message(items, refuse=False).candidates}
    assert labels == {"2026-09-24T10:00:00Z", "2026-09-24T11:00:00Z"}  # never the untrusted text
