"""Prove the 5b-1 move changed nothing but paths (comms design rev 2, §2.4).

For every file under ``src/telegram_mcp`` at BASE:

* ``.py``: parse BASE and HEAD, rewrite BASE's imports and resource anchors
  through the move map, and compare ``ast.dump`` without positions.
* anything else: the git blob hash at BASE equals the blob at the new path.

HEAD may add only ``NEW_FILES`` under ``src/comms``.
"""

from __future__ import annotations

import ast
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from migration.move_map import NEW_DIR, NEW_FILES, OLD_DIR, RESOURCE_ANCHORS, rewrite_module

_PROTOCOL = re.compile(r"^(telegram-mcp-|tg-mcp-|tgml1)")


@dataclass
class Report:
    modules: int = 0
    data_files: int = 0
    problems: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.problems


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=repo, check=True, capture_output=True, text=True
    ).stdout


class _Rewrite(ast.NodeTransformer):
    def __init__(self, anchors: list[tuple[str, str]]) -> None:
        self._anchors = anchors

    def visit_Import(self, node: ast.Import) -> ast.Import:
        for alias in node.names:
            alias.name = rewrite_module(alias.name)
        return node

    def visit_ImportFrom(self, node: ast.ImportFrom) -> ast.ImportFrom:
        if node.module:
            node.module = rewrite_module(node.module)
        return node

    def visit_Constant(self, node: ast.Constant) -> ast.Constant:
        for old, new in self._anchors:
            if node.value == old:
                node.value = new
        return node


def _anchor_values(rel_in_pkg: str) -> list[tuple[str, str]]:
    values = []
    for path, old, new in RESOURCE_ANCHORS:
        if path == rel_in_pkg:
            values.append(
                (
                    ast.literal_eval(old.split("(", 1)[1][:-1]),
                    ast.literal_eval(new.split("(", 1)[1][:-1]),
                )
            )
    return values


def check(base_ref: str, *, repo_root: Path) -> Report:
    report = Report()
    listing = _git(repo_root, "ls-tree", "-r", base_ref, "--", OLD_DIR).splitlines()
    base_files = {}
    for line in listing:
        meta, path = line.split("\t", 1)
        base_files[path] = meta.split()[2]
    expected_new = set()
    for path, blob in sorted(base_files.items()):
        rel_in_pkg = path[len(OLD_DIR) + 1 :]
        new_path = f"{NEW_DIR}/{rel_in_pkg}"
        expected_new.add(new_path)
        target = repo_root / new_path
        if not target.exists():
            report.problems.append(f"missing after move: {new_path}")
            continue
        if path.endswith(".py"):
            base_tree = ast.parse(_git(repo_root, "show", f"{base_ref}:{path}"))
            base_tree = _Rewrite(_anchor_values(rel_in_pkg)).visit(base_tree)
            head_tree = ast.parse(target.read_text())
            if ast.dump(base_tree, include_attributes=False) != ast.dump(
                head_tree, include_attributes=False
            ):
                report.problems.append(f"AST differs: {new_path}")
            report.modules += 1
        else:
            head_blob = _git(repo_root, "hash-object", "--", new_path).strip()
            if head_blob != blob:
                report.problems.append(f"bytes differ: {new_path}")
            report.data_files += 1
    for path in sorted((repo_root / "src/comms").rglob("*")):
        if path.is_file() and "__pycache__" not in path.parts:
            rel = path.relative_to(repo_root).as_posix()
            if rel not in expected_new and rel not in NEW_FILES:
                report.problems.append(f"unexpected file: {rel}")
    return report


def protocol_constants(tree_root: Path) -> list[str]:
    found = []
    for path in sorted(tree_root.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.Constant) and isinstance(node.value, (str, bytes)):
                text = node.value.decode("latin-1") if isinstance(node.value, bytes) else node.value
                if _PROTOCOL.match(text):
                    found.append(repr(node.value))
    return sorted(found)


def main() -> int:
    import sys

    base = sys.argv[1] if len(sys.argv) > 1 else "HEAD"
    report = check(base, repo_root=Path.cwd())
    for problem in report.problems:
        print("FAIL", problem)
    print(f"{report.modules} modules and {report.data_files} data files checked; ok={report.ok}")
    return 0 if report.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
