"""Static import closure over ``src/comms`` (tests only).

Every ``import`` and ``from … import`` counts, including imports inside functions (a lazy
import is still reachable). Imports under ``if TYPE_CHECKING:`` do not run and are skipped.
Importing a module also runs every parent package's ``__init__``, so those are followed too.
"""

from __future__ import annotations

import ast
from pathlib import Path

SRC = Path(__file__).resolve().parents[2] / "src"


def module_file(name: str) -> Path | None:
    base = SRC.joinpath(*name.split("."))
    for candidate in (base.with_suffix(".py"), base / "__init__.py"):
        if candidate.is_file():
            return candidate
    return None


def _type_checking_nodes(tree: ast.AST) -> set[int]:
    skipped: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.If) and "TYPE_CHECKING" in ast.unparse(node.test):
            skipped.update(id(child) for stmt in node.body for child in ast.walk(stmt))
    return skipped


def _imports(name: str, path: Path) -> set[str]:
    tree = ast.parse(path.read_text())
    skipped = _type_checking_nodes(tree)
    package = name if path.name == "__init__.py" else name.rpartition(".")[0]
    found: set[str] = set()
    for node in ast.walk(tree):
        if id(node) in skipped:
            continue
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                parts = package.split(".")
                base = ".".join(parts[: len(parts) - node.level + 1])
                module = f"{base}.{node.module}" if node.module else base
            else:
                module = node.module or ""
            found.add(module)
            found.update(f"{module}.{alias.name}" for alias in node.names)
    return {m for m in found if m.startswith("comms") and module_file(m) is not None}


def closure(*roots: str) -> set[str]:
    """Every ``comms`` module that importing ``roots`` can execute."""
    seen: set[str] = set()
    pending = list(roots)
    while pending:
        name = pending.pop()
        if name in seen:
            continue
        path = module_file(name)
        if path is None:
            raise ValueError(f"no module {name}")
        seen.add(name)
        parts = name.split(".")
        pending.extend(".".join(parts[:i]) for i in range(1, len(parts)))
        pending.extend(_imports(name, path))
    return seen
