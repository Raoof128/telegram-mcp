"""comms v0.3 Task D16: location and audience services (P §31, G16)."""

import pytest

from comms.core import refs
from comms.core.errors import CommsError
from comms.services.directory import DirectoryService
from comms.services.mutations import MutationExecutor
from tests.core.audit.legacy_fixtures import comms_world
from tests.core.campaign_helpers import person
from tests.services.group_fixtures import CTX

PHONES = ("+61400000001", "+61400000002")


@pytest.fixture
def world(tmp_path):
    w = comms_world(tmp_path)
    w["service"] = DirectoryService(w["writer"], MutationExecutor(w["writer"], {}))
    w["rcps"] = [person(w["conn"], phone=p)[0] for p in PHONES]
    return w


def _req():
    return refs.mint("request")


def _mutations(world):
    return world["conn"].execute("SELECT count(*) FROM mutations").fetchone()[0]


def test_location_create_get_and_list(world):
    s = world["service"]
    loc = s.location_create(CTX, "Parramatta", _req())["location"]
    assert loc.startswith("loc_")
    got = s.location_get(loc)
    assert got["name"] == "Parramatta" and got["enabled"] is True and got["members"] == 0
    assert loc in [i["location"] for i in s.location_list()["items"]]


def test_location_create_replay_creates_one_location(world):
    request = _req()
    a = world["service"].location_create(CTX, "Parramatta", request)
    b = world["service"].location_create(CTX, "Parramatta", request)
    assert a["location"] == b["location"] and b["replayed"]
    assert world["conn"].execute("SELECT count(*) FROM locations").fetchone()[0] == 1


def test_location_update_renames_and_keeps_the_ref(world):
    s = world["service"]
    loc = s.location_create(CTX, "Parramata", _req())["location"]
    s.location_update(CTX, loc, "Parramatta", _req())
    assert s.location_get(loc)["name"] == "Parramatta"


def test_location_enable_and_disable(world):
    s = world["service"]
    loc = s.location_create(CTX, "Parramatta", _req())["location"]
    s.location_disable(CTX, loc, _req())
    assert s.location_get(loc)["enabled"] is False
    s.location_enable(CTX, loc, _req())
    assert s.location_get(loc)["enabled"] is True


def test_audience_create_update_add_remove_get_list(world):
    s = world["service"]
    aud = s.audience_create(CTX, "Families", _req())["audience"]
    s.audience_update(CTX, aud, "All families", _req())
    for rcp in world["rcps"]:
        s.audience_add(CTX, aud, rcp, _req())
    got = s.audience_get(aud)
    assert got["name"] == "All families" and got["members"]["recipient"] == 2
    s.audience_remove(CTX, aud, world["rcps"][0], _req())
    assert s.audience_get(aud)["members"]["recipient"] == 1
    assert aud in [i["audience"] for i in s.audience_list()["items"]]


def test_audience_resolve_returns_counts_and_refs(world):
    s = world["service"]
    aud = s.audience_create(CTX, "Families", _req())["audience"]
    for rcp in world["rcps"]:
        s.audience_add(CTX, aud, rcp, _req())
    resolved = s.audience_resolve(aud)
    assert resolved["count"] == 2 and resolved["by_transport"] == {"whatsapp": 2}
    assert all(r.startswith("rct_") for r in resolved["endpoints"])
    assert not any(p[1:] in repr(resolved) for p in PHONES)  # refs, never identities


def test_cycle_refused_via_service(world):
    s = world["service"]
    a = s.audience_create(CTX, "A", _req())["audience"]
    b = s.audience_create(CTX, "B", _req())["audience"]
    s.audience_add(CTX, a, b, _req())
    before = _mutations(world)
    with pytest.raises(CommsError) as refused:
        s.audience_add(CTX, b, a, _req())
    assert refused.value.code == "INVALID_ARGUMENT" and _mutations(world) == before


def test_removing_a_membership_that_does_not_exist_is_not_found(world):
    s = world["service"]
    aud = s.audience_create(CTX, "A", _req())["audience"]
    with pytest.raises(CommsError) as refused:
        s.audience_remove(CTX, aud, world["rcps"][0], _req())
    assert refused.value.code == "NOT_FOUND"


@pytest.mark.parametrize(
    "call",
    [
        lambda s: s.location_get("loc_" + "z" * 26),
        lambda s: s.audience_get("cau_" + "z" * 26),
        lambda s: s.audience_resolve("cau_" + "z" * 26),
    ],
)
def test_unknown_refs_are_not_found(world, call):
    with pytest.raises(CommsError) as refused:
        call(world["service"])
    assert refused.value.code == "NOT_FOUND"


@pytest.mark.parametrize("name", ["", "   ", None, 7])
def test_a_name_is_required(world, name):
    with pytest.raises(CommsError) as refused:
        world["service"].location_create(CTX, name, _req())
    assert refused.value.code == "INVALID_ARGUMENT" and _mutations(world) == 0


def test_every_write_is_recorded_with_its_tool(world):
    s = world["service"]
    loc = s.location_create(CTX, "L", _req())["location"]
    s.location_update(CTX, loc, "L2", _req())
    s.location_disable(CTX, loc, _req())
    s.location_enable(CTX, loc, _req())
    aud = s.audience_create(CTX, "A", _req())["audience"]
    s.audience_update(CTX, aud, "A2", _req())
    s.audience_add(CTX, aud, loc, _req())
    s.audience_remove(CTX, aud, loc, _req())
    tools = [r[0] for r in world["conn"].execute("SELECT tool FROM mutations ORDER BY id")]
    assert tools == [
        "comms_location_create",
        "comms_location_update",
        "comms_location_disable",
        "comms_location_enable",
        "comms_audience_create",
        "comms_audience_update",
        "comms_audience_add",
        "comms_audience_remove",
    ]
