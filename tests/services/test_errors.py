"""comms v0.3 Task D2: the service error model (P §54) and the layering guard."""

import ast
import re
import sys
from pathlib import Path

import pytest

from comms.core.audit.integrity import AuditIntegrityDegraded
from comms.services.errors import ERROR_CODES, NAMED_ADDITIONS, CommsError
from comms.services.registry import ServiceRegistry

ROOT = Path(__file__).resolve().parents[2]
PROPOSAL = ROOT / "docs" / "provenance" / "comms-v0.3-proposal.md"
SERVICES = ROOT / "src" / "comms" / "services"


def _p54():
    text = PROPOSAL.read_text(encoding="utf-8")
    section = text[text.index("# 54. Errors") : text.index("# 55.")]
    block = section[
        section.index("```text") + 7 : section.index("```", section.index("```text") + 7)
    ]
    return {line.strip() for line in block.splitlines() if line.strip()}


def test_error_codes_are_exactly_p54_plus_named_additions():
    assert NAMED_ADDITIONS == {
        "REQUEST_ID_REUSE",
        "RETIRED_TOOL",
        "TOOL_NOT_FOUND",
        "AUDIT_INTEGRITY_DEGRADED",
    }
    assert ERROR_CODES == _p54() | NAMED_ADDITIONS
    assert len(_p54()) == 16


def test_error_messages_fixed_and_non_enumerating():
    messages = set()
    for code in ERROR_CODES:
        error = CommsError(code)
        assert error.code == code and str(error) and code not in str(error)
        messages.add(str(error))
    assert len(messages) == len(ERROR_CODES)  # one fixed text per code
    error = CommsError("NOT_FOUND", retry_after=None)
    assert str(error) == str(CommsError("NOT_FOUND"))
    limited = CommsError("RATE_LIMITED", retry_after=17)
    assert limited.retry_after == 17 and "17" not in str(
        limited
    )  # detail is structured, never text
    for bad in ("NOPE", "", None, "not_found"):
        with pytest.raises(ValueError):
            CommsError(bad)
    with pytest.raises(ValueError):
        CommsError("NOT_FOUND", retry_after=5)  # only a rate limit carries a wait


def test_the_registry_converts_every_failure_to_a_fixed_code():
    registry = ServiceRegistry()
    registry.register("demo.ok", lambda **kw: {"ok": kw})
    registry.register(
        "demo.refused", lambda **kw: (_ for _ in ()).throw(CommsError("NOT_AUTHORIZED"))
    )
    registry.register("demo.degraded", lambda **kw: (_ for _ in ()).throw(AuditIntegrityDegraded()))
    registry.register(
        "demo.bug", lambda **kw: (_ for _ in ()).throw(KeyError("+61400000001 secret"))
    )
    assert registry.call("demo.ok", x=1) == {"ok": {"x": 1}}
    for name, code in (
        ("demo.refused", "NOT_AUTHORIZED"),
        ("demo.degraded", "AUDIT_INTEGRITY_DEGRADED"),
        ("demo.bug", "INTERNAL_ERROR"),
        ("demo.missing", "TOOL_NOT_FOUND"),
    ):
        with pytest.raises(CommsError) as raised:
            registry.call(name)
        assert raised.value.code == code
        assert raised.value.__cause__ is None and raised.value.__context__ is None
        assert "+61400000001" not in str(raised.value)


_WRITE = re.compile(r"^\s*(INSERT|UPDATE|DELETE|REPLACE|CREATE|DROP|ALTER)\b", re.IGNORECASE)


def test_no_service_writes_sqlite_outside_core_apis():
    offenders = []
    for path in SERVICES.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import | ast.ImportFrom):
                names = (
                    [a.name for a in node.names]
                    if isinstance(node, ast.Import)
                    else [node.module or ""]
                )
                if any(
                    n.split(".")[0] in ("sqlite3", "sqlcipher3") or n == "comms.core.storage.db"
                    for n in names
                ):
                    offenders.append(f"{path.name}: imports {names}")
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr.startswith("execute")
            ):
                first = node.args[0] if node.args else None
                if (
                    isinstance(first, ast.Constant)
                    and isinstance(first.value, str)
                    and _WRITE.match(first.value)
                ):
                    offenders.append(f"{path.name}:{node.lineno}: writes SQL")
    assert offenders == []


def test_the_guard_catches_a_planted_write(tmp_path, monkeypatch):
    planted = tmp_path / "bad.py"
    planted.write_text('def f(conn):\n    conn.execute("DELETE FROM campaigns")\n')
    monkeypatch.setattr(sys.modules[__name__], "SERVICES", tmp_path)
    with pytest.raises(AssertionError):
        test_no_service_writes_sqlite_outside_core_apis()
