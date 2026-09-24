"""An import must have a reachable target.

`whatsvault import` refuses to guess: without --conversation-id and --account-id
it stops rather than inventing one. That is the right refusal, but a freshly
initialised vault has no accounts and no conversations, and no verb created
either — so the two IDs the documented command requires could not be obtained by
any means. USAGE.md printed them as `cnv_01J…` and `acc_01J…`, which no user
could turn into a real value.

The result: `init` worked, `import --dry-run` worked, and the real import was
unreachable on every fresh vault. Like the bootstrap gap before it, each layer
was individually correct and tested; only running the documented sequence in
order exposed the missing link.
"""

import json
import os

import pytest

from whatsvault.cli import commands
from whatsvault.cli import main as cli
from whatsvault.db import connection as C
from whatsvault.db import migrations as M
from whatsvault.ids import IdError, parse_id

EXPORT = """[01/02/2026, 14:32:01] Alice: hello there
[01/02/2026, 14:33:10] Me: hi Alice
"""


@pytest.fixture
def ctx(tmp_path):
    v = C.open_db(str(tmp_path / "v.db"), os.urandom(32))
    M.migrate(v, "vault")
    c = C.open_db(str(tmp_path / "c.db"), os.urandom(32))
    M.migrate(c, "control")
    return commands.Ctx(v, c)


# ---- accounts ------------------------------------------------------------------
def test_accounts_add_returns_a_usable_id(ctx):
    out = commands.cmd_accounts_add(ctx, {})
    assert out["ok"] is True
    assert parse_id(out["account_id"]) == "acc"
    rows = commands.cmd_accounts_list(ctx, {})["accounts"]
    assert [r["id"] for r in rows] == [out["account_id"]]


def test_accounts_add_does_not_store_a_phone_number(ctx):
    """A manual-export vault has no Meta account behind it. Nothing may invent a
    phone number, and display_phone must stay empty (INV-DISPLAY)."""
    out = commands.cmd_accounts_add(ctx, {})
    row = ctx.vault.execute(
        "SELECT phone_number_id, display_phone, waba_id FROM accounts WHERE id=?",
        (out["account_id"],),
    ).fetchone()
    assert row["display_phone"] is None
    assert row["waba_id"] is None
    assert row["phone_number_id"] == commands.LOCAL_PHONE_NUMBER_ID


def test_accounts_list_never_emits_a_phone_number(ctx):
    commands.cmd_accounts_add(ctx, {})
    body = json.dumps(commands.cmd_accounts_list(ctx, {}))
    assert "display_phone" not in body


# ---- conversations --------------------------------------------------------------
def test_conversations_add_requires_an_account(ctx):
    out = commands.cmd_conversations_add(ctx, {"subject": "Alice"})
    assert out["ok"] is False and "--account-id" in out["error"]


def test_conversations_add_rejects_an_unknown_account(ctx):
    """A foreign-key violation surfaces as an sqlcipher error, not a message the
    operator can act on. Check the account exists and say so."""
    out = commands.cmd_conversations_add(ctx, {"account_id": "acc_nope", "subject": "Alice"})
    assert out["ok"] is False and "acc_nope" in out["error"]


def test_conversations_add_returns_a_usable_id(ctx):
    acc = commands.cmd_accounts_add(ctx, {})["account_id"]
    out = commands.cmd_conversations_add(ctx, {"account_id": acc, "subject": "Alice"})
    assert out["ok"] is True
    assert parse_id(out["conversation_id"]) == "cnv"
    rows = commands.cmd_conversations_list(ctx, {})["conversations"]
    assert rows[0]["id"] == out["conversation_id"] and rows[0]["subject"] == "Alice"


def test_conversations_add_rejects_an_unknown_type(ctx):
    """The schema CHECK would reject it anyway, but as an opaque constraint error."""
    acc = commands.cmd_accounts_add(ctx, {})["account_id"]
    out = commands.cmd_conversations_add(ctx, {"account_id": acc, "type": "channel"})
    assert out["ok"] is False and "channel" in out["error"]


def test_new_conversations_are_visible_to_mcp_by_default_but_fenceable(ctx):
    acc = commands.cmd_accounts_add(ctx, {})["account_id"]
    cnv = commands.cmd_conversations_add(ctx, {"account_id": acc, "subject": "Alice"})["conversation_id"]
    out = commands.cmd_mcp_visibility(ctx, {"conversation_id": cnv, "visibility": "LOCAL_ONLY"})
    assert out["ok"] is True


# ---- the documented sequence, in order -------------------------------------------
def test_the_documented_import_sequence_completes(ctx, tmp_path, capsys):
    """init → accounts-add → conversations-add → import → search, with every ID
    taken from the previous command's output rather than invented."""
    export = tmp_path / "chat.txt"
    export.write_text(EXPORT, encoding="utf-8")

    assert cli.run(["accounts-add"], ctx) == 0
    acc = json.loads(capsys.readouterr().out)["account_id"]

    assert cli.run(["conversations-add", "--account-id", acc, "--subject", "Alice"], ctx) == 0
    cnv = json.loads(capsys.readouterr().out)["conversation_id"]

    assert (
        cli.run(
            [
                "import",
                "--path",
                str(export),
                "--conversation-id",
                cnv,
                "--account-id",
                acc,
                "--timezone",
                "Australia/Sydney",
                "--date-format",
                "DMY",
                "--self-label",
                "Me",
            ],
            ctx,
        )
        == 0
    )
    imported = json.loads(capsys.readouterr().out)
    assert imported["ok"] is True and imported["added"] == 2

    # the messages are searchable, which is the only reason the vault exists
    from whatsvault.mcp import reads
    from whatsvault.search.query import SearchQuery

    hits = reads.search(ctx.vault, SearchQuery(terms=["hello"]))
    assert len(hits) == 1


def test_every_id_the_docs_show_can_be_produced(ctx):
    """USAGE.md tells the operator to pass acc_… and cnv_… values. Assert a verb
    exists that produces each prefix, so a doc example cannot again refer to an
    identifier nothing can mint."""
    produced = {
        parse_id(commands.cmd_accounts_add(ctx, {})["account_id"]),
    }
    acc = commands.cmd_accounts_add(ctx, {})["account_id"]
    produced.add(parse_id(commands.cmd_conversations_add(ctx, {"account_id": acc})["conversation_id"]))
    assert {"acc", "cnv"} <= produced
    with pytest.raises(IdError):
        parse_id("cnv_01J")  # the literal the docs used is not a real id
