"""``telegram-mcp doctor``: honest checks, including the OFF probe.

Each check reports ``ok``, ``warn``, ``fail`` or ``skipped``, and a check
this phase cannot honestly perform reports ``skipped`` with the reason —
never ``ok``. ``--production`` therefore **fails** in Phase 2a: the
production gate set includes Telegram authentication state, the local
endpoint's credential rejection and the service-account elevation probes,
none of which exist to be verified yet. That failure is the correct answer,
not a gap in the checker.

The OFF probe is the design's core assertion: when the runtime is off there
are no MCP ports listening, no admin socket, no consent socket, no tunnel
client and no runtime process.

Host-mutating or host-interrogating probes (``dscl``, ``sudo -n -u``) run
only when ``allow_host_probes`` is set, so the default run is headless and
side-effect free.
"""

from __future__ import annotations

import asyncio
import hashlib
import socket
import sqlite3
import stat
import subprocess
import sys
from dataclasses import dataclass, field
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any

from telegram_mcp.consent.broker import ConsentBroker, ConsentError
from telegram_mcp.consent.challenge import StubSigner, synthetic_exposure_digest
from telegram_mcp.ipc.tunnel import TunnelPinError, current_pin
from telegram_mcp.keys.pairing import export_public
from telegram_mcp.keys.registry import KEY_REGISTRY
from telegram_mcp.keys.store import FILE_BACKED_KEYS, KeyStoreError, key_id, set_store_dir
from telegram_mcp.runtime.bootstrap import (
    ADMIN_SOCK_NAME,
    CHATGPT_PORT,
    CONSENT_SOCK_NAME,
    LOCAL_PORT,
    RUNTIME_LABEL,
    TUNNEL_LABEL,
    _list_processes_ps,
)

__all__ = [
    "CHECKS",
    "PRODUCTION_REQUIRED",
    "CheckResult",
    "DoctorContext",
    "doctor",
]

OK = "ok"
WARN = "warn"
FAIL = "fail"
SKIPPED = "skipped"

_PROBE_TIMEOUT_S = 0.5
_MINIMUM_PYTHON = (3, 12)  # spec §4 runtime floor, reported not enforced here


@dataclass(frozen=True)
class DoctorContext:
    """What doctor is allowed to look at on this host."""

    runtime_dir: Path | None = None
    store_dir: Path | None = None
    db_path: Path | None = None
    conn: sqlite3.Connection | None = None
    service_accounts: tuple[str, ...] = ("telegram-mcpd", TUNNEL_LABEL)
    allow_host_probes: bool = False
    ports: tuple[int, ...] = (LOCAL_PORT, CHATGPT_PORT)


@dataclass(frozen=True)
class CheckResult:
    """One check's verdict."""

    name: str
    status: str
    detail: str
    extra: dict[str, Any] = field(default_factory=dict)


def _check_python(ctx: DoctorContext) -> CheckResult:
    if sys.version_info[:2] < _MINIMUM_PYTHON:
        return CheckResult("runtime.python", FAIL, "Python 3.12 or newer is required")
    return CheckResult("runtime.python", OK, f"Python {sys.version.split()[0]}")


def _check_mcp_sdk(ctx: DoctorContext) -> CheckResult:
    try:
        installed = version("mcp")
    except PackageNotFoundError:
        return CheckResult("runtime.mcp_sdk", FAIL, "the mcp SDK is not installed")
    if not installed.startswith("2."):
        return CheckResult("runtime.mcp_sdk", FAIL, f"mcp {installed} is not 2.x")
    return CheckResult("runtime.mcp_sdk", OK, f"mcp {installed}")


def _check_keys(ctx: DoctorContext) -> CheckResult:
    """Spec §9.6.1 inventory for the rows the runtime owns as files."""
    if ctx.store_dir is None:
        return CheckResult("keys.inventory", SKIPPED, "no key store directory was supplied")
    try:
        set_store_dir(ctx.store_dir)
    except KeyStoreError as exc:
        return CheckResult("keys.inventory", FAIL, str(exc))
    ids: dict[str, str] = {}
    missing: list[str] = []
    for name in sorted(FILE_BACKED_KEYS):
        try:
            ids[name] = key_id(name)
        except KeyStoreError:
            missing.append(name)
    if missing:
        return CheckResult(
            "keys.inventory", FAIL, f"unprovisioned runtime keys: {', '.join(missing)}"
        )
    if len(set(ids.values())) != len(ids):
        return CheckResult("keys.inventory", FAIL, "two key purposes share one key id")
    external = sorted(
        name
        for name, spec in KEY_REGISTRY.items()
        if spec.required_phase == 2 and name not in FILE_BACKED_KEYS
    )
    return CheckResult(
        "keys.inventory",
        OK,
        f"{len(ids)} runtime keys verified with distinct ids",
        {"externally_owned": external},
    )


def _check_pairing(ctx: DoctorContext) -> CheckResult:
    """The agent's two pins must both be present for consent to work."""
    if ctx.store_dir is None:
        return CheckResult("keys.pairing", SKIPPED, "no key store directory was supplied")
    try:
        set_store_dir(ctx.store_dir)
    except KeyStoreError as exc:
        return CheckResult("keys.pairing", FAIL, str(exc))
    absent: list[str] = []
    for name in ("agent-approval-key", "agent-transport-key"):
        try:
            export_public(name)
        except KeyStoreError:
            absent.append(name)
    if absent:
        return CheckResult("keys.pairing", WARN, f"unpaired agent keys: {', '.join(absent)}")
    return CheckResult("keys.pairing", OK, "approval and transport publics are pinned")


def _check_database(ctx: DoctorContext) -> CheckResult:
    conn = ctx.conn
    opened = False
    if conn is None:
        if ctx.db_path is None:
            return CheckResult("db.integrity", SKIPPED, "no database was supplied")
        try:
            conn = sqlite3.connect(ctx.db_path)
            conn.execute("PRAGMA foreign_keys = ON")
            opened = True
        except sqlite3.Error as exc:
            return CheckResult("db.integrity", FAIL, f"database could not be opened: {exc}")
    try:
        foreign_keys = conn.execute("PRAGMA foreign_keys").fetchone()
        if not foreign_keys or int(foreign_keys[0]) != 1:
            return CheckResult("db.integrity", FAIL, "foreign_keys is not ON")
        check = conn.execute("PRAGMA quick_check").fetchone()
        if not check or check[0] != "ok":
            return CheckResult("db.integrity", FAIL, "quick_check failed")
        conn.execute("SELECT count(*) FROM security_state").fetchone()
    except sqlite3.Error as exc:
        return CheckResult("db.integrity", FAIL, f"database is unusable: {exc}")
    finally:
        if opened:
            conn.close()
    return CheckResult("db.integrity", OK, "foreign_keys=ON, quick_check ok, schema readable")


def _check_socket_permissions(ctx: DoctorContext) -> CheckResult:
    if ctx.runtime_dir is None:
        return CheckResult("sockets.permissions", SKIPPED, "no runtime directory was supplied")
    directory = Path(ctx.runtime_dir)
    if not directory.exists():
        return CheckResult("sockets.permissions", OK, "runtime directory is absent (runtime OFF)")
    if stat.S_IMODE(directory.stat().st_mode) != 0o770:
        return CheckResult("sockets.permissions", FAIL, "runtime directory is not mode 0770")
    for name in (ADMIN_SOCK_NAME, CONSENT_SOCK_NAME):
        path = directory / name
        if path.exists() and stat.S_IMODE(path.stat().st_mode) != 0o660:
            return CheckResult("sockets.permissions", FAIL, f"{name} is not mode 0660")
    return CheckResult("sockets.permissions", OK, "directory 0770, sockets 0660")


def _check_tunnel_pin(ctx: DoctorContext) -> CheckResult:
    if ctx.store_dir is None:
        return CheckResult("tunnel.pin", SKIPPED, "no key store directory was supplied")
    try:
        pin = current_pin(ctx.store_dir)
    except TunnelPinError as exc:
        return CheckResult("tunnel.pin", FAIL, str(exc))
    if pin is None:
        return CheckResult("tunnel.pin", WARN, "no tunnel client certificate is pinned")
    return CheckResult(
        "tunnel.pin", OK, "one live pin", {"expires_at": pin.expires_at, "spki": pin.spki}
    )


def _check_consent_selftest(ctx: DoctorContext) -> CheckResult:
    async def run() -> str:
        stub = StubSigner(seed=0x0D)
        broker = ConsentBroker(
            challenge_key=b"\x01" * 32, agent_verify=stub.verify, runtime_id=b"\x02" * 16
        )
        handle = await broker.issue(
            tool="telegram_status",
            request_hmac="ab" * 32,
            principal="prn_" + "a" * 26,
            client="tcl_" + "b" * 26,
            account="tga_" + "c" * 26,
            policy_epoch=1,
            project_scope_digest="1" * 64,
            security_epoch=1,
            display_digest="2" * 64,
            exposure_snapshot_digest=synthetic_exposure_digest(),
        )
        challenge = broker.challenge_bytes(handle)
        envelope = {
            "challenge_sha256": hashlib.sha256(challenge).hexdigest(),
            "sig": stub.sign(challenge),
            "key_id": stub.key_id,
        }
        tampered = dict(envelope, sig=stub.sign(challenge + b"x"))
        try:
            await broker.consume(handle, tampered)
        except ConsentError:
            pass
        else:
            return "a tampered approval was accepted"
        await broker.consume(handle, envelope)
        try:
            await broker.consume(handle, envelope)
        except ConsentError:
            return ""
        return "an approval was consumed twice"

    try:
        problem = asyncio.run(run())
    except Exception as exc:  # noqa: BLE001 -- doctor reports, never raises
        return CheckResult("consent.selftest", FAIL, f"self-test raised: {exc!r}")
    if problem:
        return CheckResult("consent.selftest", FAIL, problem)
    return CheckResult(
        "consent.selftest", OK, "challenge issued, tamper refused, exact-once consume"
    )


def _check_service_accounts(ctx: DoctorContext) -> CheckResult:
    if not ctx.allow_host_probes:
        return CheckResult(
            "service_accounts.separation", SKIPPED, "host probes are disabled for this run"
        )
    if sys.platform != "darwin":  # pragma: no cover - macOS-only probe
        return CheckResult("service_accounts.separation", SKIPPED, "probe is macOS-only")
    problems: list[str] = []
    for account in ctx.service_accounts:
        shell = subprocess.run(
            ["/usr/bin/dscl", ".", "-read", f"/Users/{account}", "UserShell"],
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
        )
        if shell.returncode != 0:
            problems.append(f"{account} does not exist")
        elif "/usr/bin/false" not in shell.stdout:
            problems.append(f"{account} has an interactive shell")
        elevate = subprocess.run(
            ["/usr/bin/sudo", "-n", "-u", account, "true"],
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
        )
        if elevate.returncode == 0:
            problems.append(f"non-interactive elevation to {account} succeeded")
    if problems:
        return CheckResult("service_accounts.separation", FAIL, "; ".join(problems))
    return CheckResult(
        "service_accounts.separation", OK, "both accounts non-login and non-impersonable"
    )


def _port_is_closed(port: int) -> bool:
    for family, address in ((socket.AF_INET, "127.0.0.1"), (socket.AF_INET6, "::1")):
        with socket.socket(family, socket.SOCK_STREAM) as probe:
            probe.settimeout(_PROBE_TIMEOUT_S)
            try:
                probe.connect((address, port))
            except OSError:
                continue
            return False
    return True


def _check_off_probe(ctx: DoctorContext) -> CheckResult:
    listening = [port for port in ctx.ports if not _port_is_closed(port)]
    sockets_present: list[str] = []
    if ctx.runtime_dir is not None:
        directory = Path(ctx.runtime_dir)
        sockets_present = [
            name
            for name in (ADMIN_SOCK_NAME, CONSENT_SOCK_NAME)
            if (directory / name).exists() or (directory / name).is_symlink()
        ]
    running = [
        entry.pid
        for entry in _list_processes_ps()
        if any(label in " ".join(entry.argv) for label in (RUNTIME_LABEL, TUNNEL_LABEL))
    ]
    problems: list[str] = []
    if listening:
        problems.append(f"ports still listening: {listening}")
    if sockets_present:
        problems.append(f"sockets still present: {sockets_present}")
    if running:
        problems.append(f"runtime or tunnel processes still running: {running}")
    if problems:
        return CheckResult("off.probe", FAIL, "; ".join(problems))
    return CheckResult(
        "off.probe", OK, "no ports, no sockets, no runtime or tunnel process, no Telegram link"
    )


def _skipped_until_phase(name: str, what: str, phase: str) -> CheckResult:
    return CheckResult(name, SKIPPED, f"{what} does not exist before {phase}")


def _check_telegram_auth(ctx: DoctorContext) -> CheckResult:
    return _skipped_until_phase("telegram.auth_state", "the Telegram adapter", "Phase 4")


def _check_endpoint_credentials(ctx: DoctorContext) -> CheckResult:
    return _skipped_until_phase(
        "mcp.endpoint_credentials", "the credential-checked local endpoint", "Phase 4"
    )


def _check_tunnel_tls(ctx: DoctorContext) -> CheckResult:
    return _skipped_until_phase("tunnel.tls_trust", "the tunnel ingress", "Phase 6")


_CHECK_FUNCTIONS = (
    _check_python,
    _check_mcp_sdk,
    _check_keys,
    _check_pairing,
    _check_database,
    _check_socket_permissions,
    _check_tunnel_pin,
    _check_consent_selftest,
    _check_service_accounts,
    _check_telegram_auth,
    _check_endpoint_credentials,
    _check_tunnel_tls,
)

CHECKS: tuple[str, ...] = (
    "runtime.python",
    "runtime.mcp_sdk",
    "keys.inventory",
    "keys.pairing",
    "db.integrity",
    "sockets.permissions",
    "tunnel.pin",
    "consent.selftest",
    "service_accounts.separation",
    "telegram.auth_state",
    "mcp.endpoint_credentials",
    "tunnel.tls_trust",
    "off.probe",
)

# The production gate set. Phase 2a cannot satisfy it, by design.
PRODUCTION_REQUIRED: frozenset[str] = frozenset(
    {
        "runtime.python",
        "runtime.mcp_sdk",
        "keys.inventory",
        "keys.pairing",
        "db.integrity",
        "sockets.permissions",
        "consent.selftest",
        "service_accounts.separation",
        "telegram.auth_state",
        "mcp.endpoint_credentials",
        "tunnel.tls_trust",
    }
)

_BY_NAME = {name: func for name, func in zip(CHECKS[:-1], _CHECK_FUNCTIONS, strict=True)}
_BY_NAME["off.probe"] = _check_off_probe


def doctor(
    checks: tuple[str, ...] | None = None,
    *,
    context: DoctorContext | None = None,
    production: bool = False,
    off: bool = False,
) -> dict[str, Any]:
    """Run the selected checks and return the report."""
    ctx = context or DoctorContext()
    selected = checks if checks is not None else tuple(n for n in CHECKS if n != "off.probe")
    if off and "off.probe" not in selected:
        selected = (*selected, "off.probe")
    unknown = set(selected) - set(_BY_NAME)
    if unknown:
        raise ValueError(f"unknown doctor check: {sorted(unknown)}")
    results = [_BY_NAME[name](ctx) for name in selected]
    status = OK
    unmet: list[str] = []
    if any(result.status == FAIL for result in results):
        status = FAIL
    if production:
        by_name = {result.name: result for result in results}
        for name in sorted(PRODUCTION_REQUIRED):
            result = by_name.get(name)
            if result is None or result.status != OK:
                unmet.append(name)
        if unmet:
            status = FAIL
    return {
        "status": status,
        "production": production,
        "off": off,
        "unmet_production_checks": tuple(unmet),
        "checks": [
            {
                "name": result.name,
                "status": result.status,
                "detail": result.detail,
                **({"extra": result.extra} if result.extra else {}),
            }
            for result in results
        ],
    }
