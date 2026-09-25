"""Backup export and staged import (comms v0.3 Task B27).

**Export**: build the ``comms-backup/v1`` payload, age-encrypt it to the operator's
recipient, sign the ciphertext with the active ``backup-key`` (``comms-backup-signature/v1``)
and record ``admin.backup_export`` through the writer.

**Staged import** runs trust → verify → decrypt → decode → binding → diff and changes
nothing:

- *trust*: the signer must be trusted for import here, or be named by ``trust_key`` for
  this one staged import (no standing trust is created);
- *decrypt*: the age identity comes only from a private 0600 file owned by this user —
  never a literal, an argument or the environment;
- *binding*: a payload bound to another installation or other providers is a staged
  incompatibility, unless ``adopt`` rebinds it explicitly (Task B28 commits and records
  ``admin.backup_adopt`` with both bindings);
- *diff*: counts per section against the live directory, and the ``base_digest`` the commit
  must still find.

A staged import is a ``cbi_`` handle in daemon memory only: bound to the admin peer and the
base digest, expiring after ten minutes, and gone after a restart.
"""

from __future__ import annotations

import hashlib
import os
import secrets
import stat
import time
from collections.abc import Callable, Hashable, Mapping
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from comms.core import refs, timeutil
from comms.core.audit.integrity import require_not_degraded
from comms.core.audit.writer import AuditWriter
from comms.core.backup import age
from comms.core.backup.payload import SCHEMA, _campaigns, _directory, account_binding, build_payload
from comms.core.backup.signature import SignatureError, sign, verify
from comms.core.canonical import jcs_dumps
from comms.core.groups import group_ref_in_tx, is_group_identity
from comms.core.keys.rotate import activate_in_tx, clean_orphans
from comms.core.keys.signers import require_import_trust
from comms.core.keys.slots import KeySlotError, KeySlotStore, active_version, load_active
from comms.core.strict_json import strict_json_loads

__all__ = [
    "ExportResult",
    "ImportCrash",
    "ImportRefused",
    "StagedImport",
    "StagedImports",
    "export",
    "stage_import",
]

STAGE_TTL_S = 600.0
_SECTIONS = ("locations", "recipients", "destinations", "contact_points", "audiences")


class ImportCrash(BaseException):
    """Raised only by the ``crash_at`` seam, which production never supplies."""


class ImportRefused(Exception):
    """A staged import step was refused. Fixed messages."""


@dataclass(frozen=True)
class ExportResult:
    ciphertext: bytes
    sidecar: bytes
    binding: str
    signer_key_id: str


@dataclass(frozen=True)
class StagedImport:
    handle: str
    peer: Hashable
    base_digest: str
    diff: Mapping[str, Mapping[str, int]]
    incompatibilities: tuple[str, ...]
    adopt: tuple[str, str] | None  # (the payload's binding, this installation's binding)
    payload: Mapping[str, Any]
    expires: float


class StagedImports:
    """The memory-only ``cbi_`` registry: a restart forgets every staged import."""

    def __init__(self, *, clock: Callable[[], float] = time.monotonic) -> None:
        self.clock = clock
        self._staged: dict[str, StagedImport] = {}

    def put(self, staged: StagedImport) -> None:
        self._staged[staged.handle] = staged

    def take(self, handle: str, peer: Hashable, base_digest: str) -> StagedImport:
        staged = self._staged.get(handle)
        if staged is None:
            raise ImportRefused("unknown staged import")
        if self.clock() >= staged.expires:
            del self._staged[handle]
            raise ImportRefused("staged import expired")
        if staged.peer != peer:
            raise ImportRefused("staged import belongs to another peer")
        if staged.base_digest != base_digest:
            raise ImportRefused("the base digest no longer matches")
        return staged


def export(
    writer: AuditWriter,
    store: KeySlotStore,
    recipient: str,
    providers: Mapping[str, str],
    *,
    now: datetime,
) -> ExportResult:
    payload = build_payload(writer.conn, providers, now=now)
    binding = str(strict_json_loads(payload.decode())["binding"])
    ciphertext = age.encrypt(payload, recipient)
    seed, key_id = load_active(writer.conn, store, "backup-key")
    sidecar = sign(ciphertext, seed)
    with writer.transaction() as tx:
        tx.append(
            "admin.backup_export",
            payload={
                "binding": binding,
                "ciphertext_sha256": hashlib.sha256(ciphertext).hexdigest(),
                "signer_key_id": key_id,
            },
        )
    return ExportResult(ciphertext, sidecar, binding, key_id)


def _identity(source: Any) -> str:
    """The age identity, from a private 0600 file owned by this user only."""
    if not isinstance(source, Path):
        raise ImportRefused("the identity must come from a private file")
    try:
        info = os.lstat(source)
    except OSError:
        raise ImportRefused("the identity file is missing") from None
    if (
        not stat.S_ISREG(info.st_mode)
        or stat.S_IMODE(info.st_mode) != 0o600
        or info.st_uid != os.getuid()
    ):
        raise ImportRefused("the identity file must be a private 0600 file")
    return source.read_text(encoding="ascii").strip()


def _current_installation(conn: Any) -> str | None:
    row = conn.execute("SELECT installation_ref FROM installation WHERE id = 1").fetchone()
    return None if row is None else str(row[0])


def _base_digest(conn: Any) -> str:
    """A read-only digest of the live directory and campaign metadata."""
    return hashlib.sha256(
        jcs_dumps({"directory": _directory(conn), "campaigns": _campaigns(conn)})
    ).hexdigest()


def _diff(conn: Any, payload: Mapping[str, Any]) -> dict[str, dict[str, int]]:
    live = {**_directory(conn), "campaigns": _campaigns(conn)}
    incoming = {**payload["directory"], "campaigns": payload["campaigns"]}
    out = {}
    for section in (*_SECTIONS, "campaigns"):
        have = {row["ref"]: row for row in live[section]}
        want = {row["ref"]: row for row in incoming[section]}
        out[section] = {
            "added": len(want.keys() - have.keys()),
            "changed": sum(1 for ref in want.keys() & have.keys() if want[ref] != have[ref]),
            "removed": len(have.keys() - want.keys()),
        }
    return out


def stage_import(
    conn: Any,
    staged: StagedImports,
    peer: Hashable,
    ciphertext: bytes,
    sidecar: bytes,
    identity_source: Any,
    *,
    providers: Mapping[str, str],
    trust_key: str | None = None,
    adopt: bool = False,
    now: datetime,
) -> StagedImport:
    # trust, then verify: who signed it, and may we take a backup from them?
    try:
        signer = verify(ciphertext, sidecar)
    except SignatureError:
        raise ImportRefused("the backup signature does not verify") from None
    known = conn.execute("SELECT 1 FROM verification_keys WHERE key_id = ?", (signer,)).fetchone()
    try:
        require_import_trust(conn, signer)
    except KeySlotError:
        # A signer this installation marked VERIFICATION_ONLY or REVOKED stays refused;
        # trust_key only vouches, for this one import, for a signer unknown here.
        if known is not None or trust_key != signer:
            raise ImportRefused("the signer is not trusted for import") from None
    identity = _identity(identity_source)
    try:
        plaintext = age.decrypt(ciphertext, identity)
    except age.AgeError:
        raise ImportRefused("the backup does not decrypt with this identity") from None
    try:
        payload = strict_json_loads(plaintext.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        raise ImportRefused("the backup payload does not decode") from None
    if (
        not isinstance(payload, dict)
        or payload.get("schema") != SCHEMA
        or jcs_dumps(payload) != plaintext
    ):
        raise ImportRefused("the backup payload is not comms-backup/v1")
    if account_binding(payload["installation_ref"], payload["providers"]) != payload["binding"]:
        raise ImportRefused("the backup binding does not recompute")
    incompatibilities: list[str] = []
    here = _current_installation(conn)
    local = account_binding(here, providers) if here is not None else None
    adoption: tuple[str, str] | None = None
    if payload["binding"] != local:
        if adopt and local is not None:
            adoption = (str(payload["binding"]), local)
        else:
            incompatibilities.append("BINDING_MISMATCH")
    result = StagedImport(
        handle=refs.mint("staged_import"),
        peer=peer,
        base_digest=_base_digest(conn),
        diff=_diff(conn, payload),
        incompatibilities=tuple(incompatibilities),
        adopt=adoption,
        payload=payload,
        expires=staged.clock() + STAGE_TTL_S,
    )
    staged.put(result)
    return result


def commit_import(
    writer: AuditWriter,
    store: KeySlotStore,
    staged: StagedImports,
    handle: str,
    peer: Hashable,
    *,
    now: datetime,
    crash_at: str | None = None,
) -> Mapping[str, Mapping[str, int]]:
    """Commit a staged import (Task B28): recheck, replace, open a new chain epoch.

    One audited transaction: the live directory must still match the staged base digest;
    the directory is replaced (refs reused, anything the backup lacks disabled, never
    deleted); the chain key rotates and ``admin.backup_import`` becomes the new epoch's
    first event; an explicit adoption records ``admin.backup_adopt`` with both bindings.
    """
    conn = writer.conn
    require_not_degraded(conn)
    old = active_version(conn, "audit-chain-key")
    if old is None:
        raise ImportRefused("the comms chain has no key")
    # Refuse before staging anything: a stale or incompatible import leaves no key behind.
    stage = staged.take(handle, peer, _base_digest(conn))
    if stage.incompatibilities:
        raise ImportRefused("the staged import has unresolved incompatibilities")
    clean_orphans(conn, store, "audit-chain-key")
    material = secrets.token_bytes(32)
    version = store.write_version("audit-chain-key", material)
    checkpoint_key = load_active(conn, store, "audit-checkpoint-key")[0]
    stamp = timeutil.iso(now)
    if crash_at == "before_tx":  # the new chain key is a staged orphan; the next commit cleans it
        raise ImportCrash(crash_at)
    try:
        with writer.transaction() as tx:
            staged.take(handle, peer, _base_digest(conn))  # rechecked under the writer's lock
            _apply_directory(conn, stage.payload["directory"], stamp)
            key_id = activate_in_tx(conn, "audit-chain-key", old, version, material, stamp)
            tx.open_epoch(
                "admin.backup_import",
                new_key=material,
                checkpoint_key=checkpoint_key,
                payload={"binding": str(stage.payload["binding"]), "chain_key_id": key_id},
            )
            if stage.adopt is not None:
                tx.append(
                    "admin.backup_adopt",
                    payload={"old_binding": stage.adopt[0], "new_binding": stage.adopt[1]},
                )
    except Exception:
        store.destroy("audit-chain-key", version)  # never registered: nothing references it
        raise
    if crash_at == "after_tx":
        raise ImportCrash(crash_at)
    return stage.diff


def _identity_id(conn: Any, transport: str, identity: str) -> int:
    row = conn.execute(
        "SELECT id FROM delivery_identities WHERE transport = ? AND identity = ?",
        (transport, identity),
    ).fetchone()
    if row is not None:
        return int(row[0])
    return int(
        conn.execute(
            "INSERT INTO delivery_identities (transport, identity) VALUES (?, ?)",
            (transport, identity),
        ).lastrowid
    )


def _ids(conn: Any, table: str) -> dict[str, int]:
    return {str(r[0]): int(r[1]) for r in conn.execute(f"SELECT ref, id FROM {table}")}


def _apply_directory(conn: Any, directory: Mapping[str, Any], stamp: str) -> None:
    """Replace the live directory with the backup's, in the caller's transaction."""
    # Everything the backup lacks, or holds disabled, is disabled first, so enabling the
    # backup's rows never meets a uniqueness clash with a row that is going away.
    for table, section in (
        ("locations", "locations"),
        ("recipients", "recipients"),
        ("destinations", "destinations"),
        ("contact_points", "contact_points"),
    ):
        keep = {row["ref"] for row in directory[section] if row["enabled"]}
        for ref in _ids(conn, table).keys() - keep:
            conn.execute(f"UPDATE {table} SET enabled = 0 WHERE ref = ?", (ref,))
    locations = _ids(conn, "locations")
    for row in directory["locations"]:
        if row["ref"] in locations:
            conn.execute(
                "UPDATE locations SET name = ?, enabled = ? WHERE ref = ?",
                (row["name"], row["enabled"], row["ref"]),
            )
        else:
            conn.execute(
                "INSERT INTO locations (ref, name, enabled, created_at) VALUES (?, ?, ?, ?)",
                (row["ref"], row["name"], row["enabled"], row["created_at"]),
            )
    recipients = _ids(conn, "recipients")
    for row in directory["recipients"]:
        # a payload from before D39-PRE carries no display_name: the local label is kept
        name = row.get("display_name")
        if row["ref"] in recipients:
            conn.execute(
                "UPDATE recipients SET enabled = ?, display_name = coalesce(?, display_name)"
                " WHERE ref = ?",
                (row["enabled"], name, row["ref"]),
            )
        else:
            conn.execute(
                "INSERT INTO recipients (ref, display_name, enabled, created_at)"
                " VALUES (?, ?, ?, ?)",
                (row["ref"], name, row["enabled"], row["created_at"]),
            )
    locations, recipients = _ids(conn, "locations"), _ids(conn, "recipients")
    destinations = _ids(conn, "destinations")
    for row in directory["destinations"]:
        if row["ref"] in destinations:
            conn.execute(
                "UPDATE destinations SET display_name = ?, capabilities = ?, enabled = ?, disabled_at = ? WHERE ref = ?",
                (
                    row["display_name"],
                    row["capabilities"],
                    row["enabled"],
                    row["disabled_at"],
                    row["ref"],
                ),
            )
        else:
            conn.execute(
                "INSERT INTO destinations (ref, location_id, transport, platform_identity, identity_id,"
                " display_name, capabilities, enabled, disabled_at, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    row["ref"],
                    locations[row["location_ref"]],
                    row["transport"],
                    row["platform_identity"],
                    _identity_id(conn, row["transport"], row["identity"]),
                    row["display_name"],
                    row["capabilities"],
                    row["enabled"],
                    row["disabled_at"],
                    row["created_at"],
                ),
            )
    for row in directory["destinations"]:  # every restored group is listed at once (E11b)
        if is_group_identity(row["platform_identity"]):
            group_ref_in_tx(conn, row["ref"], now=timeutil.instant(stamp))
    contact_points = _ids(conn, "contact_points")
    for row in directory["contact_points"]:
        if row["ref"] in contact_points:
            conn.execute(
                "UPDATE contact_points SET enabled = ?, opted_out_at = ?, disabled_at = ? WHERE ref = ?",
                (row["enabled"], row["opted_out_at"], row["disabled_at"], row["ref"]),
            )
        else:
            conn.execute(
                "INSERT INTO contact_points (ref, recipient_id, transport, platform_identity, identity_id,"
                " enabled, opted_out_at, disabled_at, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    row["ref"],
                    recipients[row["recipient_ref"]],
                    row["transport"],
                    row["platform_identity"],
                    _identity_id(conn, row["transport"], row["identity"]),
                    row["enabled"],
                    row["opted_out_at"],
                    row["disabled_at"],
                    row["created_at"],
                ),
            )
    for row in directory["location_members"]:
        conn.execute(
            "INSERT OR IGNORE INTO location_members (location_id, recipient_id) VALUES (?, ?)",
            (locations[row["location_ref"]], recipients[row["recipient_ref"]]),
        )
    audiences = _ids(conn, "audiences")
    for row in directory["audiences"]:
        if row["ref"] in audiences:
            conn.execute("UPDATE audiences SET name = ? WHERE ref = ?", (row["name"], row["ref"]))
        else:
            conn.execute(
                "INSERT INTO audiences (ref, name, created_at) VALUES (?, ?, ?)",
                (row["ref"], row["name"], row["created_at"]),
            )
    audiences, destinations = _ids(conn, "audiences"), _ids(conn, "destinations")
    for row in directory["audience_members"]:
        conn.execute(
            "INSERT OR IGNORE INTO audience_members (audience_id, member_location_id, member_audience_id,"
            " member_destination_id, member_recipient_id) VALUES (?, ?, ?, ?, ?)",
            (
                audiences[row["audience_ref"]],
                locations.get(row["location_ref"]) if row["location_ref"] else None,
                audiences.get(row["member_audience_ref"]) if row["member_audience_ref"] else None,
                destinations.get(row["destination_ref"]) if row["destination_ref"] else None,
                recipients.get(row["recipient_ref"]) if row["recipient_ref"] else None,
            ),
        )
