"""The CLI entry point must be reachable on a machine that has no vault yet.

ops.bootstrap.init_vault() was implemented and tested, but cli.main.main() opened
BOTH encrypted databases before dispatching to a verb. On a fresh machine — the
only situation in which anyone runs `whatsvault init` — that raised KeyMissing
from open_existing() before argparse's verb ever reached COMMANDS. Every README
and USAGE example begins with `whatsvault init`, so the documented first command
was the one command that could not run.

The defect was invisible to the unit tests because main() carried
`# pragma: no cover`, and invisible to inspection because init_vault() itself is
correct. It appeared the moment the real entry point was executed against an
empty $WHATSVAULT_HOME. These tests exercise main() itself, with the keystore
and paths injected, so the production dispatch path is covered rather than the
library function underneath it.
"""

import json

import pytest

from whatsvault.cli import commands
from whatsvault.cli import main as cli
from whatsvault.crypto import keystore as KS
from whatsvault.db import connection as C
from whatsvault.ops import paths


@pytest.fixture
def fresh(tmp_path):
    """A home directory that does not exist and a keystore holding no keys."""
    return paths.Paths(str(tmp_path / "home")), KS.MemoryKeyStore()


# ---- the bootstrap verbs run before any database exists -----------------------
def test_init_runs_on_a_machine_with_no_vault(fresh, capsys):
    p, ks = fresh
    assert cli.main(["init"], ks=ks, p=p) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["ok"] is True
    # not merely a clean exit: the databases the next command needs now exist
    assert C.open_existing("vault", p.vault_db, ks)
    assert C.open_existing("control", p.control_db, ks)


def test_bootstrap_verbs_are_given_no_connections(fresh):
    """A bootstrap verb must be handed a context with no open databases, because
    opening one is precisely what it cannot do yet."""
    p, ks = fresh
    for verb in commands.BOOTSTRAP_VERBS:
        ctx = cli.open_ctx(verb, p, ks)
        assert ctx.vault is None and ctx.control is None, verb
        assert ctx.ks is ks, f"{verb} still needs the keystore"


def test_bootstrap_verbs_all_exist(fresh):
    assert set(commands.COMMANDS) >= commands.BOOTSTRAP_VERBS


def test_mcp_provision_runs_before_the_databases_exist(fresh, capsys):
    """Minting the daemon's Keychain keys touches no database, and an operator
    may reasonably run it before importing anything."""
    p, ks = fresh
    assert cli.main(["mcp-provision"], ks=ks, p=p) == 0
    assert json.loads(capsys.readouterr().out)["ok"] is True


# ---- an ordinary verb on an uninitialised vault reports, never crashes ---------
def test_missing_keys_report_the_fix_instead_of_raising(fresh, capsys):
    p, ks = fresh
    code = cli.main(["doctor"], ks=ks, p=p)
    assert code == 2
    out = json.loads(capsys.readouterr().out)
    assert out["ok"] is False
    # KeyMissing subclasses KeyStoreError; an unprovisioned vault must not be
    # reported as a broken keystore, or the operator fixes the wrong thing.
    assert "init" in out["error"], out["error"]
    assert "keystore" not in out["error"].lower(), out["error"]


def test_broken_keystore_is_not_reported_as_uninitialised(fresh, capsys):
    """The other side of the same catch order: a keystore that is genuinely
    unavailable must not tell the operator to run `init`, which would not help."""
    p, _ = fresh

    class Broken(KS.MemoryKeyStore):
        def require(self, name, nbytes):
            raise KS.KeyStoreError("keychain is locked")

    code = cli.main(["doctor"], ks=Broken(), p=p)
    assert code == 2
    out = json.loads(capsys.readouterr().out)
    assert out["ok"] is False and "init" not in out["error"]


# ---- the declared console scripts resolve --------------------------------------
def test_declared_entry_points_are_importable_and_callable():
    """pyproject declares `whatsvault` and `whatsvault-mcp`. Nothing else asserts
    that those targets exist, and a rename would only surface on a user's machine
    after `pip install`."""
    import importlib
    import tomllib
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    with (root / "pyproject.toml").open("rb") as fh:
        scripts = tomllib.load(fh)["project"]["scripts"]
    assert scripts, "no console scripts declared"
    for name, target in scripts.items():
        module, _, attr = target.partition(":")
        fn = getattr(importlib.import_module(module), attr, None)
        assert callable(fn), f"console script {name} -> {target} does not resolve"


def test_console_scripts_return_an_exit_code_rather_than_exiting():
    """setuptools generates `sys.exit(main())`, so a main() that returns None
    always exits 0 — a failed command would look like a success to a shell, to
    launchd, and to CI.

    Parsed rather than grepped: a source grep for "sys.exit" matched this test's
    own docstring, the same way an earlier dispatcher test flagged itself.
    """
    import ast
    import inspect

    tree = ast.parse(inspect.getsource(cli.main))
    calls = [
        n for n in ast.walk(tree) if isinstance(n, ast.Call) and ast.unparse(n.func) in ("sys.exit", "exit")
    ]
    assert calls == [], "main() must return its exit code, not raise SystemExit"
    assert inspect.signature(cli.main).return_annotation is int
