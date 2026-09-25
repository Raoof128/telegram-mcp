"""comms v0.3 Task D1b: durable opaque refs for provider objects (G13; design D.4)."""

from datetime import timedelta

import pytest
import sqlcipher3

from comms.core import refs
from comms.core.campaigns import directory as d
from comms.core.errors import CommsError
from comms.core.objects import KIND_PREFIX, object_ref, resolve_object
from comms.core.storage.db import open_comms_db, write_tx
from tests.core import schema_fixtures as fx
from tests.core.campaign_helpers import NOW


@pytest.fixture
def world(tmp_path):
    conn = fx.migrated(tmp_path)
    loc = d.add_location(conn, "MQ", now=NOW)
    dst = d.add_destination(conn, loc, "telegram", "group:55", "g", normalize=fx.tg, now=NOW)
    destination_id = conn.execute("SELECT id FROM destinations WHERE ref = ?", (dst,)).fetchone()[0]
    return {"conn": conn, "path": tmp_path / "comms.db", "destination_id": destination_id}


def test_same_provider_object_same_ref_across_restarts(world):
    conn = world["conn"]
    first = object_ref(
        conn, "message", "telegram", "telegram_bot", world["destination_id"], "-55:4711", now=NOW
    )
    conn.close()
    reopened = open_comms_db(world["path"], fx.KEY)
    later = NOW + timedelta(days=1)
    assert (
        object_ref(
            reopened,
            "message",
            "telegram",
            "telegram_bot",
            world["destination_id"],
            "-55:4711",
            now=later,
        )
        == first
    )
    assert (
        reopened.execute("SELECT last_seen_at FROM provider_objects")
        .fetchone()[0]
        .startswith("2026-09-25")
    )
    other = object_ref(
        reopened,
        "message",
        "telegram",
        "telegram_user",
        world["destination_id"],
        "-55:4711",
        now=later,
    )
    assert other != first  # the same provider id under another actor is another object


@pytest.mark.parametrize(
    "kind,transport,destination,identity",
    [
        ("message", "telegram", True, "-55:4711"),
        ("invite", "telegram", True, "https://t.me/+AbC"),
        ("topic", "telegram", True, "31"),
        ("template", "whatsapp", False, "1000"),
        ("media", "whatsapp", False, "2000"),
    ],
)
def test_ref_resolves_after_restart_for_reply_edit_delete(
    world, kind, transport, destination, identity
):
    destination_id = world["destination_id"] if destination else None
    ref = object_ref(world["conn"], kind, transport, "actor_x", destination_id, identity, now=NOW)
    refs.check(ref, kind)
    assert ref.startswith(KIND_PREFIX[kind])
    world["conn"].close()
    found = resolve_object(open_comms_db(world["path"], fx.KEY), ref, kind)
    assert (
        found.kind,
        found.transport,
        found.actor,
        found.destination_id,
        found.provider_identity,
    ) == (
        kind,
        transport,
        "actor_x",
        destination_id,
        identity,
    )


def test_objects_without_a_destination_are_still_unique(world):
    conn = world["conn"]
    first = object_ref(conn, "template", "whatsapp", "whatsapp_cloud", None, "1000", now=NOW)
    assert (
        object_ref(conn, "template", "whatsapp", "whatsapp_cloud", None, "1000", now=NOW) == first
    )
    assert conn.execute("SELECT count(*) FROM provider_objects").fetchone()[0] == 1


def test_wrong_kind_refused(world):
    ref = object_ref(
        world["conn"],
        "message",
        "telegram",
        "telegram_bot",
        world["destination_id"],
        "-55:1",
        now=NOW,
    )
    with pytest.raises(CommsError) as refused:
        resolve_object(world["conn"], ref, "invite")
    assert refused.value.code == "INVALID_ARGUMENT"
    for missing in ("cmg_" + "a" * 26, "not-a-ref", ""):
        with pytest.raises(CommsError) as refused:
            resolve_object(world["conn"], missing, "message")
        assert refused.value.code in ("NOT_FOUND", "INVALID_ARGUMENT")


def test_provider_identity_never_leaves_except_identity_inspect(world):
    ref = object_ref(
        world["conn"],
        "message",
        "telegram",
        "telegram_bot",
        world["destination_id"],
        "-55:4711",
        now=NOW,
    )
    found = resolve_object(world["conn"], ref, "message")
    assert "-55:4711" not in repr(found) and "4711" not in ref
    with pytest.raises(CommsError) as refused:
        resolve_object(world["conn"], "cmg_" + "b" * 26, "message")
    assert "4711" not in str(refused.value)


def test_a_binding_never_changes(world):
    object_ref(
        world["conn"],
        "message",
        "telegram",
        "telegram_bot",
        world["destination_id"],
        "-55:1",
        now=NOW,
    )
    with pytest.raises(sqlcipher3.IntegrityError), write_tx(world["conn"]):
        world["conn"].execute("UPDATE provider_objects SET provider_identity = '-55:2'")


def test_unknown_kinds_and_transports_are_refused(world):
    for kind, transport in (("chat", "telegram"), ("message", "signal")):
        with pytest.raises(CommsError):
            object_ref(world["conn"], kind, transport, "a", None, "x", now=NOW)
