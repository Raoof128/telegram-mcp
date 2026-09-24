"""CLI verbs for import and MCP visibility.

Both capabilities were fully implemented and tested at the library level but had
no CLI verb, so neither was reachable by a user: the importer is a headline
feature with no entry point, and the LOCAL_ONLY fence had no way to be set.
"""

import os

import pytest

from whatsvault.cli import commands, main
from whatsvault.db import connection as C
from whatsvault.db import migrations as M

EXPORT = """[01/02/2026, 14:32:01] Alice: hello there
[01/02/2026, 14:33:10] Me: hi Alice
"""


@pytest.fixture
def ctx(tmp_path):
    v = C.open_db(str(tmp_path / "v.db"), os.urandom(32))
    M.migrate(v, "vault")
    c = C.open_db(str(tmp_path / "c.db"), os.urandom(32))
    M.migrate(c, "control")
    v.execute("INSERT INTO accounts(id, phone_number_id) VALUES('acc','pn')")
    v.execute("INSERT INTO conversations(id, account_id, type) VALUES('cnv','acc','dm')")
    v.commit()
    return commands.Ctx(v, c)


# ---- import ------------------------------------------------------------------
def test_import_requires_a_path(ctx):
    out = commands.cmd_import(ctx, {})
    assert out["ok"] is False and "--path" in out["error"]


def test_import_reports_a_missing_file_cleanly(ctx, tmp_path):
    out = commands.cmd_import(ctx, {"path": str(tmp_path / "nope.txt")})
    assert out["ok"] is False and "cannot read" in out["error"]


def test_dry_run_previews_without_writing(ctx, tmp_path):
    f = tmp_path / "chat.txt"
    f.write_text(EXPORT, encoding="utf-8")
    out = commands.cmd_import(ctx, {"path": str(f), "dry_run": True})
    assert out["ok"] is True and out["dry_run"] is True
    assert ctx.vault.execute("SELECT COUNT(*) FROM messages").fetchone()[0] == 0


def test_import_without_target_ids_refuses_rather_than_guessing(ctx, tmp_path):
    f = tmp_path / "chat.txt"
    f.write_text(EXPORT, encoding="utf-8")
    out = commands.cmd_import(ctx, {"path": str(f)})
    assert out["ok"] is False and "--conversation-id" in out["error"]


def test_import_writes_messages(ctx, tmp_path):
    f = tmp_path / "chat.txt"
    f.write_text(EXPORT, encoding="utf-8")
    out = commands.cmd_import(
        ctx, {"path": str(f), "conversation_id": "cnv", "account_id": "acc", "self_label": "Me"}
    )
    assert out["ok"] is True
    assert ctx.vault.execute("SELECT COUNT(*) FROM messages").fetchone()[0] > 0


def test_import_never_touches_control_db(ctx, tmp_path):
    """INV-IMPORT: an import writes evidence and can never reopen a send window."""
    f = tmp_path / "chat.txt"
    f.write_text(EXPORT, encoding="utf-8")
    commands.cmd_import(
        ctx, {"path": str(f), "conversation_id": "cnv", "account_id": "acc", "self_label": "Me"}
    )
    assert ctx.control.execute("SELECT COUNT(*) FROM conversation_windows").fetchone()[0] == 0


# ---- mcp-visibility ----------------------------------------------------------
def test_visibility_requires_both_arguments(ctx):
    assert commands.cmd_mcp_visibility(ctx, {"conversation_id": "cnv"})["ok"] is False
    assert commands.cmd_mcp_visibility(ctx, {"visibility": "LOCAL_ONLY"})["ok"] is False


def test_visibility_rejects_an_unknown_value(ctx):
    out = commands.cmd_mcp_visibility(ctx, {"conversation_id": "cnv", "visibility": "PUBLIC"})
    assert out["ok"] is False and "ALLOW_MCP" in out["error"]


def test_visibility_fences_and_unfences(ctx):
    from whatsvault.mcp import reads

    assert commands.cmd_mcp_visibility(ctx, {"conversation_id": "cnv", "visibility": "LOCAL_ONLY"})["ok"]
    assert reads.list_chats(ctx.vault) == []
    assert commands.cmd_mcp_visibility(ctx, {"conversation_id": "cnv", "visibility": "ALLOW_MCP"})["ok"]
    assert len(reads.list_chats(ctx.vault)) == 1


# ---- surface -----------------------------------------------------------------
def test_new_verbs_are_registered_and_not_forbidden():
    for verb in ("import", "import-undo", "mcp-visibility"):
        assert verb in commands.COMMANDS
    assert set(commands.COMMANDS).isdisjoint(commands.FORBIDDEN_VERBS)


def test_parser_accepts_every_flag_the_new_verbs_need():
    """A verb whose flags argparse rejects is unreachable in practice."""
    parser = main.build_parser()
    args = parser.parse_args(
        [
            "import",
            "--path",
            "chat.txt",
            "--conversation-id",
            "cnv",
            "--account-id",
            "acc",
            "--self-label",
            "Me",
            "--dry-run",
        ]
    )
    assert args.path == "chat.txt" and args.dry_run is True
    args = parser.parse_args(["mcp-visibility", "--conversation-id", "cnv", "--visibility", "LOCAL_ONLY"])
    assert args.visibility == "LOCAL_ONLY"
