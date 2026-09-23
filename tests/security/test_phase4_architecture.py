"""Spec §37 and design §6.1: what may import what, checked from the AST.

Fully qualified names, so an alias (``import telethon.tl.functions as f``)
resolves to what it names. Transitive closure for the demo server, so a
path through an intermediate module is caught too.
"""

import ast
import inspect
from pathlib import Path

from telegram_mcp.telegram.service import TelegramReadService

SRC = Path(__file__).resolve().parents[2] / "src" / "telegram_mcp"
PACKAGE = "telegram_mcp"
ADAPTER = SRC / "telegram" / "telethon_adapter.py"
COMPOSITION = SRC / "runtime" / "composition.py"

# The reviewed RPC allowlist per sub-phase (design §3.3, §4.1-§4.2). 4a: none.
REVIEWED_RPCS: frozenset[str] = frozenset()

PROHIBITED = {
    "send_message",
    "send_file",
    "forward_messages",
    "edit_message",
    "delete_messages",
    "send_read_acknowledge",
    "read_history",
    "leave_channel",
    "join_channel",
    "edit_admin",
    "block",
    "ReadHistoryRequest",
    "ReadMessageContentsRequest",
    "ReadMentionsRequest",
    "ReadReactionsRequest",
    "GetMessagesViewsRequest",
    "SearchGlobalRequest",
}
CONCRETE_BACKENDS = {"telegram_mcp.telegram.metadata", "telegram_mcp.telegram.telethon_adapter"}


def _modules():
    paths = sorted(SRC.rglob("*.py"))
    # Anchored to this file, and non-vacuous: executing revision 1 showed that
    # a relative SRC run from another directory made seven guards pass on an
    # empty file list.
    assert len(paths) > 40, f"source tree not found at {SRC}"
    for path in paths:
        yield path, ast.parse(path.read_text(encoding="utf-8"))


def _imports(tree: ast.AST) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
            names.update(f"{node.module}.{alias.name}" for alias in node.names)
    return names


def _module_name(path: Path) -> str:
    parts = path.relative_to(SRC.parent).with_suffix("").parts
    return ".".join(parts[:-1] if parts[-1] == "__init__" else parts)


def test_only_the_adapter_imports_telethon():
    offenders = [
        str(path)
        for path, tree in _modules()
        if path != ADAPTER and any(name.split(".")[0] == "telethon" for name in _imports(tree))
    ]
    assert offenders == []


def test_every_rpc_reference_is_reviewed():
    referenced = {
        name.rsplit(".", 1)[-1]
        for _path, tree in _modules()
        for name in _imports(tree)
        if name.startswith("telethon.tl.functions.")
    }
    assert referenced <= REVIEWED_RPCS


def test_no_prohibited_symbol_appears_in_src():
    hits = []
    for path, tree in _modules():
        for node in ast.walk(tree):
            name = (
                node.attr
                if isinstance(node, ast.Attribute)
                else node.id
                if isinstance(node, ast.Name)
                else node.name
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                else None
            )
            if name in PROHIBITED:
                hits.append(f"{path}:{node.lineno}:{name}")
    assert hits == []


def test_dispatch_and_tools_import_no_concrete_backend():
    guarded = [
        SRC / "dispatch.py",
        SRC / "sensitive_dispatch.py",
        SRC / "runtime" / "ingress.py",
        *(SRC / "tools").glob("*.py"),
    ]
    for path in guarded:
        assert not (_imports(ast.parse(path.read_text())) & CONCRETE_BACKENDS), path


def test_only_composition_wires_a_backend():
    importers = {
        str(path)
        for path, tree in _modules()
        if _imports(tree) & CONCRETE_BACKENDS and path.parent != SRC / "telegram"
    }
    assert importers <= {str(COMPOSITION)}


def test_the_demo_server_cannot_reach_sensitive_dispatch():
    graph = {
        _module_name(path): {n for n in _imports(tree) if n.startswith(PACKAGE)}
        for path, tree in _modules()
    }
    reachable, frontier = set(), ["telegram_mcp.server"]
    while frontier:
        module = frontier.pop()
        if module in reachable:
            continue
        reachable.add(module)
        frontier.extend(n for n in graph.get(module, ()) if n in graph)
    forbidden = {
        "telegram_mcp.sensitive_dispatch",
        "telegram_mcp.runtime.ingress",
        "telegram_mcp.runtime.composition",
        "telegram_mcp.disclosure.seams",
        *CONCRETE_BACKENDS,
    }
    assert reachable.isdisjoint(forbidden), reachable & forbidden


def test_src_never_imports_tests():
    for path, tree in _modules():
        assert not any(name.split(".")[0] == "tests" for name in _imports(tree)), path


def test_the_read_service_surface_is_the_reviewed_one():
    expected = {
        "list_projects",
        "resolve_project",
        "list_chats",
        "resolve_peer",
        "get_messages",
        "get_context",
        "search_messages",
        "cross_project_search",
        "get_unread",
    }
    methods = {
        n
        for n, _ in inspect.getmembers(TelegramReadService, inspect.isfunction)
        if not n.startswith("_")
    }
    assert methods == expected
    for name in expected:
        params = list(inspect.signature(getattr(TelegramReadService, name)).parameters)
        assert params == ["self", "arguments", "snapshot"], name
