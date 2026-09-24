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
        # disclosure/exposure.py (5b-3 Task 6) and consent/challenge.py (Task 9).
        "'tg-mcp-exposure-snapshot/v1'": 2,
        # consent/admin_approval.py (5b-3 Task 8): request-bound Touch ID tokens.
        "b'telegram-mcp-admin-request/v1\\x00'": 1,
        "b'telegram-mcp-admin-secret/v1\\x00'": 1,
        "b'telegram-mcp-sentinel/v1\\x00'": 1,
        # 5b-3 Task 9: the consent subsystem.
        "'telegram-mcp-agent'": 2,  # runtime/bootstrap.py launchd label + stray marker
        "b'telegram-mcp-display-v1'": 1,  # consent/challenge.py display digest
        "b'telegram-mcp-rendezvous/v1'": 1,  # ipc/rendezvous.py RV-1 transcript
    }
)
# comms v0.3 (spec A2). The 16 tool names, `tgml1` issuance and the policy-bundle ids are
# tombstoned, but none changes this multiset: the tool names are not protocol literals,
# `tgml1` stays as the lease codec's prefix for verification (issuance is retired), and
# the policy-bundle ids never shipped. The comms domains v0.3 adds are pinned by
# test_comms_wire_frozen.ADDED_IN_V03. Both counters stay here so a later v0.3 change to
# the legacy wire has to be named.
TOMBSTONED_IN_V0_3: Counter[str] = Counter()
ADDED_IN_V0_3: Counter[str] = Counter()
# KEEP_LITERALS (the 5b-1 record) entries whose files were deleted with consent.
RETIRED_KEEP_LITERALS = {
    ("src/comms/transports/telegram/ipc/rendezvous.py", "telegram_mcp.rendezvous"),
    ("tests/unit/test_gate.py", "telegram_mcp.audit"),
}


def test_protocol_constants_are_the_baseline_multiset():
    baseline = Counter(json.loads(FIXTURE.read_text()))
    assert not (TOMBSTONED_IN_V0_2 - baseline), "only baseline identifiers can be tombstoned"
    expected = baseline - TOMBSTONED_IN_V0_2 + ADDED_IN_V0_2 - TOMBSTONED_IN_V0_3 + ADDED_IN_V0_3
    assert Counter(protocol_constants(ROOT / "src" / "comms")) == expected


def test_protocol_multiset_records_v03_changes():
    from tests.security.test_comms_wire_frozen import ADDED_IN_V03

    found = Counter(protocol_constants(ROOT / "src" / "comms"))
    assert found["'tgml1'"] >= 1  # the codec remains for verification; issuance is retired
    assert not any("policy-bundle" in k or "policy-signature" in k for k in found)
    assert not (TOMBSTONED_IN_V0_3 & ADDED_IN_V0_3)
    assert all(k.startswith("b'comms") for k in ADDED_IN_V03)


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
                "test_catalog_skeleton.py",  # forbids the legacy package as an import, on purpose
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
    assert RETIRED_KEEP_LITERALS <= set(KEEP_LITERALS)
    assert remaining == set(KEEP_LITERALS) - RETIRED_KEEP_LITERALS
