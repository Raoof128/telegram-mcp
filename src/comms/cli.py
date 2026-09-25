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
    BACKUP_FLOWS,
    CLI_FLOWS,
    LOCAL_COMMANDS,
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
    selftest = sub.add_parser(
        "selftest-daemon",
        help="run the real daemon over deterministic local providers (never production)",
        allow_abbrev=False,
    )
    selftest.add_argument("--state-dir", default=None)
    selftest.add_argument("--runtime-dir", default=None)
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


def _read_credential() -> str:
    """A credential value, typed at the owner's terminal and never echoed (D39-PRE E8a)."""
    import getpass

    if not sys.stdin.isatty():
        raise ValueError("credential values are entered interactively, at a terminal")
    return getpass.getpass("value (not echoed): ")


def _telegram_flow(words: tuple[str, ...]) -> int:
    """``transport telegram login|revoke-session`` (D39-PRE E8a): CLI-driven steps over the
    daemon's retained login admin commands. Phone and code are prompted; a 2FA password is read
    only through ``getpass``; nothing is echoed back."""
    import getpass
    import json

    command = CLI_FLOWS[words]

    def step(args: dict[str, Any]) -> dict[str, Any]:
        response = _admin_request(None, {"cmd": command, "args": args})
        if response.get("ok") is not True:
            raise ValueError(str(response.get("code", "INTERNAL_ERROR")))
        data: dict[str, Any] = response["data"]
        return data

    try:
        if words[-1] == "revoke-session":
            result = step({})
        else:
            if not sys.stdin.isatty():
                raise ValueError("the Telegram login is entered interactively, at a terminal")
            reply = step({"step": "start", "phone": input("phone (+61…): ").strip()})
            handle = reply["login"]
            reply = step({"step": "code", "login": handle, "code": input("code: ").strip()})
            if reply.get("next") == "password":
                password = getpass.getpass("two-step password (not echoed): ")
                reply = step({"step": "password", "login": handle, "password": password})
            result = reply
    except OSError:
        print("comms: the daemon is not reachable", file=sys.stderr)
        return 3
    except ValueError as refused:
        print(f"comms: {refused}", file=sys.stderr)
        return 4
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


def _backup_flow(words: tuple[str, ...], args: argparse.Namespace) -> int:
    """``backup export`` and ``backup import stage`` (D39-PRE E8b): the file moves in 32 KiB
    chunks over the admin socket; both files are written 0600 and never overwritten."""
    import base64
    import hashlib
    import json
    import os

    def step(*command: str, **fields: Any) -> dict[str, Any]:
        response = _admin_request(
            None, {"cmd": "operator", "args": {"command": list(command), **fields}}
        )
        if response.get("ok") is not True:
            raise ValueError(str(response.get("code", "INTERNAL_ERROR")))
        data: dict[str, Any] = response["data"]
        return data

    def write_private(path: Path, data: bytes) -> None:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            os.write(fd, data)
            os.fsync(fd)
        finally:
            os.close(fd)

    base = Path(args.out if words == ("backup", "export") else getattr(args, "from"))
    ciphertext_path, sidecar_path = base.with_suffix(".age"), base.with_suffix(".sig")
    try:
        if words == ("backup", "export"):
            reply = step("backup", "export")
            chunks = [
                base64.b64decode(
                    step("backup", "transfer", "pull", transfer=reply["transfer"], index=i)["chunk"]
                )
                for i in range(reply["chunks"])
            ]
            data = b"".join(chunks)
            if hashlib.sha256(data).hexdigest() != reply["sha256"]:
                raise ValueError("the backup did not arrive intact")
            write_private(ciphertext_path, data)
            write_private(sidecar_path, base64.b64decode(reply["sidecar"]))
            result: dict[str, Any] = {
                "files": [str(ciphertext_path), str(sidecar_path)],
                "binding": reply["binding"],
                "signer_key_id": reply["signer_key_id"],
            }
        else:
            data, sidecar = ciphertext_path.read_bytes(), sidecar_path.read_bytes()
            transfer = step("backup", "transfer", "begin-push", size=len(data),
                            sha256=hashlib.sha256(data).hexdigest())["transfer"]  # fmt: skip
            for index, start in enumerate(range(0, max(len(data), 1), 32 * 1024)):
                chunk = base64.b64encode(data[start : start + 32 * 1024]).decode("ascii")
                step("backup", "transfer", "push", transfer=transfer, index=index, chunk=chunk)
            fields: dict[str, Any] = {"transfer": transfer, "identity": str(Path(args.identity).resolve()),
                                      "sidecar": base64.b64encode(sidecar).decode("ascii")}  # fmt: skip
            if args.trust_key:
                fields["trust_key"] = args.trust_key
            if args.adopt:
                fields["adopt"] = True
            result = step("backup", "import", "stage", **fields)
    except FileExistsError:
        print("comms: the backup files already exist; choose another --out", file=sys.stderr)
        return 4
    except OSError:
        print("comms: the daemon or the backup files are not reachable", file=sys.stderr)
        return 3
    except ValueError as refused:
        print(f"comms: {refused}", file=sys.stderr)
        return 4
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


def _operator(args: argparse.Namespace) -> int:
    import json

    if tuple(args.operator) in BACKUP_FLOWS:
        return _backup_flow(tuple(args.operator), args)

    if tuple(args.operator) in CLI_FLOWS:
        return _telegram_flow(tuple(args.operator))

    try:
        request = operator_request(args, read_value=_read_credential)
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


def _provision(args: argparse.Namespace) -> int:
    """``comms keys provision`` (R-E4): local, before the daemon exists, under its lock."""
    import json
    from datetime import UTC, datetime

    from comms.runtime.paths import CommsPaths, default_state_dir
    from comms.runtime.provision import ProvisionRefused, provision
    from comms.transports.telegram.runtime.bootstrap import _runtime_dir

    state = Path(args.state_dir) if args.state_dir else default_state_dir()
    try:
        report = provision(
            CommsPaths(state), now=datetime.now(UTC), runtime_dir=_runtime_dir(args.runtime_dir)
        )
    except ProvisionRefused as refused:
        print(f"comms: {refused}", file=sys.stderr)
        return 4
    printed = {
        "provisioned": list(report.created),
        "legacy_provisioned": list(report.legacy_created),
    }
    print(json.dumps(printed, indent=2, sort_keys=True))
    return 0


def _rekey(args: argparse.Namespace) -> int:
    """``comms keys rotate comms-db-key`` (A14): local, under the runtime lock, daemon stopped."""
    import json

    from comms.runtime.paths import CommsPaths, default_state_dir
    from comms.runtime.provision import ProvisionRefused, rotate_db_key
    from comms.transports.telegram.runtime.bootstrap import _runtime_dir

    paths = CommsPaths(Path(args.state_dir) if args.state_dir else default_state_dir())
    try:
        version = rotate_db_key(paths, runtime_dir=_runtime_dir(args.runtime_dir))
    except ProvisionRefused as refused:
        print(f"comms: {refused}", file=sys.stderr)
        return 4
    print(json.dumps({"purpose": "comms-db-key", "version": version}, indent=2, sort_keys=True))
    return 0


def _daemon(args: argparse.Namespace, *, selftest: bool) -> int:
    """``comms daemon`` / ``comms selftest-daemon`` (D39-PRE E6): one daemon, one code path.

    The selftest variant differs only in the injected adapter factory (deterministic local
    providers) and in never starting Telethon.
    """
    import asyncio

    from comms.runtime.paths import CommsPaths, default_state_dir
    from comms.runtime.settings import SettingsError, load_settings
    from comms.transports.telegram.runtime.bootstrap import _runtime_dir
    from comms.transports.telegram.runtime.daemon import DaemonConfig, DaemonError, run_daemon

    state = Path(args.state_dir) if args.state_dir else default_state_dir()
    paths = CommsPaths(state)
    try:
        settings = load_settings(paths.settings)
    except SettingsError as refused:
        print(f"comms: {refused}", file=sys.stderr)
        return 4
    factory = None
    if selftest:
        from comms.runtime.selftest import selftest_adapters

        factory = selftest_adapters
    store = getattr(args, "store_dir", None)
    config = DaemonConfig(
        runtime_dir=_runtime_dir(args.runtime_dir),
        state_dir=state,
        key_dir=Path(store) if store else paths.legacy_keys,
        api_id=None if selftest else settings.telegram_api_id,
        comms=True,
        adapters_factory=factory,
    )
    try:
        asyncio.run(run_daemon(config))
    except DaemonError as refused:
        print(f"comms: {refused}", file=sys.stderr)
        return 4
    return 0


def _doctor(args: argparse.Namespace) -> int:
    """``comms doctor`` (D39-PRE E9): the comms state, read-only; works while a daemon runs."""
    import json
    from datetime import UTC, datetime

    from comms.runtime.doctor import run_doctor
    from comms.runtime.paths import CommsPaths, default_state_dir

    state = Path(args.state_dir) if args.state_dir else default_state_dir()
    report = run_doctor(CommsPaths(state), now=datetime.now(UTC))
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["ok"] or not args.production else 1


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
    if argv[:1] == ["doctor"]:
        code = _doctor(build_parser().parse_args(argv))
        if code:
            raise SystemExit(code)
        return
    if argv[:1] in (["daemon"], ["selftest-daemon"]):
        code = _daemon(build_parser().parse_args(argv), selftest=argv[0] == "selftest-daemon")
        if code:
            raise SystemExit(code)
        return
    if argv[:3] == ["keys", "rotate", "comms-db-key"]:
        code = _rekey(build_parser().parse_args(argv))
        if code:
            raise SystemExit(code)
        return
    if tuple(argv[:2]) in LOCAL_COMMANDS:
        code = _provision(build_parser().parse_args(argv))
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
