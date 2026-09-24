# Comms 5b-1 — Rename and Restructure (Mechanical) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move every `telegram_mcp` module under `comms.transports.telegram` by pure prefix relocation, create an empty but guarded `comms.core`, keep a two-file `telegram_mcp` forwarder, and **prove** that nothing else changed.

**Architecture:** Three committed migration tools do the work:
- **a move map**, the single source of `telegram_mcp.X → comms.transports.telegram.X`;
- **a rewriter**, which edits only import statements and a closed, rule-classified set of string tokens;
- **an AST-equivalence checker**, which compares every moved module against the pre-move commit modulo that map and the two resource anchors, and requires every non-Python package file to be byte-identical.

The layering, protocol-string and entry-point tests are written RED before the move. The move turns them GREEN without touching behaviour.

**Tech Stack:** Python 3.12 stdlib (`ast`, `tokenize`, `subprocess`, `git`), uv, pytest, ruff, mypy, hatchling. No new dependency.

**Spec:** [`docs/superpowers/specs/2026-09-24-comms-consolidation-design.md`](../specs/2026-09-24-comms-consolidation-design.md), revision 2 (§1, §2, §0B). The frozen product spec v0.1.10 (SHA-256 `36b67f48…b0a`) still governs all Telegram semantics.

**Branch:** `comms-5b1`, cut from `main` at the commit carrying this plan.

## Global Constraints

- **No semantic change.** No handler, rule, default, error string, exit code, schema or behaviour changes. A commit whose explanation needs more than "import path moved" does not belong here.
- **Frozen identifiers keep their exact bytes:**
  - domain-separation constants;
  - `tgml1`, key-id formats and opaque-ref prefixes;
  - the ten tool names and `contracts/*.json`;
  - logger names (`telegram_mcp`, `telegram_mcp.admin`, `.audit`, `.sensitive`, `.daemon`, `.rendezvous`);
  - the context and scope keys (`telegram_mcp_operation`, `telegram_mcp_principal`, `telegram_mcp_rpc_phase`);
  - service accounts, runtime paths, socket names, the bundle ID, Keychain names and launchd labels.
- **`comms/core` contains no function or class definition.**
- **No module is split and no function is extracted.**
- **The `telegram-mcp` console script and `python -m telegram_mcp.cli` keep working.** `comms` is added as a second console script.
- **The distribution name stays `telegram-mcp`.** Renaming it is branding and waits for the out-of-repo rename step (design §5).
- **Fail closed:** a guard that cannot run reports a skip with a reason, never a pass.
- **Commit only on a green gate**, run fail-fast (no `;` chains).
- The full gate at the end is `uv sync --locked`, the contract check, `pytest`, smoke, formal, `ruff check`, `ruff format --check`, `mypy src/comms src/telegram_mcp` and `uv build`.
- Append dated `**Raouf:**` entries to `AGENT.md` and `CHANGELOG.md` at the end (Task 4).

## Review Focus

1. **A string that looks like a module path is actually an identifier.** Logger names, context keys and scope keys must survive byte-for-byte, even when they start with `telegram_mcp`. *Test: Task 2, `test_every_remaining_telegram_mcp_literal_is_a_frozen_identifier`.*
2. **A resource lookup still points at the old package.** `resources.files("telegram_mcp")` would load contracts from the forwarder, which has none, and the server would fail at import or at the first tool listing. *Test: Task 2, `test_contracts_load_from_the_moved_package`, plus the smoke.*
3. **A stale `.pyc` or editable-install path masks a broken import.** The installed console scripts must be regenerated from the edited `pyproject.toml`. *Test: Task 2, `test_console_scripts_resolve_to_comms`, run after `uv sync` in Task 3.*
4. **A future `core` module reaches a transport dynamically.** *Test: Task 2, `test_core_has_no_dynamic_imports_or_transport_strings`, which is proven to fail on a planted probe file.*
5. **A non-Python package file changes during `git mv`**, for example contract JSON line endings. *Test: the Task 1 checker compares git blob hashes of every non-Python file.*

## File Structure

| File | Task | Responsibility |
|---|---|---|
| `scripts/migration/move_map.py` (create) | 1 | `OLD`, `NEW`, `rewrite_module()`, `RESOURCE_ANCHORS`, `KEEP_LITERALS`, `NEW_FILES`. |
| `scripts/migration/rewrite.py` (create) | 1 | Rewrites import statements and rule-classified string tokens in place; reports unclassified literals and refuses. |
| `scripts/migration/ast_equivalence.py` (create) | 1 | Proves pre/post equivalence of every moved module; byte identity of non-Python files. |
| `tests/fixtures/migration/protocol_constants.json` (create) | 1 | Every protocol-shaped constant at the baseline. |
| `tests/unit/test_migration_tools.py` (create) | 1 | Tests for the three tools on synthetic trees. |
| `tests/security/test_comms_layering.py` (create) | 2 | Empty core, static and dynamic import direction, transport isolation, legacy forwarder shape. |
| `tests/security/test_comms_protocol_frozen.py` (create) | 2 | Protocol constants and remaining `telegram_mcp` literals, against the fixture. |
| `tests/integration/test_comms_entry_points.py` (create) | 2 | Console scripts, `python -m`, and contract loading. |
| `src/comms/**` (via `git mv`) | 3 | The relocated package, plus `comms/__init__.py`, `comms/core/__init__.py`, `comms/transports/__init__.py`. |
| `src/telegram_mcp/{__init__,cli}.py` (create after move) | 3 | The two-file forwarder. |
| `pyproject.toml`, `SECURITY-MANIFEST.json`, `.gitignore`, `CLAUDE.md` (modify) | 3–4 | Paths only. |
| `docs/verification/comms-5b1.md` (create) | 4 | Evidence. |

---

### Task 1: Migration tooling, with the baseline fixture captured before any move

**Files:**
- Create: `scripts/migration/__init__.py` (empty), `scripts/migration/move_map.py`, `scripts/migration/rewrite.py`, `scripts/migration/ast_equivalence.py`
- Create: `tests/fixtures/migration/protocol_constants.json`
- Test: `tests/unit/test_migration_tools.py`

**Interfaces:**
- Produces:
  - `move_map.rewrite_module(name: str) -> str`
  - `move_map.RESOURCE_ANCHORS: tuple[tuple[str, str, str], ...]` of `(path relative to the package, old text, new text)`
  - `move_map.KEEP_LITERALS: frozenset[tuple[str, str]]` of `(repo-relative file, literal value)`
  - `move_map.NEW_FILES: frozenset[str]`
  - `rewrite.rewrite_file(path, *, repo_root) -> bool`, which returns whether the file changed and raises `UnclassifiedLiteral`
  - `ast_equivalence.check(base_ref, *, repo_root) -> Report`, with `Report.ok`, `Report.modules` and `Report.problems`
  - `ast_equivalence.protocol_constants(tree_root) -> list[str]`

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/test_migration_tools.py
"""The 5b-1 migration tools on synthetic inputs (comms design §2.2-§2.4)."""

import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

from migration import ast_equivalence, move_map, rewrite


def test_rewrite_module_maps_only_the_package_prefix():
    assert move_map.rewrite_module("telegram_mcp") == "comms.transports.telegram"
    assert (
        move_map.rewrite_module("telegram_mcp.ipc.admin") == "comms.transports.telegram.ipc.admin"
    )
    assert move_map.rewrite_module("telegram_mcp_extra") == "telegram_mcp_extra"
    assert move_map.rewrite_module("os.path") == "os.path"


def _write(root: Path, rel: str, text: str) -> Path:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def test_imports_are_rewritten_and_logger_names_are_not(tmp_path):
    path = _write(
        tmp_path,
        "src/telegram_mcp/ipc/admin.py",
        "import logging\n"
        "from telegram_mcp.ipc.framing import (\n    read_frame,\n)\n"
        "import telegram_mcp.opaque as opaque\n"
        '_logger = logging.getLogger("telegram_mcp.admin")\n',
    )
    assert rewrite.rewrite_file(path, repo_root=tmp_path) is True
    text = path.read_text()
    assert "from comms.transports.telegram.ipc.framing import (" in text
    assert "import comms.transports.telegram.opaque as opaque" in text
    assert 'logging.getLogger("telegram_mcp.admin")' in text  # a frozen identifier


def test_test_literals_follow_the_classification_rules(tmp_path):
    path = _write(
        tmp_path,
        "tests/security/test_x.py",
        'SRC = ROOT / "src" / "telegram_mcp"\n'
        'PACKAGE = "telegram_mcp"\n'
        'TARGET = "telegram_mcp.telegram.reads.run_page"\n'
        'FILE = "src/telegram_mcp/contracts/a.json"\n'
        'PROBE = "import sys, telegram_mcp.server; print(1)"\n',
    )
    rewrite.rewrite_file(path, repo_root=tmp_path)
    text = path.read_text()
    assert 'ROOT / "src" / "comms" / "transports" / "telegram"' in text
    assert 'PACKAGE = "comms.transports.telegram"' in text
    assert '"comms.transports.telegram.telegram.reads.run_page"' in text
    assert '"src/comms/transports/telegram/contracts/a.json"' in text
    assert '"import sys, comms.transports.telegram.server; print(1)"' in text


def test_an_unclassified_literal_refuses(tmp_path):
    path = _write(tmp_path, "tests/unit/test_y.py", 'odd = f(x="telegram_mcp")\n')
    with pytest.raises(rewrite.UnclassifiedLiteral):
        rewrite.rewrite_file(path, repo_root=tmp_path)
    assert path.read_text() == 'odd = f(x="telegram_mcp")\n'  # nothing written on refusal


def _git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=root, check=True, capture_output=True, text=True
    ).stdout.strip()


@pytest.fixture
def repo(tmp_path):
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "t@example.com")
    _git(tmp_path, "config", "user.name", "t")
    _write(tmp_path, "src/telegram_mcp/__init__.py", '"""pkg."""\n')
    _write(
        tmp_path,
        "src/telegram_mcp/server.py",
        "from importlib import resources\n"
        "from telegram_mcp.opaque import mint\n"
        'M = resources.files("telegram_mcp") / "contracts"\n'
        "def f():\n    return mint()\n",
    )
    _write(tmp_path, "src/telegram_mcp/opaque.py", "def mint():\n    return 1\n")
    _write(tmp_path, "src/telegram_mcp/contracts/a.json", '{"a": 1}\n')
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-qm", "base")
    return tmp_path


def _move(root: Path) -> None:
    (root / "src/comms/transports").mkdir(parents=True)
    _git(root, "mv", "src/telegram_mcp", "src/comms/transports/telegram")
    for rel in (
        "src/comms/__init__.py",
        "src/comms/core/__init__.py",
        "src/comms/transports/__init__.py",
    ):
        _write(root, rel, '"""pkg."""\n')
    for path in sorted((root / "src/comms").rglob("*.py")):
        rewrite.rewrite_file(path, repo_root=root)


def test_a_faithful_move_is_equivalent(repo):
    _move(repo)
    report = ast_equivalence.check("HEAD", repo_root=repo)
    assert report.ok, report.problems
    assert report.modules == 3


def test_a_semantic_edit_is_caught(repo):
    _move(repo)
    target = repo / "src/comms/transports/telegram/opaque.py"
    target.write_text("def mint():\n    return 2\n")
    report = ast_equivalence.check("HEAD", repo_root=repo)
    assert not report.ok
    assert any("opaque.py" in p for p in report.problems)


def test_a_changed_data_file_is_caught(repo):
    _move(repo)
    (repo / "src/comms/transports/telegram/contracts/a.json").write_text('{"a": 2}\n')
    report = ast_equivalence.check("HEAD", repo_root=repo)
    assert not report.ok and any("a.json" in p for p in report.problems)


def test_an_extra_module_is_caught(repo):
    _move(repo)
    _write(repo, "src/comms/core/helpers.py", "def g():\n    return 0\n")
    report = ast_equivalence.check("HEAD", repo_root=repo)
    assert not report.ok and any("helpers.py" in p for p in report.problems)


def test_protocol_constants_are_collected(tmp_path):
    _write(
        tmp_path,
        "m.py",
        'A = b"telegram-mcp-audit-v1"\nB = "tg-mcp-grant/v1"\nC = "tgml1"\nD = "other"\n',
    )
    assert ast_equivalence.protocol_constants(tmp_path) == [
        "'tg-mcp-grant/v1'",
        "'tgml1'",
        "b'telegram-mcp-audit-v1'",
    ]
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/unit/test_migration_tools.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'migration'`.

- [ ] **Step 3: Implement `scripts/migration/move_map.py`**

```python
"""The single source of the 5b-1 move (comms design rev 2, §2.2).

Everything the rewriter and the equivalence checker know about the move
lives here, so the two tools cannot disagree.
"""

from __future__ import annotations

OLD = "telegram_mcp"
NEW = "comms.transports.telegram"
OLD_DIR = "src/telegram_mcp"
NEW_DIR = "src/comms/transports/telegram"

# Package-resource anchors: string arguments that name the package so that
# importlib.resources finds the data files. They must follow the files.
# (path relative to the package, old text, new text)
RESOURCE_ANCHORS: tuple[tuple[str, str, str], ...] = (
    ("server.py", 'resources.files("telegram_mcp")', 'resources.files("comms.transports.telegram")'),
    ("contract.py", 'resources.files("telegram_mcp")', 'resources.files("comms.transports.telegram")'),
)

# Frozen identifiers (design §2.1): literals containing "telegram_mcp" that
# are NOT module paths and must keep their bytes. (repo-relative file, value)
KEEP_LITERALS: frozenset[tuple[str, str]] = frozenset(
    {
        ("src/comms/transports/telegram/cli.py", "telegram_mcp"),
        ("src/comms/transports/telegram/observability/logging.py", "telegram_mcp"),
        ("src/comms/transports/telegram/audit_seam.py", "telegram_mcp.audit"),
        ("src/comms/transports/telegram/sensitive_dispatch.py", "telegram_mcp.sensitive"),
        ("src/comms/transports/telegram/runtime/daemon.py", "telegram_mcp.daemon"),
        ("src/comms/transports/telegram/ipc/rendezvous.py", "telegram_mcp.rendezvous"),
        ("src/comms/transports/telegram/ipc/admin.py", "telegram_mcp.admin"),
        ("src/comms/transports/telegram/ipc/handlers/_wrapper.py", "telegram_mcp.admin"),
        ("src/comms/transports/telegram/runtime/ingress.py", "telegram_mcp_principal"),
        ("src/comms/transports/telegram/telegram/telethon_adapter.py", "telegram_mcp_operation"),
        ("tests/unit/test_gate.py", "telegram_mcp.audit"),
        ("tests/security/test_redaction.py", "telegram_mcp"),
        ("tests/security/test_redaction.py", "telegram_mcp.test.filter.probe"),
        ("tests/telegram/recorder.py", "telegram_mcp_rpc_phase"),
    }
)

# The only new .py files the move may add under src/comms.
NEW_FILES: frozenset[str] = frozenset(
    {
        "src/comms/__init__.py",
        "src/comms/core/__init__.py",
        "src/comms/transports/__init__.py",
    }
)


def rewrite_module(name: str) -> str:
    if name == OLD or name.startswith(OLD + "."):
        return NEW + name[len(OLD) :]
    return name
```

Docstrings that mention `telegram_mcp` in prose (`audit_seam.py:1`, `budget.py:1`, `db.py:254`, `refs.py:1`, `test_keys.py:1`, `test_join_gate.py:1`, `stub_broker.py:1`) are **docstrings**. The rewriter recognises them as the first statement of a module, class or function, and never edits them. Leaving prose untouched keeps the AST identical. Task 4 updates `stub_broker.py`'s docstring paths as documentation, in a commit separate from the move.

- [ ] **Step 4: Implement `scripts/migration/rewrite.py`**

```python
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
```

The `_docstring_positions` set records `(lineno, col_offset)`. A `tokenize` token's `start` for the same string is `(lineno, col_offset)` too, so the membership test is exact.

- [ ] **Step 5: Implement `scripts/migration/ast_equivalence.py`**

```python
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
            values.append((ast.literal_eval(old.split("(", 1)[1][:-1]), ast.literal_eval(new.split("(", 1)[1][:-1])))
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
```

`protocol_constants` returns `repr` strings sorted. The fixture test in Task 2 compares the **multiset**, so a duplicated or dropped constant is visible.

- [ ] **Step 6: Run the tests to verify they pass**

Run: `uv run pytest tests/unit/test_migration_tools.py -q`
Expected: 9 passed.

- [ ] **Step 7: Capture the baseline protocol fixture (pre-move)**

```bash
uv run python -c "
import json, sys
sys.path.insert(0, 'scripts')
from pathlib import Path
from migration.ast_equivalence import protocol_constants
Path('tests/fixtures/migration').mkdir(parents=True, exist_ok=True)
Path('tests/fixtures/migration/protocol_constants.json').write_text(
    json.dumps(protocol_constants(Path('src/telegram_mcp')), indent=1) + '\n')
"
uv run python -c "import json; print(len(json.load(open('tests/fixtures/migration/protocol_constants.json'))))"
```

Expected: `29`, measured in the dry run at `337748d`. Record the count in the commit message.

- [ ] **Step 8: Gate and commit**

Run: `uv run ruff check scripts tests && uv run ruff format --check scripts tests && uv run pytest -q`
Expected: green; full suite `1502 passed, 10 skipped` (1493 + 9).

```bash
git add scripts/migration tests/unit/test_migration_tools.py tests/fixtures/migration
git commit -m "chore: 5b-1 migration tools (move map, rewriter, AST equivalence) and baseline protocol fixture"
```

---

### Task 2: The contract tests, written RED before the move

**Files:**
- Create: `tests/security/test_comms_layering.py`
- Create: `tests/security/test_comms_protocol_frozen.py`
- Create: `tests/integration/test_comms_entry_points.py`

**Interfaces:**
- Consumes: `move_map.KEEP_LITERALS` and `ast_equivalence.protocol_constants` (Task 1).
- Produces: the executable form of design §2.1–§2.3.

- [ ] **Step 1: Write the tests**

```python
# tests/security/test_comms_layering.py
"""comms design rev 2 §2.2: the dependency direction is permanent from 5b-1."""

import ast
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
CORE = ROOT / "src" / "comms" / "core"
TELEGRAM = ROOT / "src" / "comms" / "transports" / "telegram"
WHATSAPP = ROOT / "transports" / "whatsapp" / "src" / "whatsvault"
LEGACY = ROOT / "src" / "telegram_mcp"
FORBIDDEN_FROM_CORE = ("comms.transports", "telegram_mcp", "whatsvault")
DYNAMIC = {"import_module", "__import__"}


def _imports(tree: ast.AST) -> set[str]:
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


def _core_files() -> list[Path]:
    assert CORE.is_dir(), "comms.core must exist (empty) from 5b-1"
    return sorted(CORE.rglob("*.py"))


def test_core_has_no_production_implementation():
    for path in _core_files():
        for node in ast.walk(ast.parse(path.read_text())):
            assert not isinstance(
                node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
            ), f"{path}: core is empty until 5b-2's first seam"


def test_core_never_imports_a_transport():
    for path in _core_files():
        for name in _imports(ast.parse(path.read_text())):
            assert not name.startswith(FORBIDDEN_FROM_CORE), f"{path}: {name}"


def test_core_has_no_dynamic_imports_or_transport_strings():
    for path in _core_files():
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.Call):
                name = getattr(node.func, "attr", None) or getattr(node.func, "id", None)
                assert name not in DYNAMIC, f"{path}:{node.lineno} dynamic import"
            if isinstance(node, ast.Attribute) and node.attr == "util":
                assert getattr(node.value, "id", None) != "importlib", f"{path}:{node.lineno}"
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                assert not any(f in node.value for f in FORBIDDEN_FROM_CORE), f"{path}:{node.lineno}"


def test_the_guards_catch_a_planted_violation(tmp_path, monkeypatch):
    """Review Focus #4: prove the dynamic-import guard is not vacuous."""
    planted = tmp_path / "core"
    planted.mkdir()
    (planted / "bad.py").write_text('import importlib\nimportlib.import_module("comms.transports.telegram")\n')
    monkeypatch.setattr(__import__(__name__), "CORE", planted)
    with pytest.raises(AssertionError):
        test_core_has_no_dynamic_imports_or_transport_strings()


def test_telegram_never_imports_another_transport():
    for path in sorted(TELEGRAM.rglob("*.py")):
        for name in _imports(ast.parse(path.read_text())):
            assert not name.startswith("whatsvault"), f"{path}: {name}"


def test_whatsapp_never_imports_telegram():
    if not WHATSAPP.is_dir():
        pytest.skip("WhatsVault arrives in 5b-2; this guard is live from then on")
    for path in sorted(WHATSAPP.rglob("*.py")):
        for name in _imports(ast.parse(path.read_text())):
            assert not name.startswith(("comms.transports.telegram", "telegram_mcp")), f"{path}: {name}"


def test_the_legacy_package_is_only_a_forwarder():
    assert sorted(p.name for p in LEGACY.glob("*.py")) == ["__init__.py", "cli.py"]
    tree = ast.parse((LEGACY / "cli.py").read_text())
    kinds = [type(node).__name__ for node in tree.body]
    assert kinds == ["Expr", "ImportFrom", "Assign", "If"], kinds  # docstring, import, __all__, main guard
    imported = next(n for n in tree.body if isinstance(n, ast.ImportFrom))
    assert imported.module == "comms.transports.telegram.cli"
    assert [a.name for a in imported.names] == ["main"]
    init = ast.parse((LEGACY / "__init__.py").read_text())
    assert [type(n).__name__ for n in init.body] == ["Expr"]  # docstring only
```

```python
# tests/security/test_comms_protocol_frozen.py
"""comms design §2.1: protocol and identifier bytes survive the move."""

import ast
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from migration.ast_equivalence import protocol_constants
from migration.move_map import KEEP_LITERALS

FIXTURE = ROOT / "tests" / "fixtures" / "migration" / "protocol_constants.json"


def test_protocol_constants_are_the_baseline_multiset():
    baseline = json.loads(FIXTURE.read_text())
    assert Counter(protocol_constants(ROOT / "src" / "comms")) == Counter(baseline)


def _docstrings(tree: ast.AST) -> set[int]:
    ids = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = getattr(node, "body", [])
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
                ids.add(id(body[0].value))
    return ids


def test_every_remaining_telegram_mcp_literal_is_a_frozen_identifier():
    """Review Focus #1: only KEEP_LITERALS (logger, context, scope names) remain."""
    remaining = set()
    for root in ("src/comms", "tests", "scripts"):
        for path in sorted((ROOT / root).rglob("*.py")):
            if "migration" in path.parts or path.name in {
                "test_migration_tools.py",
                "test_comms_layering.py",
                "test_comms_protocol_frozen.py",
            }:
                continue
            tree = ast.parse(path.read_text())
            docs = _docstrings(tree)
            for node in ast.walk(tree):
                if (
                    isinstance(node, ast.Constant)
                    and isinstance(node.value, str)
                    and "telegram_mcp" in node.value
                    and id(node) not in docs
                ):
                    remaining.add((path.relative_to(ROOT).as_posix(), node.value))
    assert remaining == set(KEEP_LITERALS)
```

```python
# tests/integration/test_comms_entry_points.py
"""comms design §2.3: the compatibility surface, measured through real processes."""

import subprocess
import sys
from pathlib import Path

BIN = Path(sys.executable).parent


def _run(*argv: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(list(argv), capture_output=True, text=True, timeout=60, check=False)


def test_python_dash_m_legacy_cli_still_works():
    done = _run(sys.executable, "-m", "telegram_mcp.cli", "--help")
    assert done.returncode == 0, done.stderr
    assert "status" in done.stdout


def test_console_scripts_resolve_to_comms():
    """Review Focus #3: both scripts regenerated from pyproject, same main."""
    for script in ("telegram-mcp", "comms"):
        done = _run(str(BIN / script), "--help")
        assert done.returncode == 0, (script, done.stderr)
        assert "status" in done.stdout


def test_contracts_load_from_the_moved_package():
    """Review Focus #2: the resource anchor followed the files."""
    probe = (
        "from comms.transports.telegram.contract import load_contracts;"
        "print(len(load_contracts()))"
    )
    done = _run(sys.executable, "-c", probe)
    assert done.returncode == 0, done.stderr
    assert done.stdout.strip() == "10"


def test_the_package_is_importable_under_both_names():
    done = _run(sys.executable, "-c", "import comms.core, comms.transports.telegram.server, telegram_mcp.cli")
    assert done.returncode == 0, done.stderr
```

Before running, confirm the contract loader's public name with `grep -n "^def " src/telegram_mcp/contract.py`. If the loader that reads `manifest.json` (`contract.py:125`) is not called `load_contracts`, use the shipped name in the probe and record a ruling. The assertion (ten tools) is what matters.

- [ ] **Step 2: Run to verify they fail for the right reason**

Run: `uv run pytest tests/security/test_comms_layering.py tests/security/test_comms_protocol_frozen.py tests/integration/test_comms_entry_points.py -q`
Expected: FAIL. `comms.core must exist`, the empty `Counter` differs from the baseline, `comms` / `comms.core` is not importable, `telegram_mcp` has more than two files, and the `comms` script is missing.

`test_python_dash_m_legacy_cli_still_works` **passes** already, because the old package still exists. That is expected, and it must still pass after the move. `test_whatsapp_never_imports_telegram` skips with its reason.

- [ ] **Step 3: Commit the RED tests on their own**

The suite is red **by design** here, so this is the one commit exempt from "commit only on a green gate". The exemption is recorded in the ledger and the commit message. Everything else in the gate must be green.

```bash
uv run ruff check tests && uv run ruff format --check tests
git add tests/security/test_comms_layering.py tests/security/test_comms_protocol_frozen.py tests/integration/test_comms_entry_points.py
git commit -m "test: 5b-1 contract tests (RED until the move): layering, frozen protocol, entry points"
```

---

### Task 3: The move

**Files:**
- Move: `src/telegram_mcp/**` to `src/comms/transports/telegram/**` (`git mv`)
- Create: `src/comms/__init__.py`, `src/comms/core/__init__.py`, `src/comms/transports/__init__.py`, `src/telegram_mcp/__init__.py`, `src/telegram_mcp/cli.py`
- Rewrite (by tool): every `.py` under `src/comms`, `tests`, `scripts`
- Modify: `pyproject.toml`, `SECURITY-MANIFEST.json`, `.gitignore`

- [ ] **Step 1: Record BASE and move**

```bash
BASE=$(git rev-parse HEAD)          # Task 2's commit: the equivalence baseline
mkdir -p src/comms/transports
git mv src/telegram_mcp src/comms/transports/telegram
find src/comms -name __pycache__ -prune -exec rm -rf {} +
```

- [ ] **Step 2: Create the three package markers and the forwarder**

```python
# src/comms/__init__.py
"""comms: one operator-controlled communications system (comms design, 2026-09-24)."""
```

```python
# src/comms/core/__init__.py
"""Shared primitives. Empty by design until 5b-2's first proven seam (comms design rev 2, §2.2)."""
```

```python
# src/comms/transports/__init__.py
"""Transport subsystems. One transport never imports another (comms design rev 2, §2.2)."""
```

```python
# src/telegram_mcp/__init__.py
"""Legacy entry points only; the implementation lives in comms (comms design §2.3)."""
```

```python
# src/telegram_mcp/cli.py
"""`python -m telegram_mcp.cli` keeps working (comms design §2.3). No logic here."""

from comms.transports.telegram.cli import main

__all__ = ["main"]

if __name__ == "__main__":
    main()
```

- [ ] **Step 3: Run the rewriter**

```bash
PYTHONPATH=scripts uv run python -m migration.rewrite src/comms tests scripts
PYTHONPATH=scripts uv run python -m migration.rewrite src/comms tests scripts   # idempotence: must print "rewrote 0 files"
uv run ruff check --select I --fix src tests scripts   # longer paths re-wrap import lines
uv run ruff format src tests scripts                    # longer paths re-wrap long lines
```

Expected, as measured in the dry run: `rewrote 74 files`, then `rewrote 0 files`, with no `UnclassifiedLiteral`. The sort and format passes may only re-wrap lines. Step 5's order-sensitive AST check proves they reordered nothing. If it raises, the literal it names is new information. Classify it in `move_map.py` (a frozen identifier goes in `KEEP_LITERALS`; a module or file path needs a rule) with a ledger ruling, then re-run from Step 1 on a clean checkout of BASE. Never hand-edit a file to make the tool pass.

- [ ] **Step 4: Update build and manifest paths**

In `pyproject.toml`:

```toml
[project.scripts]
telegram-mcp = "comms.transports.telegram.cli:main"
comms = "comms.transports.telegram.cli:main"

[tool.hatch.build.targets.wheel]
packages = ["src/comms", "src/telegram_mcp"]
```

In `SECURITY-MANIFEST.json`, change `"implementation": "telegram_mcp.consent.challenge.jcs_dumps"` to `"implementation": "comms.transports.telegram.consent.challenge.jcs_dumps"`. This is the one path field the design exempts.

In `.gitignore`, change the comment `src/telegram_mcp/runtime/ is source` to `src/comms/transports/telegram/runtime/ is source`. The rule `/runtime/` is unchanged.

In `scripts/extract_contracts.py`, the rewriter already turned `"src" / "telegram_mcp"` into `"src" / "comms" / "transports" / "telegram"`. Confirm with `grep -n CONTRACTS_DIR scripts/extract_contracts.py`.

- [ ] **Step 5: Regenerate the environment and prove equivalence**

```bash
uv sync --locked || uv lock && uv sync --locked
find . -name __pycache__ -path "./src/*" -prune -exec rm -rf {} +
PYTHONPATH=scripts uv run python -m migration.ast_equivalence "$BASE"
```

Expected, as measured in the dry run: `96 modules and 23 data files checked; ok=True`. The module count equals `git ls-tree -r $BASE -- src/telegram_mcp | grep -c '\.py$'`.

`uv lock` is needed only if hatch's package list changes the lock's project entry. If `uv lock` changes any **third-party** pin, stop: that is not mechanical. Record the finding, and revert the lock.

- [ ] **Step 6: Run the contract tests (now GREEN), then the full gate**

Run: `uv run pytest tests/security/test_comms_layering.py tests/security/test_comms_protocol_frozen.py tests/integration/test_comms_entry_points.py tests/unit/test_migration_tools.py -q`
Expected: all pass, and `test_whatsapp_never_imports_telegram` skips with its reason.

Then run the full gate, fail-fast, one command at a time:

```bash
uv sync --locked
uv run python scripts/extract_contracts.py --check
uv run pytest -q
uv run python scripts/e2e_smoke.py
uv run pytest tests/formal -q -s
uv run ruff check src tests scripts
uv run ruff format --check src tests scripts
uv run mypy src/comms src/telegram_mcp
uv build
```

Expected:
- pytest: `1503 + 11 = 1514 passed, 11 skipped`. That is Task 1's 10 tests, plus 11 of Task 2's 12 passing and one skip. The previous 10 skips are unchanged.
- smoke 60/60; formal 624 states / 18 assertions; the build produces `telegram_mcp-0.1.10` artifacts, because the distribution name is unchanged.

Any other difference is a finding.

- [ ] **Step 7: Commit**

```bash
git add -A src tests scripts pyproject.toml uv.lock SECURITY-MANIFEST.json .gitignore
git status --short   # review: only moves, rewrites, the five new files, and the four path edits
git commit -m "refactor: relocate telegram_mcp under comms.transports.telegram (mechanical; AST-equivalent to $BASE)"
```

---

### Task 4: Evidence, documentation paths, audit trail

**Files:**
- Create: `docs/verification/comms-5b1.md`
- Modify: `CLAUDE.md` (Map table paths, the `mypy` command, the status paragraph), `tests/agent/stub_broker.py` (docstring paths only)
- Modify: `AGENT.md`, `CHANGELOG.md`

- [ ] **Step 1: Update documentation paths**

In `CLAUDE.md`:
- change every `src/telegram_mcp/...` and bare module path in the Map table to `src/comms/transports/telegram/...`;
- change the Verify block's `uv run mypy src/telegram_mcp` to `uv run mypy src/comms src/telegram_mcp` and its test count to `1514 passed, 11 skipped`;
- add one status sentence: "**5b-1 is on the `comms-5b1` branch**: Telegram lives at `comms.transports.telegram`; `comms.core` is empty and guarded; `telegram-mcp` and `comms` CLIs both work."

In `tests/agent/stub_broker.py`, change the docstring references `telegram_mcp.ipc.rendezvous.serve_rendezvous` and `telegram_mcp.consent.broker.ConsentBroker` to their `comms.transports.telegram.` paths. This is documentation only, and it gets its own commit.

- [ ] **Step 2: Write `docs/verification/comms-5b1.md`**

It must contain:
- BASE (Task 2's commit) and the move commit;
- the `ast_equivalence.py` output line (module and data-file counts, `ok=True`);
- the protocol-constant count from the fixture;
- the full gate output line for each command;
- the `KEEP_LITERALS` table, with each entry's role (logger, context key, scope key);
- the named compatibility surface (§2.3) and the test proving each item;
- the statement that no semantic change was made, and that the proof is AST equivalence, not review.

- [ ] **Step 3: Audit trail, gate and commit**

Append dated `**Raouf:**` entries to `AGENT.md` and `CHANGELOG.md`, with Scope, Summary, Files changed, Verification (exact counts) and Follow-ups (5b-2).

Re-run the full gate (Task 3 Step 6); every command exits 0.

```bash
git add docs/verification/comms-5b1.md CLAUDE.md tests/agent/stub_broker.py AGENT.md CHANGELOG.md
git commit -m "docs: record comms 5b-1 (mechanical relocation, AST-equivalence evidence)"
```

---

## Revision 2: dry-run gauntlet (the whole plan executed in a throwaway worktree at `337748d`)

The first draft was run end to end before anyone executed it. Eight defects were found, and every fix is in the code above.

| # | Defect | Found by | Fix |
|---|---|---|---|
| R1 | `test_protocol_constants_are_collected` expected unsorted order, and the text said "correct it" | test failure | expected list sorted in the code |
| R2 | `python scripts/migration/rewrite.py` cannot `import migration` (`sys.path[0]` is `scripts/migration`) | `ModuleNotFoundError` | invoke as `PYTHONPATH=scripts python -m migration.X` |
| R3 | `UnclassifiedLiteral` did not say which file or line | an unusable error | re-raised with `file:line: value` |
| R4 | The rewriter was not idempotent: an anchor already rewritten made re-runs refuse | second run | an already-rewritten anchor is accepted |
| R5 | The rewriter processed the post-move contract tests and would have corrupted them (`LEGACY = … / "telegram_mcp"`) | `test_comms_layering.py:13` refused | `POST_MOVE_FILES` excluded |
| R6 | `import telegram_mcp.config` followed by `telegram_mcp.config.__dict__` in code: rewriting the import rebinds the name, giving F821 | ruff F821 in `test_demo_isolation.py:19` | NAME-token rule: `telegram_mcp.` in code follows the import |
| R7 | The longer module path overflows lines: 33 I001 and 34 files to reformat | ruff | `ruff check --select I --fix` plus `ruff format` before the AST check, which proved no reorder (still `ok=True`) |
| R8 | Plan code lint: unused `# noqa: E402` (RUF100); nested ifs (SIM102) | ruff | removed; flattened |

**Dry-run result after the fixes:**
- rewriter: 74 files rewritten, then 0 on re-run;
- AST equivalence: 96 modules and 23 data files, `ok=True`;
- `uv sync --locked` passes, and `uv.lock` is unchanged;
- contract tests: 21 passed, 1 skipped;
- full suite: `1514 passed, 11 skipped`; smoke 60/60; formal 624/18;
- mypy clean on 101 files; ruff and format clean; build OK.

## Self-review (done while writing)

- **Spec coverage:**
  - design §2.1 → `KEEP_LITERALS`, the protocol fixture test and the AST checker;
  - §2.2 → Task 2 layering guards (empty core, static and dynamic imports, transport strings, transport isolation, and the planted-violation proof);
  - §2.3 → the forwarder plus entry-point tests;
  - §2.4 → Task 1 checker plus Task 3 Step 5;
  - §2.5 exit criteria 1–7 → Task 3 Step 6 plus Task 4.
- **Measured before writing** (at `337748d`):
  - every string literal containing `telegram_mcp` in `src`, `tests` and `scripts` was classified (16 in src, 23 in tests, 1 in scripts);
  - the two resource anchors are `server.py:28` and `contract.py:125`;
  - no dynamic imports exist in `src`;
  - outside the package, only `pyproject.toml` (3 lines), `SECURITY-MANIFEST.json` (1 field), `.gitignore` (1 comment) and `CLAUDE.md` reference the old path.
- **Known exemption:** Task 2's commit is red by design (RED-first for a move that cannot be split). It is recorded in its commit message and the ledger.
- **Placeholders:** none. Two steps tell the implementer to confirm a shipped name (the contract loader in Task 2, `CONTRACTS_DIR` in Task 3) with the exact grep, and give the rule for a mismatch.
