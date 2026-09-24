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
