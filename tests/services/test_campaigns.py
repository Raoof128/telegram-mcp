"""comms v0.3 Task D15: campaign services over the 5b-4 core (P §30, G16)."""

import os
from datetime import timedelta

import pytest

from comms.core import refs
from comms.core.delivery.commitment import commit_context
from comms.core.errors import CommsError
from comms.core.keys import rotate as rot
from comms.services.campaigns import CampaignService
from comms.services.mutations import MutationExecutor
from tests.core import fakes
from tests.core.audit.legacy_fixtures import comms_world
from tests.core.campaign_helpers import NOW, person
from tests.services.group_fixtures import CTX

PHONES = ("+61400000001", "+61400000002", "+61400000003")


@pytest.fixture
def world(tmp_path):
    w = comms_world(tmp_path)
    rot.rotate(
        w["writer"],
        w["store"],
        "campaign-commit-key",
        material=os.urandom(32),
        prove=lambda m: None,
        now=NOW,
    )
    w["rcps"] = [person(w["conn"], phone=p)[0] for p in PHONES]
    transports = {"whatsapp": fakes.FakeWhatsApp(conn=w["conn"])}
    w["service"] = CampaignService(
        w["writer"],
        MutationExecutor(w["writer"], {}),
        transports,
        commit=lambda: commit_context(w["writer"], w["store"]),
    )
    return w


def _req():
    return refs.mint("request")


def _ready(world):
    service = world["service"]
    cmp = service.create(CTX, "Nowruz", _req())["campaign"]
    service.set_content(CTX, cmp, {"canonical": "Happy Nowruz"}, _req())
    service.set_targets(CTX, cmp, {"recipients": world["rcps"]}, ["whatsapp"], _req())
    service.validate(CTX, cmp, _req())
    return cmp


def test_campaign_send_request_id_replay_returns_same_generation(world):
    cmp, request = _ready(world), _req()
    first = world["service"].send(CTX, cmp, request)
    again = world["service"].send(CTX, cmp, request)
    assert first["generation"].startswith("gen_") and again["generation"] == first["generation"]
    assert again["replayed"] and not first["replayed"]
    count = world["conn"].execute("SELECT count(*) FROM generations").fetchone()[0]
    assert count == 1  # the replay froze nothing new


def test_request_id_reuse_with_other_arguments_is_refused(world):
    request = _req()
    world["service"].create(CTX, "One", request)
    with pytest.raises(CommsError) as refused:
        world["service"].create(CTX, "Two", request)
    assert refused.value.code == "REQUEST_ID_REUSE"


def test_create_replay_creates_one_campaign(world):
    request = _req()
    a = world["service"].create(CTX, "Nowruz", request)
    b = world["service"].create(CTX, "Nowruz", request)
    assert a["campaign"] == b["campaign"]
    assert world["conn"].execute("SELECT count(*) FROM campaigns").fetchone()[0] == 1


def test_preview_carries_no_identity(world):
    cmp = _ready(world)
    preview = world["service"].preview(cmp)
    assert preview["recipients"] == 3 and preview["by_transport"] == {"whatsapp": 3}
    assert len(preview["target_digest"]) == 64
    text = repr(preview)
    assert not any(p in text or p[1:] in text for p in PHONES)
    assert "Happy Nowruz" not in text  # nor the body


def test_delivery_report_paginates(world):
    cmp = _ready(world)
    world["service"].send(CTX, cmp, _req())
    first = world["service"].delivery_report(cmp, limit=2)
    rest = world["service"].delivery_report(cmp, limit=2, cursor=first["next_cursor"])
    jobs = [j["job"] for j in first["items"] + rest["items"]]
    assert len(first["items"]) == 2 and len(rest["items"]) == 1 and rest["next_cursor"] is None
    assert len(set(jobs)) == 3 and all(j.startswith("djb_") for j in jobs)
    assert {j["state"] for j in first["items"] + rest["items"]} == {"PENDING"}
    assert not any(p[1:] in repr(first) + repr(rest) for p in PHONES)


def test_delivery_report_refuses_a_forged_cursor(world):
    cmp = _ready(world)
    world["service"].send(CTX, cmp, _req())
    with pytest.raises(CommsError) as refused:
        world["service"].delivery_report(cmp, limit=2, cursor="1; DROP TABLE")
    assert refused.value.code == "INVALID_ARGUMENT"


WRITES = {
    "create": lambda s, cmp, r: s.create(CTX, "T", r),
    "set_content": lambda s, cmp, r: s.set_content(CTX, cmp, {"canonical": "x"}, r),
    "set_targets": lambda s, cmp, r: s.set_targets(CTX, cmp, {"recipients": []}, ["whatsapp"], r),
    "validate": lambda s, cmp, r: s.validate(CTX, cmp, r),
    "schedule": lambda s, cmp, r: s.schedule(CTX, cmp, NOW + timedelta(days=1), r),
    "unschedule": lambda s, cmp, r: s.unschedule(CTX, cmp, r),
    "send": lambda s, cmp, r: s.send(CTX, cmp, r),
    "cancel": lambda s, cmp, r: s.cancel(CTX, cmp, r),
    "retry_failed": lambda s, cmp, r: s.retry_failed(CTX, cmp, r),
    "resolve_unknown": lambda s, cmp, r: s.resolve_unknown(CTX, "djb_" + "a" * 26, "sent", r),
}


@pytest.mark.parametrize("write", sorted(WRITES))
@pytest.mark.parametrize("request_id", [None, "", "req_short", 42])
def test_every_campaign_write_requires_request_id(world, write, request_id):
    cmp = world["service"].create(CTX, "T", _req())["campaign"]
    before = world["conn"].execute("SELECT count(*) FROM mutations").fetchone()[0]
    with pytest.raises(CommsError) as refused:
        WRITES[write](world["service"], cmp, request_id)
    assert refused.value.code == "INVALID_ARGUMENT"
    assert world["conn"].execute("SELECT count(*) FROM mutations").fetchone()[0] == before


def test_every_write_is_recorded_with_its_tool(world):
    cmp = _ready(world)
    world["service"].schedule(CTX, cmp, NOW + timedelta(days=1), _req())
    world["service"].unschedule(CTX, cmp, _req())
    world["service"].send(CTX, cmp, _req())
    world["service"].cancel(CTX, cmp, _req())
    tools = [r[0] for r in world["conn"].execute("SELECT tool FROM mutations ORDER BY id")]
    assert tools == [
        "comms_campaign_create",
        "comms_campaign_set_content",
        "comms_campaign_set_targets",
        "comms_campaign_validate",
        "comms_campaign_schedule",
        "comms_campaign_unschedule",
        "comms_campaign_send",
        "comms_campaign_cancel",
    ]


def test_a_refused_write_leaves_no_record_and_no_effect(world):
    cmp = world["service"].create(CTX, "T", _req())["campaign"]
    before = world["conn"].execute("SELECT count(*) FROM mutations").fetchone()[0]
    with pytest.raises(CommsError) as refused:
        world["service"].send(CTX, cmp, _req())  # a draft cannot be sent
    assert refused.value.code == "INVALID_ARGUMENT"
    assert world["conn"].execute("SELECT count(*) FROM mutations").fetchone()[0] == before
    assert world["conn"].execute("SELECT count(*) FROM generations").fetchone()[0] == 0


def test_status_get_and_list_are_reads(world):
    cmp = _ready(world)
    before = world["conn"].execute("SELECT count(*) FROM mutations").fetchone()[0]
    status = world["service"].status(cmp)
    got = world["service"].get(cmp)
    listed = world["service"].list()
    assert status["lifecycle"] == got["lifecycle"] == "READY" and status["summary"] is None
    assert got["title"] == "Nowruz" and cmp in [c["campaign"] for c in listed["items"]]
    assert world["conn"].execute("SELECT count(*) FROM mutations").fetchone()[0] == before


def test_unknown_campaign_is_not_found(world):
    with pytest.raises(CommsError) as refused:
        world["service"].status("cmp_" + "z" * 26)
    assert refused.value.code == "NOT_FOUND"
