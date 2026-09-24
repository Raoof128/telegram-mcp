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
