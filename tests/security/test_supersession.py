"""comms v0.3 Task A2: the supersession manifest (spec A5), pinned against the spec and design M9."""

import ast
import json
import re
from pathlib import Path

import pytest

from comms.core.canonical import jcs_dumps

ROOT = Path(__file__).resolve().parents[2]
MANIFEST = ROOT / "docs" / "comms-v0.3-supersession.json"
SPEC = ROOT / "docs" / "comms-spec-v0.3.md"
DESIGN = ROOT / "docs" / "superpowers" / "specs" / "2026-09-24-comms-v0.3-design.md"
WV_MCP = ROOT / "transports" / "whatsapp" / "apps" / "mcp" / "server.py"


def _manifest() -> dict:
    from comms.transports.telegram.contract import strict_json_loads

    return strict_json_loads(MANIFEST.read_text(encoding="utf-8"))


def _m9_names() -> tuple[set[str], set[str]]:
    row = next(
        line
        for line in DESIGN.read_text(encoding="utf-8").splitlines()
        if line.startswith("| M9 |")
    )
    fact = row.split("|")[2]  # the Fact column only; the last column names measuring code
    telegram_part, whatsvault_part = fact.split("The 6 retired WhatsVault tools are")
    return set(re.findall(r"`(telegram_[a-z_]+)`", telegram_part)), set(
        re.findall(r"`([a-z_]+)`", whatsvault_part)
    )


def test_manifest_is_canonical_jcs():
    raw = MANIFEST.read_bytes()
    assert jcs_dumps(json.loads(raw)) == raw.rstrip(b"\n")


def test_manifest_tombstones_match_the_spec():
    text = SPEC.read_text(encoding="utf-8")
    section = text[text.index("## Tombstones added in v0.3") :]
    named = {
        n for n in re.findall(r"`([^`]+)`", section) if not n.endswith(".py")
    }  # identifiers, not test paths
    m = _manifest()
    assert named <= set(m["tombstoned_identifiers"]) | set(m["closed_to_append"])
    assert set(m["closed_to_append"]) == {"telegram-mcp-audit-v1", "telegram-mcp-audit-genesis-v1"}
    telegram, whatsvault = _m9_names()
    assert set(m["tombstoned_identifiers"]) == telegram | whatsvault | {
        "tgml1",
        "tg-mcp-policy-bundle/v1",
        "tg-mcp-policy-signature/v1",
    }


def test_manifest_names_the_design_m9_tools():
    telegram, whatsvault = _m9_names()
    assert len(telegram) == 10 and len(whatsvault) == 6
    try:  # live cross-checks while the retired sources still exist (A14/A16 delete them)
        from comms.transports.telegram.contract import EXPECTED_TOOLS
    except ImportError:
        pass
    else:
        assert set(EXPECTED_TOOLS) == telegram
    if WV_MCP.exists():
        tree = ast.parse(WV_MCP.read_text(encoding="utf-8"))
        builder = next(
            n
            for n in ast.walk(tree)
            if isinstance(n, ast.FunctionDef) and n.name == "build_tool_handlers"
        )
        [ret] = [n.value for n in builder.body if isinstance(n, ast.Return)]
        assert {k.value for k in ret.keys if isinstance(k, ast.Constant)} == whatsvault


def _catalog_names() -> set[str]:
    try:  # the catalog arrives in Task A19; until then it is empty (the planted test proves teeth)
        from comms.mcp.catalog import TOOL_CATALOG
    except ImportError:
        return set()
    return {t.name for t in TOOL_CATALOG}


def test_manifest_tombstones_are_disjoint_from_the_catalog():
    assert not set(_manifest()["tombstoned_identifiers"]) & _catalog_names()


def test_the_disjointness_check_has_teeth(monkeypatch):
    import sys

    monkeypatch.setattr(sys.modules[__name__], "_catalog_names", lambda: {"telegram_status"})
    with pytest.raises(AssertionError):
        test_manifest_tombstones_are_disjoint_from_the_catalog()


def test_manifest_declares_the_new_authority():
    m = _manifest()
    assert m["schema"] == "comms-v0.3-supersession/v1"
    assert m["new_authority"] == {"profile": "owner_full_admin", "surface": "comms_mcp"}
    assert set(m["retired_authority"]) == {
        "projects",
        "grants",
        "owner_scope",
        "disclosure_consent",
        "exposure_budgets",
    }
    assert all(m["retained_for_verification"].values())


def test_the_spec_precedence_ranks_v03_first():
    text = SPEC.read_text(encoding="utf-8")
    section = text[text.index("## Precedence") : text.index("## Decisions")]
    rows = [line for line in section.splitlines() if line.startswith("| **")]
    ranked = [re.match(r"\| \*\*(.+?)\*\*", row).group(1) for row in rows]
    assert ranked == [
        "comms-spec-v0.3",
        "comms-spec-v0.2",
        "Telegram MCP spec v0.1.10",
        "WhatsVault spec",
    ]
    assert "governed by the highest document in this table that speaks to it" in section
