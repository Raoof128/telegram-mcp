"""The comms CLI's operator-only commands (comms v0.3 Task D31; design D.7).

Transport, credential, keys, audit, retention, backup, cutover and client administration are
the owner's, over the admin socket (peer-credential authority) — never MCP tools. Each command
becomes one ``operator`` admin request. A credential's value is read from **stdin** only: no
argument can carry it, and an empty stdin is refused rather than stored. ``daemon`` and
``doctor`` run locally, as the operator CLI always has.
"""

from __future__ import annotations

import argparse
from collections.abc import Mapping, Sequence
from typing import Any, TextIO

__all__ = [
    "LOCAL_GROUPS",
    "OPERATOR_COMMANDS",
    "OPERATOR_GROUPS",
    "add_operator_parsers",
    "operator_request",
]

# argument spec: ("name", "positional") | ("--flag", "optional" | "required" | "switch")
Arg = tuple[str, str]
OPERATOR_COMMANDS: Mapping[tuple[str, ...], Sequence[Arg]] = {
    ("daemon",): (
        ("--runtime-dir", "optional"),
        ("--state-dir", "optional"),
        ("--store-dir", "optional"),
    ),
    ("doctor",): (("--production", "switch"),),
    ("keys", "provision"): (("--state-dir", "optional"), ("--runtime-dir", "optional")),
    ("keys", "list"): (),
    ("keys", "rotate"): (
        ("purpose", "positional"),
        ("--state-dir", "optional"),  # comms-db-key only: it rotates locally, daemon stopped
        ("--runtime-dir", "optional"),
    ),
    ("keys", "mark-signer"): (("--key-id", "required"), ("--state", "required")),
    ("audit", "verify"): (("--all", "switch"),),
    ("audit", "repair"): (),
    ("backup", "export"): (("--out", "optional"),),
    ("backup", "import", "stage"): (("--from", "optional"),),
    ("backup", "import", "commit"): (),
    ("credential", "set"): (("purpose", "positional"),),
    ("credential", "rotate"): (("purpose", "positional"),),
    ("credential", "revoke"): (("purpose", "positional"),),
    ("client", "add"): (("--name", "required"), ("--helper-path", "required")),
    ("client", "rotate"): (("client", "positional"), ("--helper-path", "required")),
    ("client", "disable"): (("client", "positional"),),
    ("transport", "telegram", "login"): (),
    ("transport", "telegram", "revoke-session"): (),
    ("cutover", "run"): (),
    ("cutover", "status"): (),
    ("retention", "run"): (),
    ("oauth", "approve"): (),  # D34: a one-time, 5-minute owner code for the remote /authorize
}
OPERATOR_GROUPS = tuple(sorted({words[0] for words in OPERATOR_COMMANDS}))
LOCAL_GROUPS = frozenset({"daemon", "doctor"})  # run by the local operator CLI
# D39-PRE E1 (R-E4): run in this process before any daemon exists, under the runtime lock.
LOCAL_COMMANDS = frozenset({("keys", "provision")})
_VALUE_FROM_STDIN = frozenset({("credential", "set"), ("credential", "rotate")})


def _dest(flag: str) -> str:
    return flag.lstrip("-").replace("-", "_")


def add_operator_parsers(sub: Any) -> None:
    parsers: dict[tuple[str, ...], Any] = {}
    children: dict[tuple[str, ...], Any] = {}
    for words, arguments in OPERATOR_COMMANDS.items():
        for depth in range(1, len(words) + 1):
            prefix = words[:depth]
            if prefix in parsers:
                continue
            if depth == 1:
                parent = sub
            else:
                if prefix[:-1] not in children:  # one subparser group per parent, made once
                    children[prefix[:-1]] = parsers[prefix[:-1]].add_subparsers(
                        dest=f"operator_{depth - 1}", required=True
                    )
                parent = children[prefix[:-1]]
            parsers[prefix] = parent.add_parser(prefix[-1], allow_abbrev=False)
        parser = parsers[words]
        parser.set_defaults(operator=words)
        for name, kind in arguments:
            if kind == "positional":
                parser.add_argument(name)
            elif kind == "switch":
                parser.add_argument(name, dest=_dest(name), action="store_true")
            else:
                parser.add_argument(name, dest=_dest(name), required=kind == "required")


def operator_request(args: argparse.Namespace, *, stdin: TextIO) -> dict[str, Any]:
    """One ``operator`` admin request; a credential value comes from ``stdin`` alone."""
    words = tuple(args.operator)
    payload: dict[str, Any] = {"command": list(words)}
    for name, kind in OPERATOR_COMMANDS[words]:
        value = getattr(args, _dest(name) if kind != "positional" else name, None)
        if value is not None and value is not False:
            payload[_dest(name)] = value
    if words in _VALUE_FROM_STDIN:
        value = stdin.readline().rstrip("\r\n")
        if not value:
            raise ValueError("the credential value is read from stdin and must not be empty")
        payload["value"] = value
    return {"cmd": "operator", "args": payload}
