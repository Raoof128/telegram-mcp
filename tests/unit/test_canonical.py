"""TG-JCS-v1 lives once, in canonical.py, byte-identical to the pre-extraction encoder."""

import json
import subprocess
from pathlib import Path

import pytest

from comms.core.canonical import jcs_dumps

ROOT = Path(__file__).resolve().parents[2]
VECTORS = ROOT / "tests" / "fixtures" / "canonical" / "jcs_vectors.json"
BASE = (ROOT / "tests" / "fixtures" / "canonical" / "BASE").read_text().strip()


def _old_encoder():
    source = subprocess.run(
        ["git", "show", f"{BASE}:src/comms/transports/telegram/consent/challenge.py"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    start, end = source.index("def _walk_canonicalizable("), source.index("def display_digest(")
    namespace: dict = {}
    exec("import json\nfrom typing import Any\n" + source[start:end], namespace)  # noqa: S102
    return namespace["jcs_dumps"]


OLD = _old_encoder()
FATAL = [1.5, {"k": float("nan")}, "\ud800", {"é": 1}, {1: 2}, {"a": [object()]}, (1, 2)]
EXTRA = [
    {"b": 1, "a": [True, False, None, 0, 2**63], "ç": "x"},
    {"z": '\u202e\x00"\\', "y": "🔍"},
    [],
    {},
    "",
    0,
]


def _outcome(fn, value):
    try:
        return fn(value)
    except Exception as exc:  # noqa: BLE001 -- the outcome under comparison includes failures
        return (type(exc).__name__, str(exc))


def test_the_frozen_corpus_is_byte_identical():
    corpus = json.loads(VECTORS.read_text())
    for case in corpus["cases"]:
        assert jcs_dumps(case["input"]).hex() == case["jcs_hex"], case["name"]
        for key in ("input", "display_input"):
            if key in case:
                assert jcs_dumps(case[key]) == OLD(case[key]), (case["name"], key)


@pytest.mark.parametrize("value", FATAL, ids=repr)
def test_fatal_inputs_fail_identically(value):
    new, old = _outcome(jcs_dumps, value), _outcome(OLD, value)
    assert new == old and isinstance(new, tuple)


@pytest.mark.parametrize("value", EXTRA, ids=repr)
def test_extra_inputs_are_identical(value):
    assert _outcome(jcs_dumps, value) == _outcome(OLD, value)


def test_there_is_exactly_one_jcs_implementation():
    src = ROOT / "src"
    hits = [p for p in src.rglob("*.py") if "def jcs_dumps(" in p.read_text()]
    assert [p.relative_to(ROOT).as_posix() for p in hits] == ["src/comms/core/canonical.py"]
