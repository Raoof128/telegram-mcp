"""The daemon settings, ``comms/comms.json`` (D39-PRE Task E3; R-E5).

Non-secret configuration only, kept apart from ``comms.db`` so a database that will not open
never stops the daemon knowing how to start. The file is 0600 in a 0700 directory, owned by the
daemon user; strict JSON (duplicate keys refused), a closed schema (unknown keys refused), and no
key whose name reads like a secret anywhere in it. Every listener binds loopback: the tunnels
forward to it. Absent file: loopback defaults, no remote listener, no webhook listener. There is
no programmatic writer in v0.3; the owner edits the file, and any future writer must replace it
atomically.

Schema::

    {"local_port": 8766, "webhook_port": 8768, "telegram_api_id": 12345,
     "telegram_delivery_actor": "telegram_bot" | "telegram_user",
     "meta": {"phone_number_id": "…", "waba_id": "…"},
     "backup_recipient": "age1…",
     "remote": {"issuer": "https://…", "port": 8767, "client": "cli_…", "client_id": "…",
                "redirect_uris": ["https://…"], "owner": "…"}}
"""

from __future__ import annotations

import os
import re
import stat
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from comms.core.strict_json import strict_json_loads
from comms.mcp.oauth.server import OAuthSettings
from comms.runtime.adapters import AdapterSettings

__all__ = ["HOST", "DaemonSettings", "RemoteSettings", "SettingsError", "load_settings"]

HOST = "127.0.0.1"
LOCAL_PORT = 8766  # comms.transports.telegram.runtime.bootstrap.LOCAL_PORT
_SECRETISH = ("token", "secret", "key", "password", "seed")
_TOP = {
    "host",
    "telegram_api_id",
    "retention",
    "local_port",
    "webhook_port",
    "telegram_delivery_actor",
    "meta",
    "remote",
    "backup_recipient",
}
_META = {"phone_number_id", "waba_id"}
_REMOTE = {"issuer", "port", "client", "client_id", "redirect_uris", "owner"}
_CLIENT = re.compile(r"^cli_[a-z2-7]{26}$")
_DIGITS = re.compile(r"^[0-9]{1,32}$")
_AGE = re.compile(r"^age1[0-9a-z]{58}$")


# R-E11: the comms-only retention periods; the four legacy ones stay in the legacy settings.
RETENTION_DEFAULTS = {"campaign_body_days": 365, "identity_retention_days": 365}


class SettingsError(ValueError):
    """comms.json is refused; the message is fixed."""


@dataclass(frozen=True)
class RemoteSettings:
    oauth: OAuthSettings
    client: str
    port: int


@dataclass(frozen=True)
class DaemonSettings:
    host: str = HOST
    local_port: int = LOCAL_PORT
    webhook_port: int | None = None
    adapter: AdapterSettings = field(default_factory=AdapterSettings)
    remote: RemoteSettings | None = None
    backup_recipient: str | None = None
    telegram_api_id: int | None = None  # public app id; api_hash stays in the Keychain
    retention_days: Mapping[str, int] = field(default_factory=lambda: dict(RETENTION_DEFAULTS))


def _no_secrets(value: Any) -> None:
    if isinstance(value, dict):
        for name, inner in value.items():
            if any(word in name.lower() for word in _SECRETISH):
                raise SettingsError("comms.json holds no secrets")
            _no_secrets(inner)
    elif isinstance(value, list):
        for inner in value:
            _no_secrets(inner)


def _object(value: Any, allowed: set[str], where: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise SettingsError(f"comms.json: {where} must be an object")
    if set(value) - allowed:
        raise SettingsError(f"comms.json: unknown key in {where}")
    return value


def _port(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 1024 <= value <= 65535:
        raise SettingsError("comms.json: a port is an integer from 1024 to 65535")
    return value


def _https(value: Any) -> str:
    if not isinstance(value, str) or not re.fullmatch(
        r"https://[A-Za-z0-9.-]+(:[0-9]{1,5})?(/[^\s]*)?", value
    ):
        raise SettingsError("comms.json: remote URLs must be https")
    return value.rstrip("/")


def _text(value: Any, pattern: re.Pattern[str], what: str) -> str:
    if not isinstance(value, str) or not pattern.fullmatch(value):
        raise SettingsError(f"comms.json: {what} is malformed")
    return value


def _remote(value: Any) -> RemoteSettings:
    remote = _object(value, _REMOTE, "remote")
    if set(remote) != _REMOTE:
        raise SettingsError("comms.json: remote needs every field")
    issuer = _https(remote["issuer"])
    uris = remote["redirect_uris"]
    if not isinstance(uris, list) or not uris:
        raise SettingsError("comms.json: remote.redirect_uris is a non-empty list")
    word = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
    return RemoteSettings(
        oauth=OAuthSettings(
            issuer=issuer,
            resource=f"{issuer}/mcp",
            client_id=_text(remote["client_id"], word, "remote.client_id"),
            redirect_uris=tuple(_https(u) for u in uris),
            owner=_text(remote["owner"], word, "remote.owner"),
        ),
        client=_text(remote["client"], _CLIENT, "remote.client"),
        port=_port(remote["port"]),
    )


def load_settings(path: Path) -> DaemonSettings:
    path = Path(path)
    try:
        info = os.lstat(path)
    except FileNotFoundError:
        return DaemonSettings()
    if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid():
        raise SettingsError("comms.json must be a regular file owned by the daemon user")
    if stat.S_IMODE(info.st_mode) != 0o600:
        raise SettingsError("comms.json must be 0600")
    if stat.S_IMODE(path.parent.stat().st_mode) != 0o700:
        raise SettingsError("the comms directory must be 0700")
    try:
        raw = strict_json_loads(path.read_text(encoding="utf-8"))
    except (ValueError, UnicodeDecodeError):
        raise SettingsError("comms.json is not valid JSON") from None
    top = _object(raw, _TOP, "the top level")
    _no_secrets(top)
    if top.get("host", HOST) != HOST:
        raise SettingsError("the listeners bind loopback only")
    actor = top.get("telegram_delivery_actor", "telegram_bot")
    if actor not in ("telegram_bot", "telegram_user"):
        raise SettingsError("comms.json: telegram_delivery_actor is telegram_bot or telegram_user")
    meta = _object(top.get("meta", {}), _META, "meta")
    api_id = top.get("telegram_api_id")
    if api_id is not None and (
        isinstance(api_id, bool) or not isinstance(api_id, int) or api_id < 1
    ):
        raise SettingsError("comms.json: telegram_api_id is a positive integer")
    retention = _object(top.get("retention", {}), set(RETENTION_DEFAULTS), "retention")
    for name, value in retention.items():
        if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 3650:
            raise SettingsError(f"comms.json: retention.{name} is 1 to 3650 days")
    return DaemonSettings(
        retention_days={**RETENTION_DEFAULTS, **retention},
        telegram_api_id=api_id,
        local_port=_port(top.get("local_port", LOCAL_PORT)),
        webhook_port=None if top.get("webhook_port") is None else _port(top["webhook_port"]),
        adapter=AdapterSettings(
            telegram_delivery_actor=actor,
            meta_phone_number_id=None
            if "phone_number_id" not in meta
            else _text(meta["phone_number_id"], _DIGITS, "meta.phone_number_id"),
            meta_waba_id=None
            if "waba_id" not in meta
            else _text(meta["waba_id"], _DIGITS, "meta.waba_id"),
        ),
        remote=None if top.get("remote") is None else _remote(top["remote"]),
        backup_recipient=None
        if top.get("backup_recipient") is None
        else _text(top["backup_recipient"], _AGE, "backup_recipient"),
    )
