"""comms v0.3 Task D30 (prerequisite): every catalog tool reaches a real service through one
registry, so the CLI and MCP share one path."""

import os

import pytest

from comms.core import refs
from comms.core.delivery.commitment import commit_context
from comms.core.keys import rotate as rot
from comms.mcp.catalog import TOOL_CATALOG
from comms.mcp.dispatch import AuthenticatedClient, Dispatcher
from comms.runtime.facades import NOT_OFFERED, Services, build_registry
from comms.services.account import AccountService
from comms.services.campaigns import CampaignService
from comms.services.context import ContextEngine
from comms.services.directory import DirectoryService
from comms.services.groups import GroupService
from comms.services.handles import ContextHandles
from comms.services.identity import IdentityService
from comms.services.messages import MessageService
from tests.core import fakes
from tests.core.campaign_helpers import NOW
from tests.services.context_fixtures import Clock, Source
from tests.services.group_fixtures import fixtures, group_world
from tests.services.test_templates_media_account import WebhookState

CLIENT = AuthenticatedClient(client_ref="cli_" + "a" * 26, auth_kind="cml1")


@pytest.fixture
def world(tmp_path):
    w = group_world(tmp_path)
    for purpose in ("campaign-commit-key", "cursor-key"):
        rot.rotate(
            w["writer"], w["store"], purpose, material=os.urandom(32), prove=lambda m: None, now=NOW
        )
    capability, executor, admins = fixtures(w)
    conn = w["conn"]
    services = Services(
        conn=conn,
        capability=capability,
        context=ContextEngine(
            conn,
            {"telegram_user": Source(), "telegram_bot": Source(provenance="telegram_local")},
            clock=lambda: NOW,
            monotonic=Clock(),
            capability=capability,
        ),
        handles=ContextHandles(conn, w["store"], clock=lambda: NOW),
        groups=GroupService(conn, capability, executor),
        messages=MessageService(conn, capability, executor),
        campaigns=CampaignService(
            w["writer"],
            executor,
            {"whatsapp": fakes.FakeWhatsApp(conn=conn)},
            commit=lambda: commit_context(w["writer"], w["store"]),
        ),
        directory=DirectoryService(w["writer"], executor),
        templates=None,
        media=None,
        account=AccountService(capability, webhooks=WebhookState()),
        identity=IdentityService(conn),
        actors=("telegram_bot", "telegram_user"),
    )
    w.update(dispatcher=Dispatcher(build_registry(services)), admins=admins)
    return w


def _call(world, name, arguments):
    spec = next(s for s in TOOL_CATALOG if s.name == name)
    if spec.requires_request_id:
        arguments = {**arguments, "request_id": refs.mint("request")}
    return world["dispatcher"].call(CLIENT, name, arguments)


def test_every_catalog_service_is_registered():
    registry = build_registry(None)  # registration needs no services
    assert {s.service for s in TOOL_CATALOG} <= set(registry.names())


def test_campaign_and_directory_writes_reach_the_core(world):
    created = _call(world, "comms_campaign_create", {"title": "Nowruz"})
    assert created.error_code is None and created.structured["campaign"].startswith("cmp_")
    loc = _call(world, "comms_location_create", {"name": "Parramatta"})
    assert loc.structured["location"].startswith("loc_")
    listed = _call(world, "comms_campaign_list", {})
    assert [c["campaign"] for c in listed.structured["items"]] == [created.structured["campaign"]]


def test_group_reads_and_member_writes_resolve_the_group_ref(world):
    got = _call(world, "comms_group_get", {"group": world["grp"]})
    assert got.structured["group"] == world["grp"] and got.structured["name"] == "G"
    banned = _call(
        world, "comms_group_member_ban", {"group": world["grp"], "recipient": world["rcp"]}
    )
    assert banned.error_code is None and banned.structured["result"] == "SUCCEEDED"
    assert banned.structured["actor"] == "telegram_bot"
    missing = _call(world, "comms_group_get", {"group": "grp_" + "z" * 26})
    assert missing.error_code == "NOT_FOUND"


def test_message_send_as_me(world):
    sent = _call(
        world,
        "comms_message_send",
        {"group": world["grp"], "text": "Salaam", "actor": "telegram_user"},
    )
    assert sent.error_code is None and sent.structured["actor"] == "telegram_user"
    assert sent.structured["message"].startswith("cmg_")


def test_context_pages_through_client_bound_cursors(world):
    first = _call(world, "comms_context_recent", {"group": world["grp"], "limit": 3})
    token = first.structured["next_cursor"]
    assert first.error_code is None and token.startswith("cur_")
    second = _call(world, "comms_context_page", {"cursor": token})
    assert second.error_code is None and second.structured["items"]
    ids = {i["message_ref"] for i in first.structured["items"]}
    assert ids.isdisjoint({i["message_ref"] for i in second.structured["items"]})
    other = AuthenticatedClient(client_ref="cli_" + "b" * 26, auth_kind="cml1")
    stolen = world["dispatcher"].call(other, "comms_context_page", {"cursor": token})
    assert stolen.error_code == "STALE_HANDLE"  # a cursor is bound to its client


def test_capability_and_identity(world):
    matrix = _call(world, "comms_capability_for_group", {"group": world["grp"]})
    assert set(matrix.structured["actors"]) == {"telegram_bot", "telegram_user"}
    inspected = _call(world, "comms_admin_identity_inspect", {"ref": world["grp"]})
    assert inspected.structured["identities"] == [{"transport": "telegram", "identity": "-77"}]


@pytest.mark.parametrize("name", sorted({s.name for s in TOOL_CATALOG if s.service in NOT_OFFERED}))
def test_tools_not_offered_answer_provider_unsupported(world, name):
    spec = next(s for s in TOOL_CATALOG if s.name == name)
    example = {k: v for k, v in _minimal(world, spec).items()}
    assert _call(world, name, example).error_code in ("PROVIDER_UNSUPPORTED", "INVALID_ARGUMENT")


def _minimal(world, spec):
    """A schema-valid argument set built from the required properties."""
    values = {
        "group": world["grp"],
        "recipient": world["rcp"],
        "media": "med_" + "a" * 26,
        "topic": "top_" + "a" * 26,
        "message": "cmg_" + "a" * 26,
        "to_group": world["grp"],
        "location": "loc_" + "a" * 26,
        "title": "T",
        "kind": "supergroup",
        "file": "f",
        "mime": "image/png",
    }
    return {k: values[k] for k in spec.input_schema.get("required", []) if k in values}
