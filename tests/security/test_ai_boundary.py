"""comms spec v0.2 §AI-boundary: exactly what is proven, and no more (5b-3 design §3, §4).

Proven here: no MCP tool on either transport is a transmission primitive,
WhatsVault has no network client and no real provider, its dormant dispatcher
is unreachable from the installed distribution, and Claude Code asks before
every send command. Not claimed: that a shell-capable agent cannot run a
command the operator could. Agents granted unrestricted operator-shell
authority are, by definition, inside the operator trust boundary.
"""

import ast
import importlib.metadata
import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
WHATSAPP = ROOT / "transports" / "whatsapp"
WHATSVAULT = WHATSAPP / "src" / "whatsvault"
WHATSVAULT_MCP = WHATSAPP / "apps" / "mcp" / "server.py"
SEND_VERBS = ("send", "transmit", "post", "publish", "forward", "reply", "broadcast", "dispatch")
NETWORK = {"urllib.request", "http.client", "httpx", "requests", "aiohttp", "socket"}
WHATSVAULT_TOOLS = {
    "search",
    "get_messages",
    "list_chats",
    "get_message_status",
    "get_conversation_window",
    "list_templates",
}


def _imports(path: Path) -> set[str]:
    names = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            names.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


def test_no_telegram_mcp_tool_is_a_transmission_primitive():
    from comms.transports.telegram.contract import EXPECTED_TOOLS

    assert len(EXPECTED_TOOLS) == 10
    for name in EXPECTED_TOOLS:
        assert not any(verb in name for verb in SEND_VERBS), name


def test_whatsvault_mcp_app_is_retired_and_its_tools_were_read_only():
    """comms v0.3 retired `apps/mcp` (R-A16); its six tool names are tombstoned (A2).

    The names stay pinned here so a reintroduction under any of them is noticed, and so
    the record shows none was ever a transmission primitive.
    """
    assert not WHATSVAULT_MCP.exists()
    assert len(WHATSVAULT_TOOLS) == 6
    for name in WHATSVAULT_TOOLS:
        assert not any(verb in name for verb in SEND_VERBS), name


def test_whatsvault_has_no_network_client():
    for path in [*WHATSVAULT.rglob("*.py"), *(WHATSAPP / "apps").rglob("*.py")]:
        assert not (_imports(path) & NETWORK), path


def test_whatsvault_providers_are_only_the_protocol_and_the_fake():
    names = sorted(p.name for p in (WHATSVAULT / "providers").glob("*.py"))
    assert names == ["__init__.py", "base.py", "fake_meta.py"]


def test_the_dormant_dispatcher_is_unreachable_from_the_root():
    assert importlib.util.find_spec("apps") is None
    scripts = sorted(e.name for e in importlib.metadata.distribution("telegram-mcp").entry_points)
    assert scripts == ["comms", "telegram-mcp"]
    for path in (ROOT / "src" / "comms").rglob("*.py"):
        assert not any(n.startswith("whatsvault") for n in _imports(path)), path


def test_send_commands_are_always_ask_in_claude_code():
    """A UX interlock, not the authorization boundary (spec v0.2 §AI-boundary)."""
    settings = json.loads((ROOT / ".claude" / "settings.json").read_text(encoding="utf-8"))
    ask = settings["permissions"]["ask"]
    for rule in (
        "Bash(comms campaign send:*)",
        "Bash(uv run comms campaign send:*)",
        "Bash(comms campaign retry-failed:*)",
        "Bash(uv run comms campaign retry-failed:*)",
    ):
        assert rule in ask


CAMPAIGN_CORE = ("comms.core.campaigns", "comms.core.delivery")


def _ai_surfaces() -> list[Path]:
    """Every module an MCP server or its dispatch can load (5b-4 design §1, R21)."""
    transports = ROOT / "src" / "comms" / "transports"
    paths = [
        p
        for p in transports.glob("*/*.py")
        if p.name in {"server.py", "dispatch.py", "sensitive_dispatch.py"}
    ]
    paths += [p for p in transports.rglob("*.py") if "mcp" in p.parent.parts]
    paths += sorted((WHATSAPP / "apps" / "mcp").glob("*.py"))
    return sorted(set(paths))


def test_no_ai_surface_imports_the_campaign_core():
    surfaces = _ai_surfaces()
    assert any(p.name == "server.py" for p in surfaces), "an empty scan proves nothing"
    for path in surfaces:
        assert not [n for n in _imports(path) if n.startswith(CAMPAIGN_CORE)], path


def test_the_campaign_core_guard_catches_a_planted_import(tmp_path, monkeypatch):
    planted = tmp_path / "server.py"
    planted.write_text("from comms.core.delivery.freeze import send\n")
    monkeypatch.setattr(sys.modules[__name__], "_ai_surfaces", lambda: [planted, planted])
    with pytest.raises(AssertionError):
        test_no_ai_surface_imports_the_campaign_core()
