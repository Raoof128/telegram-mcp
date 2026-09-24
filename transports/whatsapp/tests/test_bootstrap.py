"""Vault bootstrap: the path from a fresh clone to a running system.

connection.provision_db() existed and was tested, but nothing called it — no CLI
verb, no daemon — so there was no way to create a vault at all. Every entry point
used open_existing(), which requires databases nothing could produce.

The dangerous case this must never hit: minting a fresh key over a database that
already exists. SQLCipher keys are not recoverable, so that would strand the
vault permanently. `require` never regenerates for exactly this reason
(INV-ATREST); init has to be equally careful one level up.
"""

import os
import stat
from pathlib import Path

import pytest

from whatsvault.crypto import keystore as KS
from whatsvault.db import connection as C
from whatsvault.db import migrations as M
from whatsvault.mcp import audit, auth
from whatsvault.ops import bootstrap, paths


@pytest.fixture
def env(tmp_path):
    return paths.Paths(str(tmp_path / "home")), KS.MemoryKeyStore()


def _mode(path):
    return stat.S_IMODE(os.stat(path).st_mode)


# ---- happy path ---------------------------------------------------------------
def test_init_creates_databases_and_keys(env):
    p, ks = env
    out = bootstrap.init_vault(p, ks)
    assert out["ok"] is True
    assert out["created"] == ["vault", "control"]
    assert os.path.isfile(p.vault_db) and os.path.isfile(p.control_db)
    for name in (
        C.DB_KEY_NAMES["vault"],
        C.DB_KEY_NAMES["control"],
        auth.TOKEN_KEY_NAME,
        audit.AUDIT_KEY_NAME,
    ):
        assert ks.require(name, 32), f"{name} not provisioned"


def test_init_applies_migrations(env):
    p, ks = env
    bootstrap.init_vault(p, ks)
    v = C.open_existing("vault", p.vault_db, ks)
    c = C.open_existing("control", p.control_db, ks)
    assert M.user_version(v) == max(n for n, _ in M.MIGRATIONS["vault"])
    assert M.user_version(c) == max(n for n, _ in M.MIGRATIONS["control"])
    # The schema is genuinely usable, not merely versioned.
    assert v.execute("SELECT COUNT(*) FROM messages").fetchone()[0] == 0
    assert c.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0] == 0


def test_init_creates_every_runtime_directory_at_0700(env):
    p, ks = env
    bootstrap.init_vault(p, ks)
    for d in p.all_dirs():
        assert os.path.isdir(d), d
        assert _mode(d) == 0o700, f"{d} is {oct(_mode(d))}"


def test_database_files_are_owner_only(env):
    p, ks = env
    bootstrap.init_vault(p, ks)
    for f in p.secret_files():
        assert _mode(f) == 0o600, f"{f} is {oct(_mode(f))}"


def test_fsperms_check_passes_after_init(env):
    from whatsvault.ops import fsperms

    p, ks = env
    bootstrap.init_vault(p, ks)
    assert all(f["ok"] for f in fsperms.check(p))


# ---- idempotence and safety ---------------------------------------------------
def test_second_init_is_a_no_op(env):
    p, ks = env
    bootstrap.init_vault(p, ks)
    before = Path(p.vault_db).read_bytes()
    out = bootstrap.init_vault(p, ks)
    assert out["ok"] is True and out["created"] == []
    assert out["already_present"] == ["vault", "control"]
    assert Path(p.vault_db).read_bytes() == before  # byte-identical: nothing rewritten


def test_init_never_mints_a_key_over_an_existing_database(env):
    """The unrecoverable case: a vault file whose key is gone.

    Provisioning here would mint a NEW key, leaving the existing ciphertext
    permanently unreadable. It must refuse and say so.
    """
    p, ks = env
    bootstrap.init_vault(p, ks)
    ks._d.pop(C.DB_KEY_NAMES["vault"])  # simulate a lost Keychain entry

    out = bootstrap.init_vault(p, ks)
    assert out["ok"] is False
    assert "vault" in out["error"] and "key" in out["error"].lower()
    # and critically: no new key was minted
    with pytest.raises(KS.KeyMissing):
        ks.require(C.DB_KEY_NAMES["vault"], 32)


def test_existing_key_with_missing_database_is_recoverable(env):
    """The reverse is safe: the key still opens whatever is created next."""
    p, ks = env
    bootstrap.init_vault(p, ks)
    key_before = ks.require(C.DB_KEY_NAMES["vault"], 32)
    os.remove(p.vault_db)

    out = bootstrap.init_vault(p, ks)
    assert out["ok"] is True and "vault" in out["created"]
    assert ks.require(C.DB_KEY_NAMES["vault"], 32) == key_before  # reused, not rotated
    assert C.open_existing("vault", p.vault_db, ks)


# ---- token handling -----------------------------------------------------------
def test_token_is_withheld_unless_revealed(env):
    p, ks = env
    out = bootstrap.init_vault(p, ks)
    assert "token" not in out
    assert "reveal" in out["note"].lower()


def test_reveal_returns_the_usable_token(env):
    p, ks = env
    out = bootstrap.init_vault(p, ks, reveal=True)
    assert out["token"] == ks.require(auth.TOKEN_KEY_NAME, 32).hex()


# ---- CLI wiring ---------------------------------------------------------------
def test_init_verb_is_registered_and_not_forbidden():
    from whatsvault.cli import commands

    assert "init" in commands.COMMANDS
    assert set(commands.COMMANDS).isdisjoint(commands.FORBIDDEN_VERBS)


def test_cli_init_runs_without_preexisting_context(tmp_path, monkeypatch):
    """`init` must work when there is nothing to open yet — Ctx has no databases."""
    from whatsvault.cli import commands

    monkeypatch.setenv("WHATSVAULT_HOME", str(tmp_path / "home"))
    ctx = commands.Ctx(None, None, ks=KS.MemoryKeyStore())
    out = commands.cmd_init(ctx, {})
    assert out["ok"] is True
    assert os.path.isfile(str(tmp_path / "home" / "vault.db"))


def test_cli_init_reports_a_missing_keystore(tmp_path, monkeypatch):
    from whatsvault.cli import commands

    monkeypatch.setenv("WHATSVAULT_HOME", str(tmp_path / "home"))
    out = commands.cmd_init(commands.Ctx(None, None), {})
    assert out["ok"] is False and "keystore" in out["error"].lower()


# ---- the report must account for every key init mints --------------------------
def test_report_names_every_key_that_was_provisioned(env):
    """keys_provisioned listed only the two MCP keys, because the database keys
    are minted inside provision_db. An operator reading the report would believe
    two keys existed when four did — and the two omitted ones are the pair whose
    loss makes the vault permanently unreadable."""
    p, ks = env
    out = bootstrap.init_vault(p, ks)
    assert set(out["keys_provisioned"]) == {
        C.DB_KEY_NAMES["vault"],
        C.DB_KEY_NAMES["control"],
        auth.TOKEN_KEY_NAME,
        audit.AUDIT_KEY_NAME,
    }


def test_a_second_run_reports_no_new_keys(env):
    p, ks = env
    bootstrap.init_vault(p, ks)
    assert bootstrap.init_vault(p, ks)["keys_provisioned"] == []


def test_a_partial_vault_reports_only_the_missing_key(env):
    """The control database exists with its key; the vault does not. Only the
    vault key is minted, and the report says exactly that."""
    p, ks = env
    bootstrap.init_vault(p, ks)
    os.remove(p.vault_db)
    ks._d.pop(C.DB_KEY_NAMES["vault"])
    assert bootstrap.init_vault(p, ks)["keys_provisioned"] == [C.DB_KEY_NAMES["vault"]]


# ---- an existing vault must receive newly shipped migrations -------------------
def test_init_migrates_a_vault_created_by_an_earlier_version(env):
    """`init` skipped any database that already existed, so a migration added
    after a vault was created never reached it. The vault kept working until
    something touched the new table, then failed with `no such table` on a live
    system — which is exactly how the OAuth tables were found missing.

    Forward-only migration of an existing database is the safe, idempotent
    operation the runner is built for; skipping it was never protecting anything.
    """
    p, ks = env
    bootstrap.init_vault(p, ks)
    control = C.open_existing("control", p.control_db, ks)
    latest = max(n for n, _ in M.MIGRATIONS["control"])

    # rewind to an older schema, as a vault created by an earlier release would be
    control.execute("DROP TABLE IF EXISTS oauth_tokens")
    control.execute("DROP TABLE IF EXISTS oauth_codes")
    control.execute("DROP TABLE IF EXISTS oauth_pending")
    control.execute("DROP TABLE IF EXISTS oauth_clients")
    # Rewind past every migration that touches these tables, so the runner
    # recreates them rather than trying to ALTER one that is not there.
    control.execute("PRAGMA user_version = 3")
    control.commit()
    control.close()

    out = bootstrap.init_vault(p, ks)
    assert out["ok"] is True
    assert out["migrated"] == ["control"], out

    control = C.open_existing("control", p.control_db, ks)
    assert M.user_version(control) == latest
    assert control.execute("SELECT COUNT(*) FROM oauth_pending").fetchone()[0] == 0


def test_a_vault_already_current_reports_no_migration(env):
    p, ks = env
    bootstrap.init_vault(p, ks)
    assert bootstrap.init_vault(p, ks)["migrated"] == []


def test_doctor_reports_a_database_behind_the_shipped_schema(env):
    """Silence is what made this expensive: the vault looked healthy right up to
    the moment a query hit the missing table."""
    from whatsvault import doctor

    p, ks = env
    bootstrap.init_vault(p, ks)
    control = C.open_existing("control", p.control_db, ks)
    control.execute("PRAGMA user_version = 1")
    control.commit()
    vault = C.open_existing("vault", p.vault_db, ks)
    checks = {c["check"]: c for c in doctor.check_mcp(vault, control)}
    assert checks["schema_current"]["ok"] is False
    assert "init" in checks["schema_current"]["detail"]
