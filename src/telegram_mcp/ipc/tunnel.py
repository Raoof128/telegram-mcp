"""Tunnel client-certificate SPKI pins: verify, add, rotate with history.

The tunnel ingress (8767, ChatGPT modes only) accepts one pinned mTLS client
certificate. Spec §12.2 stores the current pin as the ``openai_tunnel``
client's ``auth_binding`` — "a pinned digest of the accepted mTLS client
certificate public key (SPKI)". The spec has no table for *previous* pins,
and §3 of the design requires rotation with history retention, so history
lives in a daemon-owned ``0600`` append-only record beside the key store:
one JSON object per line, public material only (an SPKI digest, never a
key). This is an implementation choice and is recorded as such.

A presented pin is accepted only when it is the current record, the record
has not been retired, and its lifetime has not expired (install issues
90-day certificates). Anything unpinned, retired or expired is refused.
"""

from __future__ import annotations

import hashlib
import json
import os
import stat
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path

__all__ = [
    "PIN_LIFETIME_DAYS",
    "TunnelPin",
    "TunnelPinError",
    "add_pin",
    "current_pin",
    "pin_history",
    "rotate_binding",
    "spki_digest",
    "verify_pin",
]

PIN_LIFETIME_DAYS = 90
_DAY_S = 86_400
_HISTORY_NAME = "tunnel-pins.jsonl"
_PIN_RE = "spki:sha256:"


class TunnelPinError(Exception):
    """Pin refusal or record error. Fixed message; public material only."""


@dataclass(frozen=True)
class TunnelPin:
    """One pin record: digest, when pinned, when it expires, when retired."""

    spki: str
    pinned_at: int
    expires_at: int
    retired_at: int | None = None

    def __post_init__(self) -> None:
        if not self.spki.startswith(_PIN_RE) or len(self.spki) != len(_PIN_RE) + 64:
            raise TunnelPinError("invalid SPKI pin format")
        if self.expires_at <= self.pinned_at:
            raise TunnelPinError("pin expiry must follow its creation")


def spki_digest(public_der: bytes) -> str:
    """``spki:sha256:<hex>`` over the DER SubjectPublicKeyInfo."""
    if not isinstance(public_der, bytes) or not public_der:
        raise TunnelPinError("invalid SPKI bytes")
    return _PIN_RE + hashlib.sha256(public_der).hexdigest()


def _history_path(store_dir: str | Path) -> Path:
    return Path(store_dir) / _HISTORY_NAME


def pin_history(store_dir: str | Path) -> tuple[TunnelPin, ...]:
    """Every pin ever recorded, oldest first."""
    path = _history_path(store_dir)
    if not path.exists():
        return ()
    if path.is_symlink() or stat.S_IMODE(path.stat().st_mode) != 0o600:
        raise TunnelPinError("pin history must be a 0600 regular file")
    records: list[TunnelPin] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            records.append(TunnelPin(**json.loads(line)))
        except (TypeError, ValueError) as exc:
            raise TunnelPinError("pin history is corrupt") from exc
    return tuple(records)


def _write_history(store_dir: str | Path, records: tuple[TunnelPin, ...]) -> None:
    path = _history_path(store_dir)
    body = "".join(
        json.dumps(asdict(record), sort_keys=True, separators=(",", ":")) + "\n"
        for record in records
    )
    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=_HISTORY_NAME + ".")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(body)
        os.chmod(tmp_name, 0o600)
        os.replace(tmp_name, path)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def current_pin(store_dir: str | Path) -> TunnelPin | None:
    """The one live pin, or ``None`` when the tunnel is unpinned."""
    live = [record for record in pin_history(store_dir) if record.retired_at is None]
    if len(live) > 1:
        raise TunnelPinError("more than one live tunnel pin")
    return live[0] if live else None


def add_pin(
    store_dir: str | Path,
    spki: str,
    *,
    now: int,
    lifetime_days: int = PIN_LIFETIME_DAYS,
) -> TunnelPin:
    """Pin a client certificate. Refuses while another pin is live."""
    if current_pin(store_dir) is not None:
        raise TunnelPinError("a live pin exists: rotate instead of adding")
    record = TunnelPin(spki=spki, pinned_at=int(now), expires_at=int(now) + lifetime_days * _DAY_S)
    _write_history(store_dir, (*pin_history(store_dir), record))
    return record


def rotate_binding(
    store_dir: str | Path,
    new_spki: str,
    *,
    now: int,
    lifetime_days: int = PIN_LIFETIME_DAYS,
) -> TunnelPin:
    """Retire the live pin and record a new one; history is kept."""
    history = pin_history(store_dir)
    live = [record for record in history if record.retired_at is None]
    if not live:
        raise TunnelPinError("no live pin to rotate")
    replacement = TunnelPin(
        spki=new_spki, pinned_at=int(now), expires_at=int(now) + lifetime_days * _DAY_S
    )
    rotated = tuple(
        TunnelPin(
            spki=record.spki,
            pinned_at=record.pinned_at,
            expires_at=record.expires_at,
            retired_at=int(now) if record.retired_at is None else record.retired_at,
        )
        for record in history
    )
    _write_history(store_dir, (*rotated, replacement))
    return replacement


def verify_pin(store_dir: str | Path, presented_spki: str, *, now: int) -> TunnelPin:
    """Accept only the live, unexpired pin; raise ``TunnelPinError`` otherwise."""
    live = current_pin(store_dir)
    if live is None:
        raise TunnelPinError("tunnel client certificate is not pinned")
    if not isinstance(presented_spki, str) or presented_spki != live.spki:
        raise TunnelPinError("presented certificate does not match the pin")
    if int(now) >= live.expires_at:
        raise TunnelPinError("pinned certificate has expired")
    return live
