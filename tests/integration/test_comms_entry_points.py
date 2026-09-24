"""comms design §2.3: the compatibility surface, measured through real processes."""

import subprocess
import sys
from pathlib import Path

BIN = Path(sys.executable).parent


def _run(*argv: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(list(argv), capture_output=True, text=True, timeout=60, check=False)


def test_python_dash_m_legacy_cli_still_works():
    done = _run(sys.executable, "-m", "telegram_mcp.cli", "--help")
    assert done.returncode == 0, done.stderr
    assert "status" in done.stdout


def test_console_scripts_resolve_to_comms():
    """Review Focus #3: both scripts regenerated from pyproject, same main."""
    for script in ("telegram-mcp", "comms"):
        done = _run(str(BIN / script), "--help")
        assert done.returncode == 0, (script, done.stderr)
        assert "status" in done.stdout


def test_contracts_load_from_the_moved_package():
    """Review Focus #2: the resource anchor followed the files."""
    probe = (
        "from comms.transports.telegram.contract import load_contracts;print(len(load_contracts()))"
    )
    done = _run(sys.executable, "-c", probe)
    assert done.returncode == 0, done.stderr
    assert done.stdout.strip() == "10"


def test_the_package_is_importable_under_both_names():
    done = _run(
        sys.executable,
        "-c",
        "import comms.core, comms.transports.telegram.server, telegram_mcp.cli",
    )
    assert done.returncode == 0, done.stderr
