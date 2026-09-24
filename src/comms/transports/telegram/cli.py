"""Operator entry point: `demo` (Phase 1) plus the Phase-2a runtime verbs.

``demo`` is untouched from Phase 1: it still serves the synthetic profile on
loopback and never reaches Telegram.

The Phase-2 verbs split exactly as design §2 requires. Bootstrap control —
``start``, ``stop``, ``status`` — works while the runtime is OFF and never
needs the runtime socket. Runtime admin IPC — everything under ``admin`` —
exists only while READY and is proxied to the admin socket, where the
router decides. ``doctor`` runs locally and exits non-zero when a check
fails or a requested production gate is unmet.

There are no test-only flags. A flag that weakened a production path would
be a backdoor, so the headless tests drive ``run_lifecycle`` and
``serve_admin`` directly instead, and the CLI's own argument parsing is
covered through ``--help``.

Configuration and startup failures print one fixed line and exit non-zero;
validation objects are never dumped, because they quote the offending value.
"""

import argparse
import json
import logging
import os
import socket
import sys
from pathlib import Path
from typing import Any

logger = logging.getLogger("telegram_mcp")

EXIT_OK = 0
EXIT_FAILURE = 1
EXIT_USAGE = 2
EXIT_NOT_RUNNING = 3
EXIT_REFUSED = 4
EXIT_NOT_IN_PHASE = 5
# 6 was EXIT_PRESENCE (PRESENCE_REQUIRED); retired by comms spec v0.2, never reassigned.
EXIT_PERMISSION = 7

_ADMIN_EXITS = {
    "MALFORMED_REQUEST": EXIT_USAGE,
    "UNKNOWN_COMMAND": EXIT_USAGE,
    "NOT_AVAILABLE_IN_PHASE": EXIT_NOT_IN_PHASE,
    "PERMISSION_DENIED": EXIT_PERMISSION,
    "INTERNAL_ERROR": EXIT_FAILURE,
}


def _init_safe_logging() -> None:
    from comms.transports.telegram.observability.logging import install_safe_logging

    install_safe_logging()


def _fail(message: str, code: int) -> int:
    print(f"telegram-mcp: {message}", file=sys.stderr)
    return code


def _emit(payload: Any) -> None:
    print(json.dumps(payload, indent=2, sort_keys=True, default=str))


def _mode_from(args: argparse.Namespace) -> str:
    if args.chatgpt:
        return "chatgpt"
    if args.all:
        return "all"
    return "local"


def _cmd_demo(args: argparse.Namespace) -> int:
    from pydantic import ValidationError

    from comms.transports.telegram.config import DemoConfig, validate_environment

    try:
        validate_environment(os.environ)
        config = DemoConfig(host=args.host, port=args.port)
    except (ValidationError, ValueError):
        return _fail("invalid demo configuration.", EXIT_USAGE)

    from comms.transports.telegram.server import create_app

    app = create_app(config)
    logger.info("telegram-mcp demo starting")
    print(f"telegram-mcp demo listening on {config.host}:{config.port}/mcp (synthetic build)")
    try:
        import uvicorn

        uvicorn.run(app, host=config.host, port=config.port, access_log=False)
    except OSError:
        return _fail("failed to bind demo server.", EXIT_FAILURE)
    logger.info("telegram-mcp demo stopped")
    return EXIT_OK


def _cmd_serve(args: argparse.Namespace) -> int:
    return _fail(
        "serve is not a separate verb in this build: use `telegram-mcp start`"
        " (runtime) or `telegram-mcp demo` (synthetic).",
        EXIT_NOT_IN_PHASE,
    )


def _cmd_start(args: argparse.Namespace) -> int:
    from comms.transports.telegram.runtime.bootstrap import LaunchctlJobControl, start_all

    try:
        report = start_all(mode=_mode_from(args), job_control=LaunchctlJobControl())
    except ValueError:
        return _fail("unknown start mode.", EXIT_USAGE)
    except RuntimeError:
        return _fail("runtime did not reach READY; run doctor.", EXIT_FAILURE)
    except OSError:
        return _fail("could not reach the service manager.", EXIT_FAILURE)
    _emit(report)
    return EXIT_OK


def _cmd_stop(args: argparse.Namespace) -> int:
    from comms.transports.telegram.runtime.bootstrap import LaunchctlJobControl, stop_all

    try:
        report = stop_all(job_control=LaunchctlJobControl())
    except OSError:
        return _fail("could not reach the service manager.", EXIT_FAILURE)
    _emit(report)
    return EXIT_OK


def _cmd_status(args: argparse.Namespace) -> int:
    from comms.transports.telegram.runtime.bootstrap import bootstrap_status

    _emit(bootstrap_status())
    return EXIT_OK


def _cmd_doctor(args: argparse.Namespace) -> int:
    from comms.transports.telegram.doctor import DoctorContext, doctor

    context = DoctorContext(
        runtime_dir=Path(args.runtime_dir) if args.runtime_dir else None,
        store_dir=Path(args.store_dir) if args.store_dir else None,
        db_path=Path(args.db) if args.db else None,
        allow_host_probes=args.production,
    )
    report = doctor(context=context, production=args.production, off=args.off)
    _emit(report)
    return EXIT_OK if report["status"] == "ok" else EXIT_FAILURE


def _parse_admin_args(pairs: list[str]) -> dict[str, Any]:
    args: dict[str, Any] = {}
    for pair in pairs:
        key, sep, raw = pair.partition("=")
        if not sep or not key:
            raise ValueError("arguments must be key=value")
        try:
            args[key] = json.loads(raw)
        except ValueError:
            args[key] = raw
    return args


def _cmd_admin(args: argparse.Namespace) -> int:
    from comms.transports.telegram.ipc.framing import decode_json_frame, encode_json_frame
    from comms.transports.telegram.runtime.bootstrap import ADMIN_SOCK_NAME, _runtime_dir

    command = " ".join(args.command)
    try:
        payload = encode_json_frame({"cmd": command, "args": _parse_admin_args(args.arg)})
    except ValueError as exc:
        return _fail(f"{exc}.", EXIT_USAGE)
    admin_path = _runtime_dir(args.runtime_dir) / ADMIN_SOCK_NAME
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            sock.settimeout(args.timeout)
            sock.connect(str(admin_path))
            sock.sendall(len(payload).to_bytes(4, "big") + payload)
            header = sock.recv(4)
            if len(header) != 4:
                return _fail("admin socket closed without a response.", EXIT_FAILURE)
            remaining = int.from_bytes(header, "big")
            body = b""
            while len(body) < remaining:
                chunk = sock.recv(remaining - len(body))
                if not chunk:
                    break
                body += chunk
    except (FileNotFoundError, ConnectionRefusedError):
        return _fail("runtime is not running.", EXIT_NOT_RUNNING)
    except OSError:
        return _fail("admin socket is unreachable.", EXIT_FAILURE)
    try:
        response = decode_json_frame(body)
    except Exception:  # noqa: BLE001 -- any decode failure is one fixed line
        return _fail("admin response was malformed.", EXIT_FAILURE)
    if response.get("ok") is True:
        _emit(response.get("data"))
        return EXIT_OK
    code = str(response.get("code", "INTERNAL_ERROR"))
    print(f"telegram-mcp: {code}: {response.get('reason', '')}".rstrip(), file=sys.stderr)
    return _ADMIN_EXITS.get(code, EXIT_REFUSED)


def _cmd_keys(args: argparse.Namespace) -> int:
    from comms.transports.telegram.keys.store import (
        FILE_BACKED_KEYS,
        KeyStoreError,
        key_id,
        provision_missing,
        set_store_dir,
    )

    try:
        if args.action == "provision":
            # Phases 2 and 3: the runtime's own file-backed rows. `keys list`
            # reports every FILE_BACKED_KEYS row, so provisioning a subset
            # would leave the two verbs disagreeing.
            created = provision_missing(args.store_dir, phases=(2, 3))
            _emit({"store_dir": args.store_dir, "provisioned": created})
            return EXIT_OK
        set_store_dir(args.store_dir)
        _emit({name: key_id(name) for name in sorted(FILE_BACKED_KEYS)})
    except KeyStoreError as exc:
        return _fail(f"{exc}.", EXIT_FAILURE)
    return EXIT_OK


def _cmd_rotate(args: argparse.Namespace) -> int:
    import time

    from comms.transports.telegram.ipc.tunnel import (
        TunnelPinError,
        add_pin,
        current_pin,
        rotate_binding,
    )

    try:
        now = int(time.time())
        if current_pin(args.store_dir) is None:
            record = add_pin(args.store_dir, args.spki, now=now)
        else:
            record = rotate_binding(args.store_dir, args.spki, now=now)
    except TunnelPinError as exc:
        return _fail(f"{exc}.", EXIT_FAILURE)
    _emit({"spki": record.spki, "pinned_at": record.pinned_at, "expires_at": record.expires_at})
    return EXIT_OK


def _cmd_daemon(args: argparse.Namespace) -> int:
    import asyncio

    from comms.transports.telegram.runtime.daemon import DaemonConfig, DaemonError, run_daemon

    test_dc = None
    if args.test_dc:
        try:
            dc, ip, port = args.test_dc.split(":")
            test_dc = (int(dc), ip, int(port))
        except ValueError:
            return _fail("--test-dc must be DC:IP:PORT.", EXIT_USAGE)
    config = DaemonConfig(
        runtime_dir=Path(args.runtime_dir),
        state_dir=Path(args.state_dir),
        key_dir=Path(args.store_dir),
        api_id=args.api_id,
        test_dc=test_dc,
        port=args.port,
        admin_group=args.admin_group,
    )
    try:
        asyncio.run(run_daemon(config))
    except DaemonError as exc:
        return _fail(f"{exc}.", EXIT_FAILURE)
    except KeyboardInterrupt:
        pass
    return EXIT_OK


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="telegram-mcp")
    # dest="verb": the admin subcommand owns the name "command".
    sub = parser.add_subparsers(dest="verb", required=True)

    demo = sub.add_parser("demo", help="serve the synthetic profile on loopback")
    demo.add_argument("--host", default="127.0.0.1")
    demo.add_argument("--port", type=int, default=8766)

    start = sub.add_parser("start", help="start the runtime and tunnel")
    modes = start.add_mutually_exclusive_group()
    modes.add_argument("--local", action="store_true", help="runtime, port 8766")
    modes.add_argument("--chatgpt", action="store_true", help="adds the tunnel client, port 8767")
    modes.add_argument("--all", action="store_true", help="both listeners")

    sub.add_parser("serve", help="not a separate verb in this build; see start and demo")
    sub.add_parser("stop", help="drain and stop every job (no-op while OFF)")
    sub.add_parser("status", help="report runtime state; works while OFF")

    doctor_parser = sub.add_parser("doctor", help="run the health checks")
    doctor_parser.add_argument("--production", action="store_true", help="apply the release gate")
    doctor_parser.add_argument("--off", action="store_true", help="assert nothing is running")
    doctor_parser.add_argument("--runtime-dir", default=None)
    doctor_parser.add_argument("--store-dir", default=None)
    doctor_parser.add_argument("--db", default=None)

    admin = sub.add_parser("admin", help="send one operator command to the runtime")
    admin.add_argument("command", nargs="+", help="a spec §33 command, e.g. lock status")
    admin.add_argument("--arg", action="append", default=[], metavar="KEY=VALUE")
    admin.add_argument("--runtime-dir", default=None)
    admin.add_argument("--timeout", type=float, default=5.0)

    keys = sub.add_parser("keys", help="provision or list the runtime key inventory")
    keys.add_argument("action", choices=("provision", "list"))
    keys.add_argument("--store-dir", required=True)

    rotate = sub.add_parser("rotate", help="rotate the tunnel client-certificate pin")
    rotate.add_argument("target", choices=("tunnel-binding",))
    rotate.add_argument("spki")
    rotate.add_argument("--store-dir", required=True)

    daemon = sub.add_parser("daemon", help="run the Telegram daemon in the foreground")
    daemon.add_argument("--runtime-dir", required=True)
    daemon.add_argument("--state-dir", required=True)
    daemon.add_argument("--store-dir", required=True)
    daemon.add_argument("--api-id", type=int, default=None)
    daemon.add_argument("--test-dc", default=None)
    daemon.add_argument("--port", type=int, default=8766)
    daemon.add_argument("--admin-group", default=None, help="production: telegram-mcp-admin")
    return parser


_COMMANDS = {
    "demo": _cmd_demo,
    "start": _cmd_start,
    "stop": _cmd_stop,
    "status": _cmd_status,
    "doctor": _cmd_doctor,
    "serve": _cmd_serve,
    "admin": _cmd_admin,
    "keys": _cmd_keys,
    "rotate": _cmd_rotate,
    "daemon": _cmd_daemon,
}


def main() -> None:
    args = _build_parser().parse_args()
    _init_safe_logging()
    code = _COMMANDS[args.verb](args)
    if code != EXIT_OK:
        raise SystemExit(code)


if __name__ == "__main__":  # `python -m telegram_mcp.cli`
    main()
