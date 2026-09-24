"""Rewrite one file for the 5b-1 move. Imports always; strings only by rule.

Rules, applied per string token (``tokenize``, so positions are exact):

1. Docstrings are never edited.
2. ``(file, value)`` in ``KEEP_LITERALS`` is never edited.
3. Resource anchors listed in ``RESOURCE_ANCHORS`` are replaced verbatim.
4. A value of exactly ``"telegram_mcp"``:
   * preceded by ``/``: a path segment, becomes ``"comms" / "transports" / "telegram"``;
   * the right-hand side of ``PACKAGE =``: becomes ``"comms.transports.telegram"``.
5. Any other value containing ``telegram_mcp``: ``src/telegram_mcp/`` becomes
   ``src/comms/transports/telegram/``, and ``telegram_mcp.`` becomes
   ``comms.transports.telegram.`` (module paths, including inside probe code).

Anything else that contains ``telegram_mcp`` raises ``UnclassifiedLiteral``
and nothing is written.
"""

from __future__ import annotations

import ast
import io
import re
import tokenize
from pathlib import Path

from migration.move_map import KEEP_LITERALS, NEW_DIR, OLD, OLD_DIR, RESOURCE_ANCHORS

# Authored for the post-move world (Task 2): never rewritten.
POST_MOVE_FILES = frozenset(
    {
        "test_migration_tools.py",
        "test_comms_layering.py",
        "test_comms_protocol_frozen.py",
        "test_comms_entry_points.py",
    }
)

_IMPORT_NAME = re.compile(r"(?<![\w.])telegram_mcp(?=[.\s,)]|$)")


class UnclassifiedLiteral(ValueError):
    pass


def _docstring_positions(tree: ast.AST) -> set[tuple[int, int]]:
    found = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = getattr(node, "body", [])
            if (
                body
                and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)
            ):
                found.add((body[0].value.lineno, body[0].value.col_offset))
    return found


def _import_lines(tree: ast.AST) -> set[int]:
    lines = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            lines.update(range(node.lineno, (node.end_lineno or node.lineno) + 1))
    return lines


def _new_token(text: str, value: str, prev: str, prev2: str) -> str:
    if value == OLD:
        if prev == "/":
            return '"comms" / "transports" / "telegram"'
        if prev == "=" and prev2 == "PACKAGE":
            return text.replace(OLD, "comms.transports.telegram")
        raise UnclassifiedLiteral(value)
    out = text.replace(OLD_DIR + "/", NEW_DIR + "/").replace(
        OLD + ".", "comms.transports.telegram."
    )
    if OLD in ast.literal_eval(out):
        raise UnclassifiedLiteral(value)
    return out


def rewrite_file(path: Path, *, repo_root: Path) -> bool:
    source = path.read_text()
    rel = path.resolve().relative_to(repo_root.resolve()).as_posix()
    tree = ast.parse(source)
    docstrings = _docstring_positions(tree)
    import_lines = _import_lines(tree)

    lines = source.splitlines(keepends=True)
    for index in sorted(import_lines):
        lines[index - 1] = _IMPORT_NAME.sub("comms.transports.telegram", lines[index - 1])
    text = "".join(lines)

    for rel_in_pkg, old, new in RESOURCE_ANCHORS:
        if rel.endswith("/" + rel_in_pkg) and (
            "/transports/telegram/" in rel or "/telegram_mcp/" in rel
        ):
            if text.count(old) == 1:
                text = text.replace(old, new)
            elif text.count(new) != 1:  # idempotent: already rewritten is fine
                raise UnclassifiedLiteral(f"{rel}: resource anchor not found exactly once")

    tokens = list(tokenize.generate_tokens(io.StringIO(text).readline))
    edits: list[tuple[int, int, int, int, str]] = []
    significant: list[tokenize.TokenInfo] = []
    for index, token in enumerate(tokens):
        following = tokens[index + 1].string if index + 1 < len(tokens) else ""
        if token.type == tokenize.NAME and token.string == OLD and following == ".":
            # `import telegram_mcp.x` binds the name `telegram_mcp`; code that
            # dereferences it must follow the rewritten import (F821 otherwise).
            edits.append((*token.start, *token.end, "comms.transports.telegram"))
        if (
            token.type == tokenize.STRING
            and OLD in token.string
            and token.start not in docstrings
            and not token.string.startswith(("f", "F", "rf", "fr"))
        ):
            value = ast.literal_eval(token.string)
            keep_key = (rel.replace(OLD_DIR + "/", NEW_DIR + "/"), value)
            if keep_key not in KEEP_LITERALS and (rel, value) not in KEEP_LITERALS:
                prev = significant[-1].string if significant else ""
                prev2 = significant[-2].string if len(significant) > 1 else ""
                try:
                    new = _new_token(token.string, value, prev, prev2)
                except UnclassifiedLiteral:
                    raise UnclassifiedLiteral(f"{rel}:{token.start[0]}: {value!r}") from None
                edits.append((*token.start, *token.end, new))
        if token.type not in (
            tokenize.NL,
            tokenize.NEWLINE,
            tokenize.COMMENT,
            tokenize.INDENT,
            tokenize.DEDENT,
        ):
            significant.append(token)

    if edits:
        lines = text.splitlines(keepends=True)
        for srow, scol, erow, ecol, new in sorted(edits, reverse=True):
            if srow != erow:
                raise UnclassifiedLiteral(f"{rel}:{srow}: multi-line literal")
            line = lines[srow - 1]
            lines[srow - 1] = line[:scol] + new + line[ecol:]
        text = "".join(lines)

    if text != source:
        path.write_text(text)
        return True
    return False


def main(argv: list[str] | None = None) -> int:
    import sys

    repo = Path.cwd()
    roots = (argv or sys.argv[1:]) or ["src", "tests", "scripts"]
    changed = 0
    for root in roots:
        for path in sorted((repo / root).rglob("*.py")):
            if "/migration/" in path.as_posix() or path.name in POST_MOVE_FILES:
                continue
            changed += rewrite_file(path, repo_root=repo)
    print(f"rewrote {changed} files")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
