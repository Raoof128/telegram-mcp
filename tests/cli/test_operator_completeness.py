"""D39-PRE Task E9 (R-E3): every registered operator command works, or it is not registered.

A command is served by the daemon's operator table, run locally by the CLI, or driven by the
CLI as steps over the daemon's commands; nothing else is registered. Every ``comms`` line a
runbook names resolves to one of those, or to a catalog tool family.
"""

from pathlib import Path

from comms.cli_commands.operator import (
    BACKUP_FLOWS,
    CLI_FLOWS,
    LOCAL_COMMANDS,
    OPERATOR_COMMANDS,
)
from comms.cli_commands.tools import FAMILIES
from comms.runtime.operator import OPERATOR_HANDLERS, PROTOCOL_STEPS
from comms.transports.telegram.ipc.admin import ADMIN_COMMANDS
from tests.security.test_runbooks import _commands

ROOT = Path(__file__).resolve().parents[2]
LOCAL = LOCAL_COMMANDS | {("daemon",), ("doctor",)}


def test_every_registered_operator_command_has_a_handler():
    served = set(OPERATOR_HANDLERS) - PROTOCOL_STEPS
    assert set(OPERATOR_COMMANDS) == served | LOCAL | set(CLI_FLOWS)
    assert set(CLI_FLOWS.values()) <= set(ADMIN_COMMANDS)  # each flow's steps exist
    assert BACKUP_FLOWS <= served and PROTOCOL_STEPS <= set(OPERATOR_HANDLERS)


def test_no_unwired_refusal_remains():
    for path in (ROOT / "src" / "comms").rglob("*.py"):
        assert "not wired in this release" not in path.read_text(encoding="utf-8"), path


def test_every_runbook_command_reaches_a_handler():
    for where, argv in _commands():
        if argv[0] != "comms":
            continue
        words = tuple(a for a in argv[1:] if not a.startswith(("-", "~", "/", "<")))
        if words[0] in FAMILIES or words[0] == "mcp":
            continue  # a catalog tool (D30), or the stdio proxy (D29)
        command = next(
            (words[:n] for n in range(len(words), 0, -1) if words[:n] in OPERATOR_COMMANDS), None
        )
        assert command is not None, (where, argv)
        assert command in OPERATOR_HANDLERS or command in LOCAL or command in CLI_FLOWS, (
            where,
            command,
        )
