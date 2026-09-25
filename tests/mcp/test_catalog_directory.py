"""comms v0.3 Task D23: the location and audience tools (P §31)."""

import pytest

from comms.core import refs
from comms.mcp.tools.directory import DIRECTORY_TOOLS
from comms.services.directory import DirectoryService
from comms.services.mutations import MutationExecutor
from tests.core.audit.legacy_fixtures import comms_world
from tests.core.campaign_helpers import person
from tests.mcp import family
from tests.services.group_fixtures import CTX

BY_NAME = {spec.name: spec for spec in DIRECTORY_TOOLS}
NAMES = sorted(BY_NAME)
READS = {
    f"comms_{r}"
    for r in ("location_list", "location_get", "audience_list", "audience_get", "audience_resolve")
}


@pytest.fixture(scope="module")
def results(tmp_path_factory):
    w = comms_world(tmp_path_factory.mktemp("dir"))
    s = DirectoryService(w["writer"], MutationExecutor(w["writer"], {}))
    rcp = person(w["conn"], phone="+61400000001")[0]

    def req():
        return refs.mint("request")

    produced, examples = {}, {}
    created = s.location_create(CTX, "Parramatta", req())
    loc = created["location"]
    produced["comms_location_create"], examples["comms_location_create"] = (
        created,
        {"name": "Parramatta"},
    )
    produced["comms_location_update"] = s.location_update(CTX, loc, "Parramatta West", req())
    examples["comms_location_update"] = {"location": loc, "name": "Parramatta West"}
    produced["comms_location_disable"] = s.location_disable(CTX, loc, req())
    produced["comms_location_enable"] = s.location_enable(CTX, loc, req())
    examples["comms_location_disable"] = examples["comms_location_enable"] = {"location": loc}
    produced["comms_location_get"], examples["comms_location_get"] = (
        s.location_get(loc),
        {"location": loc},
    )
    produced["comms_location_list"], examples["comms_location_list"] = (
        s.location_list(),
        {"limit": 5},
    )
    made = s.audience_create(CTX, "Families", req())
    aud = made["audience"]
    produced["comms_audience_create"], examples["comms_audience_create"] = (
        made,
        {"name": "Families"},
    )
    produced["comms_audience_update"] = s.audience_update(CTX, aud, "All families", req())
    examples["comms_audience_update"] = {"audience": aud, "name": "All families"}
    produced["comms_audience_add"] = s.audience_add(CTX, aud, rcp, req())
    produced["comms_audience_resolve"] = s.audience_resolve(aud)
    produced["comms_audience_get"] = s.audience_get(aud)
    produced["comms_audience_remove"] = s.audience_remove(CTX, aud, rcp, req())
    examples["comms_audience_add"] = examples["comms_audience_remove"] = {
        "audience": aud,
        "member": rcp,
    }
    examples["comms_audience_resolve"] = examples["comms_audience_get"] = {"audience": aud}
    produced["comms_audience_list"], examples["comms_audience_list"] = s.audience_list(), {}
    return {"results": produced, "examples": examples}


def test_the_family_is_p31():
    tools = (
        "location_list",
        "location_get",
        "location_create",
        "location_update",
        "location_enable",
        "location_disable",
        "audience_list",
        "audience_get",
        "audience_create",
        "audience_update",
        "audience_add",
        "audience_remove",
        "audience_resolve",
    )
    assert NAMES == sorted(f"comms_{t}" for t in tools)


@pytest.mark.parametrize("name", NAMES)
def test_schema_valid_json_schema_2020_12(name):
    family.schema_valid(BY_NAME[name])


@pytest.mark.parametrize("name", NAMES)
def test_output_schema_matches_service_result(name, results):
    family.output_matches(BY_NAME[name], results["results"][name])


@pytest.mark.parametrize("name", NAMES)
def test_annotations(name):
    family.annotations(BY_NAME[name])
    assert BY_NAME[name].read_only == (name in READS)
    assert not BY_NAME[name].open_world  # the directory is local


@pytest.mark.parametrize("name", sorted(set(NAMES) - READS))
def test_write_requires_request_id(name, results):
    family.write_requires_request_id(BY_NAME[name], results["examples"][name])


@pytest.mark.parametrize("name", NAMES)
def test_dispatch_reaches_its_service(name, results):
    family.dispatch_reaches_its_service(
        BY_NAME[name], results["examples"][name], results["results"][name]
    )


def test_resolve_names_endpoints_not_identities(results):
    resolved = results["results"]["comms_audience_resolve"]
    assert resolved["count"] == 1 and "61400000001" not in repr(resolved)
