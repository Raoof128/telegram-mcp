"""comms v0.3 Task D22: the campaign tools (P §30)."""

import os
from datetime import timedelta

import pytest

from comms.core import refs
from comms.core.delivery.commitment import commit_context
from comms.core.keys import rotate as rot
from comms.mcp.tools.campaigns import CAMPAIGN_TOOLS
from comms.services.campaigns import CampaignService
from comms.services.mutations import MutationExecutor
from tests.core import fakes
from tests.core.audit.legacy_fixtures import comms_world
from tests.core.campaign_helpers import NOW, person
from tests.mcp import family
from tests.services.group_fixtures import CTX

BY_NAME = {spec.name: spec for spec in CAMPAIGN_TOOLS}
NAMES = sorted(BY_NAME)
WRITES = {
    f"comms_campaign_{w}"
    for w in (
        "create",
        "set_content",
        "set_targets",
        "validate",
        "schedule",
        "unschedule",
        "send",
        "cancel",
        "retry_failed",
        "resolve_unknown",
    )
}


@pytest.fixture(scope="module")
def results(tmp_path_factory):
    w = comms_world(tmp_path_factory.mktemp("cmp"))
    rot.rotate(
        w["writer"],
        w["store"],
        "campaign-commit-key",
        material=os.urandom(32),
        prove=lambda m: None,
        now=NOW,
    )
    rcps = [person(w["conn"], phone=p)[0] for p in ("+61400000001", "+61400000002")]
    s = CampaignService(
        w["writer"],
        MutationExecutor(w["writer"], {}),
        {"whatsapp": fakes.FakeWhatsApp(conn=w["conn"])},
        commit=lambda: commit_context(w["writer"], w["store"]),
    )

    def req():
        return refs.mint("request")

    content = {"canonical": "Happy Nowruz", "links": ["https://example.org"]}
    targets = {"recipients": rcps}
    produced, examples = {}, {}
    created = s.create(CTX, "Nowruz", req())
    cmp = created["campaign"]
    produced["comms_campaign_create"], examples["comms_campaign_create"] = (
        created,
        {"title": "Nowruz"},
    )
    produced["comms_campaign_set_content"] = s.set_content(CTX, cmp, content, req())
    examples["comms_campaign_set_content"] = {"campaign": cmp, "content": content}
    produced["comms_campaign_set_targets"] = s.set_targets(CTX, cmp, targets, ["whatsapp"], req())
    examples["comms_campaign_set_targets"] = {
        "campaign": cmp,
        "targets": targets,
        "transports": ["whatsapp"],
    }
    produced["comms_campaign_validate"] = s.validate(CTX, cmp, req())
    produced["comms_campaign_preview"] = s.preview(cmp)
    at = NOW + timedelta(days=1)
    produced["comms_campaign_schedule"] = s.schedule(CTX, cmp, at, req())
    examples["comms_campaign_schedule"] = {"campaign": cmp, "at": "2026-09-25T00:00:00Z"}
    produced["comms_campaign_unschedule"] = s.unschedule(CTX, cmp, req())
    produced["comms_campaign_send"] = s.send(CTX, cmp, req())
    produced["comms_campaign_status"] = s.status(cmp)
    produced["comms_campaign_delivery_report"] = s.delivery_report(cmp, limit=1)
    produced["comms_campaign_retry_failed"] = s.retry_failed(CTX, cmp, req())
    produced["comms_campaign_cancel"] = s.cancel(CTX, cmp, req())
    produced["comms_campaign_get"] = s.get(cmp)
    produced["comms_campaign_list"] = s.list()
    job = produced["comms_campaign_delivery_report"]["items"][0]["job"]
    produced["comms_campaign_resolve_unknown"] = {**produced["comms_campaign_validate"]}
    examples["comms_campaign_resolve_unknown"] = {"job": job, "verdict": "sent"}
    for name in NAMES:
        examples.setdefault(
            name, {"campaign": cmp} if name != "comms_campaign_list" else {"limit": 5}
        )
    return {"results": produced, "examples": examples}


def test_the_family_is_p30():
    tools = (
        "create",
        "get",
        "list",
        "set_content",
        "set_targets",
        "validate",
        "preview",
        "schedule",
        "unschedule",
        "send",
        "cancel",
        "retry_failed",
        "resolve_unknown",
        "status",
        "delivery_report",
    )
    assert NAMES == sorted(f"comms_campaign_{t}" for t in tools)


@pytest.mark.parametrize("name", NAMES)
def test_schema_valid_json_schema_2020_12(name):
    family.schema_valid(BY_NAME[name])


@pytest.mark.parametrize("name", NAMES)
def test_output_schema_matches_service_result(name, results):
    family.output_matches(BY_NAME[name], results["results"][name])


@pytest.mark.parametrize("name", NAMES)
def test_annotations(name):
    family.annotations(BY_NAME[name])
    assert BY_NAME[name].read_only == (name not in WRITES)


@pytest.mark.parametrize("name", sorted(WRITES))
def test_write_requires_request_id(name, results):
    family.write_requires_request_id(BY_NAME[name], results["examples"][name])


@pytest.mark.parametrize("name", NAMES)
def test_dispatch_reaches_its_service(name, results):
    family.dispatch_reaches_its_service(
        BY_NAME[name], results["examples"][name], results["results"][name]
    )


def test_preview_and_report_carry_no_identity_or_body(results):
    text = repr([results["results"][n] for n in NAMES])
    assert "61400000001" not in text and "Happy Nowruz" not in text


def test_send_and_schedule_are_open_world_and_cancel_is_destructive():
    assert BY_NAME["comms_campaign_send"].open_world
    assert BY_NAME["comms_campaign_schedule"].open_world
    assert BY_NAME["comms_campaign_cancel"].destructive
    assert not BY_NAME["comms_campaign_create"].open_world
