"""comms v0.3 Task B30: every command a runbook names exists (scope B; Task D29 widens it).

``telegram-mcp …`` lines are parsed by the real CLI parser, and ``telegram-mcp admin …``
must name an admin command. Since D31 every ``comms …`` line is parsed by the real
``comms`` parser (D29 began with ``comms mcp``).
"""

import re
import shlex
from pathlib import Path

import pytest

from comms.cli import build_parser
from comms.transports.telegram.cli import _build_parser
from comms.transports.telegram.ipc.admin import ADMIN_COMMANDS

ROOT = Path(__file__).resolve().parents[2]
RUNBOOKS = sorted((ROOT / "docs" / "runbooks").glob("*.md"))
EXPECTED = {
    "install",
    "uninstall",
    "restore-from-backup",
    "session-compromise",
    "key-compromise",
    "audit-degraded",
    "provider-credential-rotation",
    "cutover",
    "live-acceptance-telegram",  # comms v0.3 C32
    "live-acceptance-whatsapp",  # comms v0.3 C32
    "clients-claude-code",  # comms v0.3 D38
    "clients-codex",  # comms v0.3 D38
    "clients-chatgpt",  # comms v0.3 D38
}


def _commands():
    for path in RUNBOOKS:
        for block in re.findall(r"```bash\n(.*?)```", path.read_text(encoding="utf-8"), re.DOTALL):
            for line in block.splitlines():
                line = line.strip()
                if line.startswith(("comms ", "telegram-mcp ")):
                    yield path.name, shlex.split(line)


def test_the_runbook_set_is_complete():
    assert {p.stem for p in RUNBOOKS} == EXPECTED


@pytest.mark.parametrize(
    "where, argv", list(_commands()), ids=lambda v: " ".join(v) if isinstance(v, list) else v
)
def test_every_runbook_command_exists_and_has_a_handler(where, argv):
    program, *rest = argv
    if program == "telegram-mcp":
        _build_parser().parse_args(rest)  # SystemExit on an unknown verb or argument
        if rest[0] == "admin":
            assert " ".join(a for a in rest[1:] if not a.startswith("-")) in ADMIN_COMMANDS, where
        return
    build_parser().parse_args(rest)  # D31: every comms line, by the real parser


def test_comms_mcp_is_parsed_by_the_real_cli():
    args = build_parser().parse_args(["mcp", "--stdio", "--client-seed", "/tmp/seed"])
    assert args.stdio and args.client_seed == Path("/tmp/seed")
    for bad in (["mcp"], ["mcp", "--stdio"], ["mcp", "--stdio", "--seed", "x"]):
        with pytest.raises(SystemExit):
            build_parser().parse_args(bad)
