"""comms 5b-4 P2/G30: every ``comms-*`` wire domain under src/comms is pinned by name.

The frozen-protocol collector matches only the Telegram prefixes, so ``comms-*``
domains are guarded here: the exact multiset, and — for core — one home.
"""

import ast
import sys
from collections import Counter
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src" / "comms"
CORE = SRC / "core"
DOMAINS = CORE / "domains.py"

PINNED = Counter({"b'comms-call-binding/v1\\x00'": 1})  # 5b-3, disclosure/binding
ADDED_IN_5B4: Counter[str] = Counter()


def _constants(path: Path) -> list[str]:
    found = []
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Constant) and isinstance(node.value, (str, bytes)):
            text = node.value.decode("latin-1") if isinstance(node.value, bytes) else node.value
            if text.startswith("comms-"):
                found.append(repr(node.value))
    return found


def _files(root: Path) -> list[Path]:
    return [p for p in sorted(root.rglob("*.py")) if "__pycache__" not in p.parts]


def test_comms_wire_domains_are_exactly_the_pinned_multiset():
    found = Counter(c for p in _files(SRC) for c in _constants(p))
    assert found == PINNED + ADDED_IN_5B4


def test_core_wire_domains_live_only_in_domains_py():
    for path in _files(CORE):
        if path != DOMAINS:
            assert _constants(path) == [], path


def test_the_guard_catches_a_planted_domain(tmp_path, monkeypatch):
    planted = tmp_path / "comms"
    (planted / "core").mkdir(parents=True)
    (planted / "core" / "x.py").write_text('D = b"comms-planted/v1\\0"\n')
    monkeypatch.setattr(sys.modules[__name__], "SRC", planted)
    monkeypatch.setattr(sys.modules[__name__], "CORE", planted / "core")
    with pytest.raises(AssertionError):
        test_comms_wire_domains_are_exactly_the_pinned_multiset()
    with pytest.raises(AssertionError):
        test_core_wire_domains_live_only_in_domains_py()
