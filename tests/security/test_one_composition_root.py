"""D39-PRE Task E4: the daemon and the smoke build the runtime through one root.

No third hand-built runtime: only ``comms/runtime/assemble.py`` calls ``build_comms_runtime``
in production code or the smoke (unit tests of the builder itself may call it).
"""

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SMOKE = ROOT / "scripts" / "e2e_smoke.py"
DAEMON = ROOT / "src" / "comms" / "transports" / "telegram" / "runtime" / "daemon.py"
SERVE = ROOT / "src" / "comms" / "runtime" / "serve.py"


def _calls(path, name):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return [
        n
        for n in ast.walk(tree)
        if isinstance(n, ast.Call) and getattr(n.func, "id", getattr(n.func, "attr", None)) == name
    ]


def test_only_assemble_calls_build_comms_runtime():
    callers = [p for p in (ROOT / "src").rglob("*.py") if _calls(p, "build_comms_runtime")]
    assert [p.relative_to(ROOT).as_posix() for p in callers] == ["src/comms/runtime/assemble.py"]
    assert not _calls(SMOKE, "build_comms_runtime")


def test_the_smoke_uses_assemble_runtime():
    assert _calls(SMOKE, "assemble_runtime")


def test_the_daemon_uses_assemble_runtime():
    """E6: the daemon starts the comms side through CommsServer, which calls the one root."""
    assert _calls(DAEMON, "CommsServer")
    assert _calls(SERVE, "assemble_runtime")
