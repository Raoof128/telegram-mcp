"""comms v0.3 §AI boundary under owner_full_admin (spec A29, A37; D5): what is proven, and no more.

v0.2 proved that no MCP tool could transmit. v0.3 retires that guard: the owner grants
the AI full administration, so writes and sends are legitimate tools. What is proven now
is that the AI surface is exactly the one catalog, every write says it is a write, every
destructive operation says it is destructive, no tool is an untyped provider tunnel, and
MCP handlers reach the service layer only. WhatsVault still has no network client and no
real provider; real adapters arrive in Part C under ``comms.transports``. Host prompts
belong to the host (D5); nothing here asserts a Claude Code permission rule.
"""

import ast
import importlib.metadata
import importlib.util
import json
import sys
from pathlib import Path

import pytest

from comms.mcp.catalog import TOOL_CATALOG, tools_list_payload
from comms.mcp.dispatch import Dispatcher
from comms.services.registry import ServiceRegistry
from tests.security.import_closure import closure

ROOT = Path(__file__).resolve().parents[2]
WHATSAPP = ROOT / "transports" / "whatsapp"
WHATSVAULT = WHATSAPP / "src" / "whatsvault"
MCP = ROOT / "src" / "comms" / "mcp"
NETWORK = {"urllib.request", "http.client", "httpx", "requests", "aiohttp", "socket"}
T = "comms.transports.telegram."
# P §23–29: every semantic operation that deletes, removes, revokes, reduces authority or
# overwrites provider state. Each catalog entry for one of these must be destructive; the
# entries arrive in Part D, so a missing one is not yet a failure.
DESTRUCTIVE_OPERATIONS = frozenset(
    {
        "message.delete",
        "message.edit",
        "group.member.remove",
        "group.member.ban",
        "group.member.restrict",
        "group.admin.demote",
        "group.admin.update_rights",
        "group.permissions.set",
        "group.info.set_title",
        "group.info.set_description",
        "group.info.set_photo",
        "group.invite.revoke",
        "group.join_requests.reject",
        "group.delete",
        "group.migrate",
    }
)
RAW_WORDS = ("raw", "rpc", "graph", "method", "endpoint", "invoke", "passthrough")
# A37: MCP handlers call the typed service layer; they never write SQLite or call an adapter.
MCP_FORBIDDEN = (
    "comms.core.campaigns",
    "comms.core.delivery",
    "comms.core.storage",
    "comms.transports",
    "whatsvault",
)


def _imports(path: Path) -> set[str]:
    names = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            names.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


def _operation(tool_name: str) -> str:
    """comms_group_member_remove -> group.member.remove (P §38 naming)."""
    return tool_name.removeprefix("comms_").replace("_", ".")


def test_the_ai_surface_is_exactly_the_catalog():
    names = [spec.name for spec in TOOL_CATALOG]
    assert [tool["name"] for tool in tools_list_payload()] == names
    services = ServiceRegistry()
    for spec in TOOL_CATALOG:
        services.register(spec.service, lambda client, arguments: {})
    assert sorted(Dispatcher(services)._tools) == sorted(names)
    # The retired MCP servers are not reachable from any production entry (A14).
    production = closure(T + "cli", T + "runtime.daemon")
    assert production.isdisjoint({T + "server", T + "dispatch", T + "sensitive_dispatch"})
    assert not (WHATSAPP / "apps" / "mcp").exists()


def test_write_tools_are_annotated_as_writes():
    for spec in TOOL_CATALOG:
        # A28: every mutating tool, local or provider, requires a request_id.
        assert spec.read_only is not spec.requires_request_id, spec.name
        if spec.destructive:
            assert not spec.read_only, spec.name


def test_destructive_tools_are_annotated_destructive():
    assert all(op.count(".") >= 1 for op in DESTRUCTIVE_OPERATIONS)
    for spec in TOOL_CATALOG:
        if (
            spec.service in DESTRUCTIVE_OPERATIONS
            or _operation(spec.name) in DESTRUCTIVE_OPERATIONS
        ):
            assert spec.destructive and not spec.read_only, spec.name


def test_no_raw_rpc_tool():
    for spec in TOOL_CATALOG:
        words = spec.name.removeprefix("comms_").split("_") + spec.service.replace(".", "_").split(
            "_"
        )
        assert not any(w in RAW_WORDS for w in words), spec.name


def test_mcp_handlers_reach_the_service_layer_only():
    paths = sorted(MCP.rglob("*.py"))
    assert any(p.name == "dispatch.py" for p in paths), "an empty scan proves nothing"
    for path in paths:
        bad = [n for n in _imports(path) if n.startswith(MCP_FORBIDDEN)]
        assert not bad, (path, bad)


def test_the_mcp_guard_catches_a_planted_import(tmp_path, monkeypatch):
    planted = tmp_path / "dispatch.py"
    planted.write_text("from comms.core.delivery.freeze import send\n")
    monkeypatch.setattr(sys.modules[__name__], "MCP", tmp_path)
    with pytest.raises(AssertionError):
        test_mcp_handlers_reach_the_service_layer_only()


def test_whatsvault_still_has_no_network_client_outside_adapters():
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
        reached = {n for n in _imports(path) if n.startswith("whatsvault")}
        assert reached <= WHATSVAULT_ALLOWED, path


# D39-PRE E10b (R-E14, the owner's comms-native archive): comms may import exactly WhatsVault's
# pure webhook normaliser, and nothing it reaches may be WhatsVault's dispatcher, databases,
# MCP or ops code. Proved at run time in a fresh interpreter.
WHATSVAULT_ALLOWED = frozenset({"whatsvault.ingest.normalise", "whatsvault.ingest"})
WHATSVAULT_REACHABLE = frozenset(
    {"whatsvault", "whatsvault.ingest", "whatsvault.ingest.normalise", "whatsvault.ingest.dedupe"}
)


def test_the_archive_reaches_only_the_whatsvault_normaliser():
    import subprocess

    probe = (
        "import json, sys, comms.transports.whatsapp.webhooks.archive;"
        "print(json.dumps(sorted(m for m in sys.modules if m.startswith('whatsvault'))))"
    )
    done = subprocess.run([sys.executable, "-c", probe], capture_output=True, text=True, check=True)
    assert set(json.loads(done.stdout)) <= WHATSVAULT_REACHABLE, done.stdout


def test_the_destructive_guard_catches_an_unmarked_delete(monkeypatch):
    from dataclasses import replace

    seed = TOOL_CATALOG[0]
    unmarked = replace(
        seed,
        name="comms_message_delete",
        service="message.delete",
        read_only=False,
        requires_request_id=True,
        destructive=False,
    )
    monkeypatch.setattr(sys.modules[__name__], "TOOL_CATALOG", (seed, unmarked))
    with pytest.raises(AssertionError):
        test_destructive_tools_are_annotated_destructive()
