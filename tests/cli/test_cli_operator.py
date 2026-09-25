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
    request = operator_request(build_parser().parse_args(argv), stdin=io.StringIO(""))
    assert request["cmd"] == "operator"
    command = request["args"]["command"]
    assert command[0] == argv[0] and all(isinstance(w, str) for w in command)
    assert argv[: len(command)] == command  # the command words, then its arguments


def test_credential_value_read_from_stdin_only():
    args = build_parser().parse_args(["credential", "set", "telegram-bot-token"])
    request = operator_request(args, stdin=io.StringIO("123456:secret-token\n"))
    assert request["args"]["value"] == "123456:secret-token"
    for bad in (
        ["credential", "set", "telegram-bot-token", "123456:secret-token"],
        ["credential", "set", "telegram-bot-token", "--value", "x"],
    ):
        with pytest.raises(SystemExit):
            build_parser().parse_args(bad)
    with pytest.raises(ValueError):  # an empty stdin is refused, never an empty secret
        operator_request(args, stdin=io.StringIO(""))


def test_revoke_takes_no_value():
    args = build_parser().parse_args(["credential", "revoke", "meta-access-token"])
    assert "value" not in operator_request(args, stdin=io.StringIO("ignored"))["args"]


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
