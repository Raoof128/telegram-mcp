"""comms v0.3 Task D31: the comms CLI's operator-only commands; the telegram-mcp alias."""

import io

import pytest

from comms.cli import build_parser, main
from comms.cli_commands.operator import OPERATOR_GROUPS, operator_request
from comms.mcp.catalog import TOOL_CATALOG


def test_operator_only_commands_are_not_mcp_tools():
    for spec in TOOL_CATALOG:
        words = set(spec.name.removeprefix("comms_").split("_"))
        assert not words & set(OPERATOR_GROUPS), spec.name
    assert set(OPERATOR_GROUPS) == {
        "transport",
        "credential",
        "keys",
        "audit",
        "retention",
        "backup",
        "doctor",
        "cutover",
        "client",
        "daemon",
        "oauth",
    }


@pytest.mark.parametrize(
    "argv",
    [
        ["keys", "rotate", "audit-chain-key"],
        ["audit", "verify", "--all"],
        ["backup", "import", "stage", "--from", "/tmp/b"],
        ["client", "add", "--name", "claude-code", "--helper-path", "/tmp/seed"],
        ["client", "disable", "cli_" + "a" * 26],
        ["transport", "telegram", "revoke-session"],
        ["cutover", "status"],
        ["retention", "run"],
    ],
)
def test_operator_commands_become_one_admin_request(argv):
    request = operator_request(build_parser().parse_args(argv), read_value=lambda: "")
    assert request["cmd"] == "operator"
    command = request["args"]["command"]
    assert command[0] == argv[0] and all(isinstance(w, str) for w in command)
    assert argv[: len(command)] == command  # the command words, then its arguments


def test_credential_value_comes_from_the_value_reader_only():
    args = build_parser().parse_args(["credential", "set", "telegram-bot-token"])
    request = operator_request(args, read_value=lambda: "123456:secret-token\n")
    assert request["args"]["value"] == "123456:secret-token"
    for bad in (
        ["credential", "set", "telegram-bot-token", "123456:secret-token"],
        ["credential", "set", "telegram-bot-token", "--value", "x"],
    ):
        with pytest.raises(SystemExit):
            build_parser().parse_args(bad)
    with pytest.raises(ValueError):  # an empty value is refused, never an empty secret
        operator_request(args, read_value=lambda: "")


def test_revoke_takes_no_value():
    args = build_parser().parse_args(["credential", "revoke", "meta-access-token"])
    assert "value" not in operator_request(args, read_value=lambda: "ignored")["args"]


def test_serve_refused(capsys):
    with pytest.raises(SystemExit) as refused:
        main(["serve"])
    assert refused.value.code != 0
    assert "retired" in capsys.readouterr().err


def test_telegram_mcp_alias_forwards(monkeypatch):
    import comms.cli
    from comms.transports.telegram import cli as legacy

    seen = []
    monkeypatch.setattr(comms.cli, "main", lambda argv=None: seen.append(argv))
    monkeypatch.setattr("sys.argv", ["telegram-mcp", "credential", "revoke", "meta-access-token"])
    legacy.main()
    assert seen == [["credential", "revoke", "meta-access-token"]]


def test_legacy_verbs_still_reach_the_legacy_cli(monkeypatch):
    from comms.transports.telegram import cli as legacy

    seen = []
    monkeypatch.setattr(legacy, "main", lambda: seen.append("legacy"))
    main(["status"])
    assert seen == ["legacy"]


def test_a_credential_value_is_read_only_from_a_tty(monkeypatch, capsys):
    """D39-PRE E8a: never argv, never env, never a pipe: a value typed at a terminal."""

    from comms import cli

    monkeypatch.setattr("sys.stdin", io.StringIO("7000000001:AAsecret\n"))
    with pytest.raises(SystemExit) as refused:
        cli.main(["credential", "set", "telegram-bot-token"])
    assert refused.value.code != 0
    assert "entered interactively" in capsys.readouterr().err


class _Tty:
    def isatty(self):
        return True


def test_telegram_login_is_a_tty_flow_over_the_daemons_login_steps(monkeypatch, capsys):
    """D39-PRE E8a: phone and code are prompted; a 2FA password only through getpass."""
    import json

    from comms import cli

    sent = []
    answers = iter([{"login": "h1", "next": "code"}, {"login": "h1", "next": "password"},
                    {"authorized": True, "account_ref": "acct_x"}])  # fmt: skip

    def fake_request(runtime_dir, request):
        sent.append(request)
        return {"ok": True, "data": next(answers)}

    prompts = iter(["+61400000001", "12345"])
    monkeypatch.setattr(cli, "_admin_request", fake_request)
    monkeypatch.setattr("sys.stdin", _Tty())
    monkeypatch.setattr("builtins.input", lambda prompt="": next(prompts))
    monkeypatch.setattr("getpass.getpass", lambda prompt="": "two-factor-secret")
    cli.main(["transport", "telegram", "login"])
    steps = [(r["cmd"], r["args"]["step"]) for r in sent]
    assert steps == [("auth login", "start"), ("auth login", "code"), ("auth login", "password")]
    assert sent[2]["args"]["password"] == "two-factor-secret"
    printed = capsys.readouterr().out
    assert json.loads(printed) == {"authorized": True, "account_ref": "acct_x"}
    assert "two-factor-secret" not in printed and "12345" not in printed


def test_telegram_login_refuses_without_a_tty(monkeypatch, capsys):
    import io

    from comms import cli

    monkeypatch.setattr("sys.stdin", io.StringIO("+61400000001\n"))
    with pytest.raises(SystemExit):
        cli.main(["transport", "telegram", "login"])
    assert "interactively" in capsys.readouterr().err


def test_revoke_session_is_one_admin_request(monkeypatch, capsys):
    from comms import cli

    sent = []
    monkeypatch.setattr(cli, "_admin_request",
                        lambda d, r: sent.append(r) or {"ok": True, "data": {"revoked": True}})  # fmt: skip
    cli.main(["transport", "telegram", "revoke-session"])
    assert sent == [{"cmd": "auth revoke-this-session", "args": {}}]
