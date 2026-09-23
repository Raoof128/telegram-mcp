"""The two metadata-backed catalogue methods (spec §15.1, §15.2)."""

import pytest

from telegram_mcp.disclosure.seams import CatalogueSnapshot, VisibleProject
from telegram_mcp.telegram.metadata import MetadataReadAdapter
from telegram_mcp.telegram.service import TOOL_METHODS, RoutedRetrieval


def _project(ref_char, slug, name, egress="full_text"):
    return VisibleProject("tpr_" + ref_char * 26, slug, name, egress, None, False)


def _snapshot(*projects, page=0):
    return CatalogueSnapshot(
        principal_id=1,
        client_id=1,
        account_id=1,
        principal_ref="prn_" + "a" * 26,
        client_ref="tcl_" + "a" * 26,
        account_ref="tga_" + "a" * 26,
        client_kind="codex_local",
        security_epoch=1,
        policy_epoch=1,
        scope_hex="0" * 64,
        scope_entries=(),
        visible=tuple(projects),
        page=page,
    )


def _adapter(minted):
    def mint(snapshot, arguments, page):
        minted.append(page)
        return "tgc_" + "c" * 26

    return MetadataReadAdapter(mint_cursor=mint)


async def test_listing_pages_and_hands_back_a_cursor():
    minted = []
    projects = [_project(c, f"p{c}", f"P {c}") for c in "abc"]
    adapter = _adapter(minted)
    first = await adapter.list_projects({"limit": 2}, _snapshot(*projects))
    assert [p["project_ref"] for p in first["projects"]] == [
        projects[0].project_ref,
        projects[1].project_ref,
    ]
    assert first["_next_cursor"] == "tgc_" + "c" * 26 and minted == [1]
    last = await adapter.list_projects({"limit": 2}, _snapshot(*projects, page=1))
    assert [p["project_ref"] for p in last["projects"]] == [projects[2].project_ref]
    assert "_next_cursor" not in last


async def test_resolve_prefers_exact_and_flags_ambiguity():
    adapter = _adapter([])
    snap = _snapshot(_project("a", "ops", "Ops"), _project("b", "ops-2", "Ops Two"))
    exact = await adapter.resolve_project({"query": "OPS", "limit": 10}, snap)
    assert [m["match_kind"] for m in exact["matches"]] == ["exact_slug"] and exact[
        "ambiguous"
    ] is False
    prefix = await adapter.resolve_project({"query": "op", "limit": 10}, snap)
    assert {m["match_kind"] for m in prefix["matches"]} == {"prefix_display_name"}
    assert prefix["ambiguous"] is True
    none = await adapter.resolve_project({"query": "zz", "limit": 10}, snap)
    assert none == {"matches": [], "ambiguous": False}


async def test_resolve_matches_persian_and_composed_forms():
    adapter = _adapter([])
    snap = _snapshot(_project("a", "persian", "انجمن فارسی"), _project("b", "cafe", "Café"))
    persian = await adapter.resolve_project({"query": "انجمن", "limit": 10}, snap)
    assert [m["match_kind"] for m in persian["matches"]] == ["prefix_display_name"]
    composed = await adapter.resolve_project({"query": "CAFÉ", "limit": 10}, snap)
    assert [m["match_kind"] for m in composed["matches"]] == ["exact_display_name"]


async def test_truncation_by_limit_is_ambiguous():
    adapter = _adapter([])
    snap = _snapshot(*[_project(c, f"team-{c}", f"Team {c}") for c in "abcd"])
    result = await adapter.resolve_project({"query": "team", "limit": 2}, snap)
    assert len(result["matches"]) == 2 and result["ambiguous"] is True


async def test_routing_is_closed():
    adapter = _adapter([])
    routed = RoutedRetrieval({"telegram_list_projects": adapter.list_projects})
    assert set(TOOL_METHODS) >= {"telegram_list_projects", "telegram_get_messages"}
    with pytest.raises(LookupError):
        await routed.retrieve(tool_name="telegram_get_messages", arguments={}, snapshot=None)
