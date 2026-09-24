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
from pathlib import Path

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


def _whatsvault_tool_names() -> set[str]:
    """The keys of the dict ``build_tool_handlers`` returns: the registered tools."""
    tree = ast.parse(WHATSVAULT_MCP.read_text(encoding="utf-8"))
    builder = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "build_tool_handlers"
    )
    returned = [
        node.value
        for node in builder.body
        if isinstance(node, ast.Return) and isinstance(node.value, ast.Dict)
    ]
    assert len(returned) == 1, "the tool registry must be one literal dict"
    return {key.value for key in returned[0].keys if isinstance(key, ast.Constant)}


def test_no_whatsvault_mcp_tool_is_a_transmission_primitive():
    registered = _whatsvault_tool_names()
    assert registered == WHATSVAULT_TOOLS
    for name in registered:
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
