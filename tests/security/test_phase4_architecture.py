"""Spec §37 and design §6.1: what may import what, checked from the AST.

Fully qualified names, so an alias (``import telethon.tl.functions as f``)
resolves to what it names. Transitive closure for the demo server, so a
path through an intermediate module is caught too.
"""

import ast
import inspect
from pathlib import Path

from comms.transports.telegram.telegram.service import TelegramReadService

SRC = Path(__file__).resolve().parents[2] / "src" / "comms" / "transports" / "telegram"
PACKAGE = "comms.transports.telegram"
ADAPTER = SRC / "telegram" / "telethon_adapter.py"
COMPOSITION = SRC / "runtime" / "composition.py"
# comms v0.3 A3: the retired MCP runtime, kept for retained tests; no production entry reaches it.
LEGACY_COMPOSITION = SRC / "runtime" / "legacy_composition.py"

# Design §3.3, as <module>.<Request>: the reviewer's copy, deliberately not
# imported from the adapter, so a change to one without the other fails here.
REVIEWED_RPCS: frozenset[str] = frozenset(
    {
        "messages.GetDialogsRequest",
        "messages.GetPeerDialogsRequest",
        "messages.GetHistoryRequest",
        "messages.GetMessagesRequest",
        "channels.GetMessagesRequest",
        "users.GetUsersRequest",
        "updates.GetStateRequest",
        "auth.SendCodeRequest",
        "auth.SignInRequest",
        "account.GetPasswordRequest",
        "auth.CheckPasswordRequest",
        "help.GetConfigRequest",
        "messages.GetRepliesRequest",
        "messages.SearchRequest",
        "auth.LogOutRequest",  # comms v0.3 B14: admin.revoke only
    }
)

# comms v0.3 B14: the one administrative RPC, built only inside the adapter's admin.revoke path.
SANCTIONED = {(ADAPTER, "LogOutRequest")}
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
    "send_code_request",
    "sign_in",
    "is_user_authorized",
    "get_me",
    "iter_dialogs",
    "get_dialogs",
    "log_out",
    "LogOutRequest",
    "ResendCodeRequest",
    "ResolveUsernameRequest",
    "GetChannelsRequest",
    "get_entity",
    "UpdatePasswordSettingsRequest",
    "ConfirmPasswordEmailRequest",
    "InitTakeoutSessionRequest",
}
CONCRETE_BACKENDS = {
    "comms.transports.telegram.telegram.metadata",
    "comms.transports.telegram.telegram.telethon_adapter",
    "comms.transports.telegram.telegram.reads",
}


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


def _aliases(tree: ast.AST) -> dict[str, str]:
    """Local name -> fully qualified name, for every import in the module."""
    out: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                out[alias.asname or alias.name.split(".")[0]] = (
                    alias.name if alias.asname else alias.name.split(".")[0]
                )
        elif isinstance(node, ast.ImportFrom) and node.module:
            for alias in node.names:
                out[alias.asname or alias.name] = f"{node.module}.{alias.name}"
    return out


def _qualified_references(tree: ast.AST) -> set[str]:
    """Every dotted name, resolved through the module's import aliases."""
    aliases = _aliases(tree)
    found: set[str] = set(aliases.values())
    for node in ast.walk(tree):
        if not isinstance(node, ast.Attribute):
            continue
        parts: list[str] = []
        cursor: ast.AST = node
        while isinstance(cursor, ast.Attribute):
            parts.append(cursor.attr)
            cursor = cursor.value
        if isinstance(cursor, ast.Name) and cursor.id in aliases:
            found.add(".".join([aliases[cursor.id], *reversed(parts)]))
    return found


FUNCTIONS = "telethon.tl.functions."


def test_every_rpc_reference_is_reviewed():
    referenced = {
        name.removeprefix(FUNCTIONS)
        for _path, tree in _modules()
        for name in _qualified_references(tree)
        if name.startswith(FUNCTIONS) and name.count(".") == 4  # functions.<module>.<Request>
    }
    assert referenced, "the guard sees no RPC at all: it is not reading the adapter"
    assert referenced <= REVIEWED_RPCS, referenced - REVIEWED_RPCS


def test_the_guard_resolves_aliases():
    tree = ast.parse(
        "from telethon.tl import functions as f\n"
        "import telethon.tl.functions.messages as m\n"
        "f.messages.SendMessageRequest\nm.ReadHistoryRequest\n"
    )
    names = _qualified_references(tree)
    assert "telethon.tl.functions.messages.SendMessageRequest" in names
    assert "telethon.tl.functions.messages.ReadHistoryRequest" in names


def test_the_adapter_declares_the_reviewed_set():
    from comms.transports.telegram.telegram.telethon_adapter import REVIEWED_REQUESTS

    assert REVIEWED_REQUESTS == REVIEWED_RPCS


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
            if name in PROHIBITED and (path, name) not in SANCTIONED:
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
    assert importers <= {str(COMPOSITION), str(LEGACY_COMPOSITION)}


def test_the_demo_server_cannot_reach_sensitive_dispatch():
    graph = {
        _module_name(path): {n for n in _imports(tree) if n.startswith(PACKAGE)}
        for path, tree in _modules()
    }
    reachable, frontier = set(), ["comms.transports.telegram.server"]
    while frontier:
        module = frontier.pop()
        if module in reachable:
            continue
        reachable.add(module)
        frontier.extend(n for n in graph.get(module, ()) if n in graph)
    forbidden = {
        "comms.transports.telegram.sensitive_dispatch",
        "comms.transports.telegram.runtime.ingress",
        "comms.transports.telegram.runtime.composition",
        "comms.transports.telegram.runtime.legacy_composition",
        "comms.transports.telegram.disclosure.seams",
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


def test_write_rpcs_live_only_in_the_fixture_builder():
    builder = Path(__file__).resolve().parents[1] / "telegram" / "fixture_builder.py"
    assert builder.exists()
    for path, tree in _modules():
        assert "fixture_builder" not in {n.rsplit(".", 1)[-1] for n in _imports(tree)}, path
