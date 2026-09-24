"""comms v0.3 Task B30: every command a runbook names exists (scope B; Task D29 widens it).

``telegram-mcp …`` lines are parsed by the real CLI parser, and ``telegram-mcp admin …``
must name an admin command. The ``comms`` CLI arrives in Part D (D30/D31): until then its
lines are checked against the operator surface below, the contract D31 implements; D29
replaces this set with the real parser.
"""

import re
import shlex
from pathlib import Path

import pytest

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
}
# The Part B contract for Part D's operator CLI (design D.7; plan D31).
COMMS_SURFACE = {
    ("daemon",),
    ("doctor",),
    ("keys", "provision"),
    ("keys", "list"),
    ("keys", "rotate"),
    ("keys", "mark-signer"),
    ("audit", "verify"),
    ("audit", "repair"),
    ("backup", "export"),
    ("backup", "import", "stage"),
    ("backup", "import", "commit"),
    ("credential", "set"),
    ("credential", "rotate"),
    ("credential", "revoke"),
    ("client", "add"),
    ("client", "rotate"),
    ("client", "disable"),
    ("transport", "telegram", "login"),
    ("transport", "telegram", "revoke-session"),
    ("cutover", "run"),
    ("cutover", "status"),
    ("retention", "run"),
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
    words = tuple(a for a in rest if not a.startswith("-"))
    assert any(words[: len(c)] == c for c in COMMS_SURFACE), (where, argv)
