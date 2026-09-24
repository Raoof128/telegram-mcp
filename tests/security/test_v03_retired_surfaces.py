"""comms v0.3 Task A14: the Telegram MCP surface is out of production composition (A1, A3; design A.3)."""

import ast
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

from tests.security.import_closure import closure

ROOT = Path(__file__).resolve().parents[2]
T = "comms.transports.telegram."
RETIRED_MCP = {T + "server", T + "dispatch", T + "sensitive_dispatch", T + "runtime.ingress"}
# The console scripts' target and the daemon entry are the production roots.
PRODUCTION_ROOTS = (T + "cli", T + "runtime.daemon")


def _cli(*args):
    return subprocess.run(
        [sys.executable, "-m", "comms.transports.telegram.cli", *args],
        capture_output=True,
        text=True,
        cwd=ROOT,
        check=False,
        timeout=60,
    )


@pytest.mark.parametrize("verb", ["serve", "demo"])
def test_telegram_mcp_serve_refuses(verb):
    done = _cli(verb)
    assert done.returncode != 0
    assert done.stderr.strip() == f"telegram-mcp {verb} is retired in comms v0.3; use comms mcp"
    assert done.stdout == ""


def test_no_production_composition_registers_the_legacy_mcp_server():
    reached = closure(*PRODUCTION_ROOTS)
    assert reached.isdisjoint(RETIRED_MCP), sorted(reached & RETIRED_MCP)
    assert T + "runtime.legacy_composition" not in reached


def test_the_demo_server_is_not_an_entry_point():
    scripts = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["scripts"]
    assert set(scripts) == {"comms", "telegram-mcp"}
    assert set(scripts.values()) == {"comms.transports.telegram.cli:main"}


def test_legacy_verify_imports_no_policy_or_coordinator():
    reached = closure(T + "legacy_verify")
    forbidden = {
        T + "authority.policy",
        T + "disclosure.coordinator",
        T + "disclosure.budget",
    }
    assert reached.isdisjoint(forbidden), sorted(reached & forbidden)
    assert not any(m.startswith(T + "ipc.handlers") for m in reached)
    assert reached.isdisjoint(RETIRED_MCP)


def test_legacy_verify_exposes_the_historical_entry_points():
    from comms.transports.telegram import legacy_verify

    assert set(legacy_verify.__all__) == {
        "missing_verification_keys",
        "verify_legacy_chain",
        "verify_proof",
        "verify_receipt_v1",
        "verify_receipt_v2",
    }


def test_the_closure_helper_sees_lazy_imports():
    # cli imports the daemon only inside a function; the closure must still reach it.
    assert T + "runtime.daemon" in closure(T + "cli")


def test_whatsvault_mcp_app_is_absent():
    wv = ROOT / "transports" / "whatsapp"
    assert not (wv / "apps" / "mcp").exists()
    assert not (wv / "apps" / "launchd" / "mcp.plist").exists()
    for path in wv.rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        for node in ast.walk(ast.parse(path.read_text())):
            names = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module, *(f"{node.module}.{a.name}" for a in node.names)]
            assert not any(n == "apps.mcp" or n.startswith("apps.mcp.") for n in names), path
    scripts = tomllib.loads((wv / "pyproject.toml").read_text())["project"]["scripts"]
    assert not any(target.startswith("apps.mcp") for target in scripts.values())
