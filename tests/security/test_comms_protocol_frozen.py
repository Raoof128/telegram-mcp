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


# comms spec v0.2 (5b-3) is the one semantic phase: it may add protocol
# identifiers and tombstone retired ones, each named here. Anything else that
# changes the multiset is drift and fails.
ADDED_IN_V0_2 = Counter({"'tg-mcp-disclosure/v2'": 1})
# Occurrences removed with consent. Tombstoned permanently: never reassigned.
TOMBSTONED_IN_V0_2 = Counter(
    {
        "'tg-mcp-exposure-snapshot/v1'": 1,  # disclosure/exposure.py (5b-3 Task 6)
        # consent/admin_approval.py (5b-3 Task 8): request-bound Touch ID tokens.
        "b'telegram-mcp-admin-request/v1\\x00'": 1,
        "b'telegram-mcp-admin-secret/v1\\x00'": 1,
        "b'telegram-mcp-sentinel/v1\\x00'": 1,
    }
)


def test_protocol_constants_are_the_baseline_multiset():
    baseline = Counter(json.loads(FIXTURE.read_text()))
    assert not (TOMBSTONED_IN_V0_2 - baseline), "only baseline identifiers can be tombstoned"
    expected = baseline - TOMBSTONED_IN_V0_2 + ADDED_IN_V0_2
    assert Counter(protocol_constants(ROOT / "src" / "comms")) == expected


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
                "test_comms_entry_points.py",  # names the legacy entry point on purpose
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
