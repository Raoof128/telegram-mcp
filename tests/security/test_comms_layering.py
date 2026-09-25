"""comms design rev 2 §2.2: the dependency direction is permanent from 5b-1."""

import ast
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
CORE = ROOT / "src" / "comms" / "core"
TELEGRAM = ROOT / "src" / "comms" / "transports" / "telegram"
WHATSAPP = ROOT / "transports" / "whatsapp" / "src" / "whatsvault"
LEGACY = ROOT / "src" / "telegram_mcp"
FORBIDDEN_FROM_CORE = (
    "comms.transports",
    "comms.services",  # comms v0.3 D2: services depend on core, never the reverse
    "comms.mcp",
    "comms.runtime",
    "telegram_mcp",
    "whatsvault",
)
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
    assert CORE.is_dir(), "comms.core must exist from 5b-1"
    return sorted(CORE.rglob("*.py"))


def test_core_is_transport_neutral():
    """5b-4 D2: core holds domain code; the guards below run over every core file."""
    files = _core_files()
    assert (CORE / "canonical.py") in files and (CORE / "opaque.py") in files
    test_core_never_imports_a_transport()
    test_core_has_no_dynamic_imports_or_transport_strings()


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
                assert not any(f in node.value for f in FORBIDDEN_FROM_CORE), (
                    f"{path}:{node.lineno}"
                )


def test_the_guards_catch_a_planted_violation(tmp_path, monkeypatch):
    """Review Focus #4: prove the dynamic-import guard is not vacuous."""
    planted = tmp_path / "core"
    planted.mkdir()
    (planted / "bad.py").write_text(
        'import importlib\nimportlib.import_module("comms.transports.telegram")\n'
    )
    monkeypatch.setattr(sys.modules[__name__], "CORE", planted)
    with pytest.raises(AssertionError):
        test_core_has_no_dynamic_imports_or_transport_strings()


def test_telegram_never_imports_another_transport():
    assert TELEGRAM.is_dir(), "the Telegram transport must exist; an empty walk proves nothing"
    for path in sorted(TELEGRAM.rglob("*.py")):
        for name in _imports(ast.parse(path.read_text())):
            assert not name.startswith("whatsvault"), f"{path}: {name}"


def test_whatsapp_never_imports_telegram():
    if not WHATSAPP.is_dir():
        pytest.skip("WhatsVault arrives in 5b-2; this guard is live from then on")
    for path in sorted(WHATSAPP.rglob("*.py")):
        for name in _imports(ast.parse(path.read_text())):
            assert not name.startswith(("comms.transports.telegram", "telegram_mcp")), (
                f"{path}: {name}"
            )


def test_the_legacy_package_is_only_a_forwarder():
    assert sorted(p.name for p in LEGACY.glob("*.py")) == ["__init__.py", "cli.py"]
    tree = ast.parse((LEGACY / "cli.py").read_text())
    kinds = [type(node).__name__ for node in tree.body]
    assert kinds == ["Expr", "ImportFrom", "Assign", "If"], (
        kinds
    )  # docstring, import, __all__, main guard
    imported = next(n for n in tree.body if isinstance(n, ast.ImportFrom))
    assert imported.module == "comms.transports.telegram.cli"
    assert [a.name for a in imported.names] == ["main"]
    init = ast.parse((LEGACY / "__init__.py").read_text())
    assert [type(n).__name__ for n in init.body] == ["Expr"]  # docstring only


CONSENT_WORDS = ("consent", "rendezvous", "pairing")


def test_no_consent_modules_remain():
    """comms spec v0.2 (5b-3 Task 9): the consent subsystem is deleted, not dormant."""
    src = ROOT / "src" / "comms"
    paths = [p for p in src.rglob("*.py") if "__pycache__" not in p.parts]
    named = [p for p in paths if any(w in part for part in p.parts for w in CONSENT_WORDS)]
    assert named == []
    for path in paths:
        imported = _imports(ast.parse(path.read_text(encoding="utf-8")))
        assert not [m for m in imported if any(w in m for w in CONSENT_WORDS)], path
