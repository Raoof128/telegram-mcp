"""Design §2.1 and 0B G4: who may own a transaction, and no await inside one."""

import ast
from pathlib import Path

SRC = Path(__file__).resolve().parents[2] / "src" / "telegram_mcp"
HANDLERS = SRC / "ipc" / "handlers"
TX_NAMES = {"immediate_transaction", "commit", "rollback"}


def _names(tree: ast.AST) -> set[str]:
    found = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute):
            found.add(node.attr)
        elif isinstance(node, ast.Name):
            found.add(node.id)
    return found


def test_only_the_wrapper_owns_an_admin_transaction():
    for path in sorted(HANDLERS.glob("*.py")):
        if path.name == "_wrapper.py":
            continue
        assert not _names(ast.parse(path.read_text())) & TX_NAMES, path


def _is_tx(expr: ast.expr) -> bool:
    func = expr.func if isinstance(expr, ast.Call) else None
    name = getattr(func, "attr", None) or getattr(func, "id", None)
    return name == "immediate_transaction"


def test_no_await_inside_a_transaction():
    offenders = []
    for path in sorted(SRC.rglob("*.py")):
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, (ast.With, ast.AsyncWith)) and any(
                _is_tx(item.context_expr) for item in node.items
            ):
                for inner in (n for stmt in node.body for n in ast.walk(stmt)):
                    if isinstance(inner, ast.Await):
                        offenders.append(f"{path.relative_to(SRC)}:{node.lineno}")
    assert offenders == []


def test_scope_mode_exists_exactly_once():
    hits = [p.name for p in HANDLERS.glob("*.py") if '"scope mode"' in p.read_text()]
    assert hits == ["projects.py"]


DECISION_FIELDS = {
    "owner_mode",
    "include_archived",
    "include_private",
    "include_groups",
    "include_channels",
}


def test_owner_policy_is_decided_only_in_authority_policy():
    """0B G13: loads are keyword arguments; any attribute access is a decision."""
    offenders = []
    for path in sorted(SRC.rglob("*.py")):
        if path == SRC / "authority" / "policy.py":
            continue
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.Attribute) and node.attr in DECISION_FIELDS:
                offenders.append(f"{path.relative_to(SRC)}:{node.lineno}")
    assert offenders == []


def test_only_authority_policy_calls_the_class_rule():
    """Review #1: no retrieval code decides chat class on its own."""
    offenders = []
    for path in sorted(SRC.rglob("*.py")):
        if path == SRC / "authority" / "policy.py":
            continue
        for node in ast.walk(ast.parse(path.read_text())):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr in {"admits", "decide"}
            ):
                offenders.append(f"{path.relative_to(SRC)}:{node.lineno}")
    assert offenders == []


def test_snapshots_carry_the_view_not_a_private_owner_scope():
    from dataclasses import fields

    from telegram_mcp.disclosure.seams import ProjectSnapshot
    from telegram_mcp.disclosure.search_authority import SearchSnapshot

    for cls in (ProjectSnapshot, SearchSnapshot):
        names = {f.name for f in fields(cls)}
        assert "view" in names and "owner_scope" not in names, cls
