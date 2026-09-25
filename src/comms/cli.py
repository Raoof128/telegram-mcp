"""The ``comms`` command (comms v0.3 Part D).

``comms mcp --stdio --client-seed <path>`` runs the unprivileged stdio proxy (D29); the
campaign, location, audience, group and message commands (D30) and the operator-only commands
(D31) travel over the admin socket. ``daemon``, ``doctor`` and the legacy runtime verbs run the
operator CLI locally, as before; ``serve`` stays retired.
"""

from __future__ import annotations

import argparse
import socket
import sys
from pathlib import Path
from typing import Any

from comms.cli_commands.operator import (
    LOCAL_GROUPS,
    OPERATOR_GROUPS,
    add_operator_parsers,
    operator_request,
)
from comms.cli_commands.tools import FAMILIES, add_tool_parsers, command_request

__all__ = ["build_parser", "main"]

EXIT_USAGE = 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="comms", allow_abbrev=False)
    sub = parser.add_subparsers(dest="verb", required=True)
    mcp = sub.add_parser("mcp", help="serve MCP to one local client", allow_abbrev=False)
    mcp.add_argument("--stdio", action="store_true", required=True, help="speak MCP over stdio")
    mcp.add_argument("--client-seed", type=Path, required=True, help="this client's 0600 seed file")
    mcp.add_argument("--daemon", default="http://127.0.0.1:8765", help="the daemon's /mcp origin")
    mcp.add_argument("--runtime-dir", default=None, help="where the daemon's admin socket lives")
    add_tool_parsers(sub)  # D30: campaign, location, audience, group, message
    add_operator_parsers(sub)  # D31: the owner's commands, never MCP tools
    return parser


def _admin_request(runtime_dir: str | None, request: dict[str, Any]) -> dict[str, Any]:
    """One framed request over the daemon's admin socket (peer-credential authority)."""
    from comms.transports.telegram.ipc.framing import decode_json_frame, encode_json_frame
    from comms.transports.telegram.runtime.bootstrap import ADMIN_SOCK_NAME, _runtime_dir

    payload = encode_json_frame(request)
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
        sock.settimeout(30.0)
        sock.connect(str(_runtime_dir(runtime_dir) / ADMIN_SOCK_NAME))
        sock.sendall(len(payload).to_bytes(4, "big") + payload)
        size = int.from_bytes(sock.recv(4), "big")
        body = b""
        while len(body) < size:
            chunk = sock.recv(size - len(body))
            if not chunk:
                break
            body += chunk
    response: dict[str, Any] = decode_json_frame(body)
    return response


def _tool(args: argparse.Namespace) -> int:
    import json

    tool, arguments = command_request(args)
    try:
        response = _admin_request(
            getattr(args, "runtime_dir", None),
            {"cmd": "tool call", "args": {"tool": tool, "arguments": arguments}},
        )
    except OSError:
        print("comms: the daemon is not reachable", file=sys.stderr)
        return 3
    if response.get("ok") is not True:
        print(f"comms: {response.get('code', 'INTERNAL_ERROR')}", file=sys.stderr)
        return 4
    print(json.dumps(response["data"], indent=2, sort_keys=True))
    return 0 if response["data"].get("error") is None else 4


def _operator(args: argparse.Namespace) -> int:
    import json

    try:
        request = operator_request(args, stdin=sys.stdin)
    except ValueError as refused:
        print(f"comms: {refused}", file=sys.stderr)
        return EXIT_USAGE
    try:
        response = _admin_request(None, request)
    except OSError:
        print("comms: the daemon is not reachable", file=sys.stderr)
        return 3
    if response.get("ok") is not True:
        print(f"comms: {response.get('code', 'INTERNAL_ERROR')}", file=sys.stderr)
        return 4
    print(json.dumps(response["data"], indent=2, sort_keys=True))
    return 0


def _hello(runtime_dir: str | None) -> Any:
    """The daemon's non-secret security epoch, over the admin socket's ``hello`` control."""
    from comms.transports.telegram.ipc.framing import decode_json_frame, encode_json_frame
    from comms.transports.telegram.runtime.bootstrap import ADMIN_SOCK_NAME, _runtime_dir

    path = _runtime_dir(runtime_dir) / ADMIN_SOCK_NAME

    def hello() -> int:
        payload = encode_json_frame({"control": "hello"})
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            sock.settimeout(5.0)
            sock.connect(str(path))
            sock.sendall(len(payload).to_bytes(4, "big") + payload)
            size = int.from_bytes(sock.recv(4), "big")
            body = b""
            while len(body) < size:
                chunk = sock.recv(size - len(body))
                if not chunk:
                    break
                body += chunk
        response = decode_json_frame(body)
        if response.get("ok") is not True:
            raise OSError("the daemon did not answer hello")
        return int(response["data"]["security_epoch"])

    return hello


def _mcp(args: argparse.Namespace) -> int:
    import anyio

    from comms.mcp.stdio_proxy import Proxy, ProxyError, http_post, serve

    try:
        proxy = Proxy(args.client_seed, hello=_hello(args.runtime_dir), post=http_post(args.daemon))
    except ProxyError as refused:
        print(f"comms: {refused}", file=sys.stderr)
        return EXIT_USAGE
    anyio.run(serve, proxy)
    return 0


def main(argv: list[str] | None = None) -> None:
    argv = sys.argv[1:] if argv is None else argv
    if argv[:1] and argv[0] in FAMILIES:
        code = _tool(build_parser().parse_args(argv))
        if code:
            raise SystemExit(code)
        return
    if argv[:1] and argv[0] in OPERATOR_GROUPS and argv[0] not in LOCAL_GROUPS:
        code = _operator(build_parser().parse_args(argv))
        if code:
            raise SystemExit(code)
        return
    if argv[:1] != ["mcp"]:
        from comms.transports.telegram.cli import main as operator_main

        sys.argv = ["comms", *argv]
        operator_main()
        return
    code = _mcp(build_parser().parse_args(argv))
    if code:
        raise SystemExit(code)
