"""Task 8: the install scripts and the LaunchAgent definition.

The dry-run and lint checks are headless: they prove the scripts are valid,
idempotent by construction and that they would touch exactly the paths the
design names. Anything that actually creates a service account, loads a
LaunchAgent or probes elevation is ``platform_gated`` and runs only with
``--run-platform-gated`` on a macOS host with admin rights.
"""

import plistlib
import shutil
import subprocess
from pathlib import Path

import pytest

SCRIPTS = Path("scripts")
USERS = SCRIPTS / "install_service_users.sh"
PATHS = SCRIPTS / "install_paths.sh"
PLIST = SCRIPTS / "consent-agent.plist"


def _run(*args, env=None):
    return subprocess.run(
        ["/bin/bash", *[str(a) for a in args]],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
        env=env,
    )


@pytest.mark.parametrize("script", [USERS, PATHS])
def test_scripts_are_executable_and_syntactically_valid(script):
    assert script.exists()
    assert script.stat().st_mode & 0o111
    assert _run("-n", script).returncode == 0


@pytest.mark.parametrize("script", [USERS, PATHS])
def test_scripts_require_an_explicit_verb(script):
    assert _run(script).returncode == 2
    assert _run(script, "install", "--unknown-flag").returncode == 2
    assert "usage:" in _run(script).stdout


def test_service_user_plan_is_non_login_and_collision_safe():
    result = _run(USERS, "install", "--dry-run")
    assert result.returncode == 0, result.stderr
    out = result.stdout
    assert "telegram-mcpd" in out and "telegram-mcp-tunnel" in out
    assert "/usr/bin/false" in out
    assert "/var/empty" in out
    assert "telegram-mcp-admin: adding operator" in out
    # every id the plan hands out is distinct
    ids = [line.split()[-1] for line in out.splitlines() if "creating with gid" in line]
    assert len(ids) == len(set(ids)) == 3
    assert "AuthenticationAuthority" in out


def test_service_user_uninstall_and_repair_plans_are_bounded():
    for verb in ("uninstall", "repair"):
        result = _run(USERS, verb, "--dry-run")
        assert result.returncode == 0, result.stderr
        assert "rm -rf" not in result.stdout


def test_path_plan_creates_the_design_layout_with_exact_modes(tmp_path):
    import os

    env = dict(os.environ)
    env["TELEGRAM_MCP_RUNTIME_DIR"] = str(tmp_path / "run")
    env["TELEGRAM_MCP_STATE_DIR"] = str(tmp_path / "state")
    result = _run(PATHS, "install", "--dry-run", env=env)
    assert result.returncode == 0, result.stderr
    out = result.stdout
    assert (
        f"dir {tmp_path / 'run'}: creating mode 0770 owner telegram-mcpd:telegram-mcp-admin" in out
    )
    assert (
        f"dir {tmp_path / 'state' / 'db'}: creating mode 0700 owner telegram-mcpd:telegram-mcpd"
        in out
    )
    assert "serverAuth" not in out  # extension config never printed
    assert "chmod 600" in out
    assert "tunnel-server" in out and "tunnel-client" in out
    assert "rotate-binding" in out
    # no private material is ever printed
    assert "BEGIN" not in out


def test_path_plan_does_not_touch_the_host(tmp_path):
    import os

    env = dict(os.environ)
    env["TELEGRAM_MCP_RUNTIME_DIR"] = str(tmp_path / "run")
    env["TELEGRAM_MCP_STATE_DIR"] = str(tmp_path / "state")
    assert _run(PATHS, "install", "--dry-run", env=env).returncode == 0
    assert not (tmp_path / "run").exists()
    assert not (tmp_path / "state").exists()


def test_consent_launch_agent_is_disabled_by_default_and_logs_nothing():
    with PLIST.open("rb") as handle:
        plist = plistlib.load(handle)
    assert plist["Label"] == "com.telegram-mcp.consent-agent"
    assert plist["RunAtLoad"] is False
    assert plist["KeepAlive"] is False
    assert plist["ProcessType"] == "Interactive"
    assert plist["StandardOutPath"] == "/dev/null"
    assert plist["StandardErrorPath"] == "/dev/null"
    assert plist["ProgramArguments"][0].endswith("MacOS/telegram-mcp-consent")
    assert "/private/var/run/telegram-mcp/consent.sock" in plist["ProgramArguments"]


@pytest.mark.skipif(shutil.which("plutil") is None, reason="plutil is macOS-only")
def test_consent_launch_agent_passes_plutil_lint():
    result = subprocess.run(
        ["plutil", "-lint", str(PLIST)], capture_output=True, text=True, check=False, timeout=60
    )
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.platform_gated
def test_service_accounts_exist_and_cannot_be_impersonated():
    """Real host probe: both accounts non-login, neither impersonable."""
    for account in ("telegram-mcpd", "telegram-mcp-tunnel"):
        shell = subprocess.run(
            ["/usr/bin/dscl", ".", "-read", f"/Users/{account}", "UserShell"],
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
        assert shell.returncode == 0, f"{account} is not installed"
        assert "/usr/bin/false" in shell.stdout
        elevate = subprocess.run(
            ["/usr/bin/sudo", "-n", "-u", account, "true"],
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
        assert elevate.returncode != 0, f"non-interactive elevation to {account} succeeded"


@pytest.mark.platform_gated
def test_service_account_secrets_are_unreadable_from_this_account():
    """Real host probe: the interactive user cannot read runtime secrets."""
    state = Path("/var/db/telegram-mcp")
    if not state.exists():
        pytest.skip("paths installer has not run on this host")
    for candidate in (state / "keys", state / "db", state / "tls"):
        if not candidate.exists():
            continue
        with pytest.raises(PermissionError):
            list(candidate.iterdir())
