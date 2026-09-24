"""launchd targets must actually run (ledger #57 follow-up).

test_ops_launchd validates plist *structure* — keys present, no inline secrets.
It cannot catch a well-formed plist pointing at something that exits immediately,
which with KeepAlive=true becomes a silent restart loop rather than a visible
failure. These tests check the target is genuinely executable.
"""

import glob
import os
import plistlib
import tomllib
from pathlib import Path

import pytest

# Daemons whose module has no __main__ entry yet. Each is a real restart-loop bug;
# they are recorded rather than silently tolerated, and this list must only shrink.
# Empty: every shipped unit now has a real entry point. Three of the four cannot
# yet do their job (gated dependencies), but they report the blocker and exit 0
# instead of restart-looping. Keep this empty.
KNOWN_BROKEN: dict[str, str] = {}


def _plists():
    return sorted(glob.glob("apps/launchd/*.plist"))


def _load(path):
    with open(path, "rb") as f:
        return plistlib.load(f)


def _module_path(argv):
    """Resolve `... -m pkg.mod` to a source path."""
    if "-m" not in argv:
        return None
    return argv[argv.index("-m") + 1].replace(".", "/") + ".py"


@pytest.mark.parametrize("path", _plists())
def test_plist_target_is_runnable(path):
    pl = _load(path)
    label = pl["Label"]
    src = _module_path(pl["ProgramArguments"])
    if src is None:
        return  # console-script form, covered below
    if label in KNOWN_BROKEN:
        pytest.xfail(KNOWN_BROKEN[label])
    assert os.path.exists(src), f"{label} targets a module that does not exist: {src}"
    body = Path(src).read_text(encoding="utf-8")
    assert '__name__ == "__main__"' in body, (
        f"{label} targets {src}, which has no __main__ entry: launchd would import it, "
        "get an immediate exit, and restart it forever"
    )


def test_known_broken_list_only_shrinks():
    """A new daemon must not be added to the debt list without a conscious edit."""
    labels = {_load(p)["Label"] for p in _plists()}
    assert set(KNOWN_BROKEN) <= labels, "KNOWN_BROKEN names a plist that no longer exists"
    assert KNOWN_BROKEN == {}, "a launchd unit regressed to having no entry point"


def test_mcp_plist_does_not_use_system_python():
    """System python3 has none of the deps (mcp, sqlcipher3, cryptography)."""
    pl = next(_load(p) for p in _plists() if _load(p)["Label"] == "com.whatsvault.mcp")
    argv = pl["ProgramArguments"]
    assert argv[:2] != ["/usr/bin/env", "python3"], "system python cannot import the project's dependencies"
    assert any("venv" in a or a.endswith("whatsvault-mcp") for a in argv), argv


def test_mcp_plist_binds_loopback_only():
    """The daemon must never be launched with a public bind (#18)."""
    pl = next(_load(p) for p in _plists() if _load(p)["Label"] == "com.whatsvault.mcp")
    blob = repr(pl)
    assert "0.0.0.0" not in blob and "::" not in blob


def test_mcp_console_entry_point_declared():
    cfg = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))
    scripts = cfg["project"]["scripts"]
    assert "whatsvault-mcp" in scripts, "no console entry point for the MCP daemon"
    assert scripts["whatsvault-mcp"] == "apps.mcp.server:main"


def test_apps_package_is_installable():
    """`-m apps.mcp.server` and the console script both need `apps` packaged;
    packages.find previously looked only in src/, so an installed wheel had none."""
    cfg = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))
    find = cfg["tool"]["setuptools"]["packages"]["find"]
    # Parsed as TOML rather than string-split: a naive split on "[" stops at the
    # first list literal (`where = [...]`) and silently reports a false failure.
    assert "." in find["where"], "apps/ is outside the package search roots"
    assert any(pat.startswith("apps") for pat in find["include"]), (
        "apps/ is not packaged; the installed console script would fail"
    )
