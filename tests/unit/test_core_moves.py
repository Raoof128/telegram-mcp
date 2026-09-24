"""comms 5b-4 Task 1: TG-JCS-v1 and the opaque-ref minter live once, in comms.core."""

import ast
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
CORE = SRC / "comms" / "core"
SINGLE_COPY = {"jcs_dumps": "canonical.py", "mint_opaque_ref": "opaque.py"}


def test_canonical_and_opaque_are_single_copies_in_core():
    from comms.core.canonical import jcs_dumps
    from comms.core.opaque import mint_opaque_ref

    assert callable(jcs_dumps) and callable(mint_opaque_ref)
    for name in ("canonical.py", "opaque.py"):
        assert not (SRC / "comms" / "transports" / "telegram" / name).exists(), name
    for path in sorted(SRC.rglob("*.py")):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.FunctionDef) and node.name in SINGLE_COPY:
                assert path == CORE / SINGLE_COPY[node.name], (path, node.name)


def test_core_canonical_is_byte_identical_to_the_pinned_base():
    """The BASE oracle in test_canonical.py now runs against the core copy."""
    tree = ast.parse((ROOT / "tests" / "unit" / "test_canonical.py").read_text(encoding="utf-8"))
    sources = {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
    assert "comms.core.canonical" in sources
    spec = importlib.util.spec_from_file_location(
        "_canonical_oracle", ROOT / "tests" / "unit" / "test_canonical.py"
    )
    assert spec is not None and spec.loader is not None
    oracle = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(oracle)
    oracle.test_the_frozen_corpus_is_byte_identical()
