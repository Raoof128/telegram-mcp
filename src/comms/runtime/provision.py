"""``comms keys provision``: create the encrypted comms state (D39-PRE Task E1; R-E4).

It runs before the daemon exists (install step 2), so it is local, under the same runtime lock
the daemon takes: it refuses while a daemon runs. It is idempotent and never overwrites:

- no ``comms.db``: ``comms-db-key`` v1 into the secret store, the pointer, the database and its
  schema;
- every required purpose without an active slot is minted. Before the comms chain exists (no
  cutover genesis yet) a first version is registered without an audit event, as the audit keys
  always were; the genesis then covers them. After the genesis a missing purpose is minted by
  the audited rotation;
- the legacy key store the retained legacy side of the daemon needs (its chain, login) is
  completed the same way (``provision_missing``: missing files only);
- a fresh install (no legacy ``meta.db``) gets one, migrated, with its empty chain anchored
  at sequence 0 under the legacy chain key (R-E9): the core anchor rule reads an empty chain
  with a sequence-0 anchor as clean, so the cutover can seal it. An existing ``meta.db`` is
  never touched;
- existing material that does not load, or does not match its recorded key id, is refused and
  left untouched: it is never replaced silently.

Every file is created with ``O_EXCL`` and fsynced (``VersionedFiles``); directories are 0700.
The report names purposes only, never material.
"""

from __future__ import annotations

import os
import stat
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from comms.core.audit.chain import COMMS, head
from comms.core.audit.writer import AuditWriter, SlotChainKeys
from comms.core.installation import installation_ref
from comms.core.keys import rotate as rot
from comms.core.keys.secrets import FileSecretStore, SecretStoreError
from comms.core.keys.slots import (
    KeySlotError,
    KeySlotStore,
    active_version,
    bootstrap_keys,
    load_active,
)
from comms.core.storage.db import CommsDbKeyError, open_comms_db
from comms.core.storage.migrations import migrate
from comms.core.storage.rekey import ITEM as DB_KEY
from comms.core.storage.rekey import KeyPointer, open_with_recovery, rekey
from comms.runtime.paths import CommsPaths
from comms.transports.telegram.keys.store import provision_missing
from comms.transports.telegram.runtime.lock import RuntimeActive, acquire_lock

__all__ = [
    "PROVISIONED_KEYS",
    "ProvisionRefused",
    "ProvisionReport",
    "provision",
    "rotate_db_key",
]

PROVISIONED_KEYS = (
    "audit-chain-key",
    "audit-checkpoint-key",
    "campaign-commit-key",
    "backup-key",
    "cursor-key",
    "oauth-signing-key",
)
_DAMAGED = "existing key material is damaged (run: comms doctor)"


class ProvisionRefused(Exception):
    """Provisioning refused; the message is fixed and names the fix."""


@dataclass(frozen=True)
class ProvisionReport:
    created: tuple[str, ...]
    legacy_created: tuple[str, ...] = ()


def _private_dir(path: Path) -> None:
    path.mkdir(mode=0o700, exist_ok=True)  # the parent must exist: never create a tree
    if stat.S_IMODE(path.stat().st_mode) != 0o700:
        raise ProvisionRefused("the comms directories must be 0700")


def _anchor_empty_legacy_chain(paths: CommsPaths, *, now: datetime) -> None:
    from comms.core.audit.chain import genesis_mac
    from comms.core.timeutil import iso
    from comms.transports.telegram.disclosure.audit.anchor import write_anchor
    from comms.transports.telegram.disclosure.audit.chain import LEGACY_TELEGRAM
    from comms.transports.telegram.keys.store import load_key
    from comms.transports.telegram.storage.db import open_db
    from comms.transports.telegram.storage.migrations import migrate as migrate_legacy

    legacy = open_db(paths.legacy_db)
    try:
        migrate_legacy(legacy)
    finally:
        legacy.close()
    _private_dir(paths.legacy_anchor.parent)
    write_anchor(
        paths.legacy_anchor,
        load_key("audit-chain-key"),  # the legacy store, bound by provision_missing
        chain_epoch=1,
        chain_seq=0,
        event_id="",
        event_mac=genesis_mac(LEGACY_TELEGRAM, 1),
        now=iso(now),
    )


def _open(paths: CommsPaths, secrets: FileSecretStore, created: list[str]) -> Any:
    pointer = KeyPointer(paths.db_key_pointer)
    if not paths.db.exists():
        if secrets.versions(DB_KEY) or paths.db_key_pointer.exists():
            raise ProvisionRefused(_DAMAGED)  # a key without its database: never guess
        secrets.put(DB_KEY, 1, os.urandom(32))
        pointer.set(1)
        created.append(DB_KEY)
        conn = open_comms_db(paths.db, secrets.get(DB_KEY, 1))
    else:
        try:
            conn = open_with_recovery(paths.db, secrets, pointer)
        except (CommsDbKeyError, SecretStoreError):
            raise ProvisionRefused(_DAMAGED) from None
    migrate(conn)
    return conn


def provision(
    paths: CommsPaths,
    *,
    now: datetime,
    runtime_dir: Path,
    clock: Callable[[], datetime] | None = None,
) -> ProvisionReport:
    try:
        lock = acquire_lock(
            Path(runtime_dir) / "runtime.lock", runtime_id=os.urandom(16), mode="provision"
        )
    except RuntimeActive:
        raise ProvisionRefused("a daemon is running for this runtime directory") from None
    conn = None
    try:
        for directory in (
            Path(paths.state_dir),
            paths.root,
            paths.secrets_dir,
            paths.slots_dir,
            paths.anchor_dir,
        ):
            _private_dir(directory)
        secrets, store = FileSecretStore(paths.secrets_dir), KeySlotStore(paths.slots_dir)
        created: list[str] = []
        conn = _open(paths, secrets, created)
        missing = []
        for purpose in PROVISIONED_KEYS:
            if active_version(conn, purpose) is None:
                missing.append(purpose)
                continue
            try:
                load_active(conn, store, purpose)
            except KeySlotError:
                raise ProvisionRefused(_DAMAGED) from None
        if head(conn, COMMS) is None:
            bootstrap_keys(conn, store, missing, now=now)  # before the genesis: it covers them
        else:
            writer = AuditWriter(
                conn, SlotChainKeys(conn, store), paths.anchor, clock=clock or (lambda: now)
            )
            for purpose in missing:
                rot.rotate(
                    writer, store, purpose, material=os.urandom(32), prove=lambda m: None, now=now
                )
        created.extend(missing)
        installation_ref(conn, now=now)
        _private_dir(paths.legacy_keys)
        legacy = provision_missing(paths.legacy_keys, phases=(2, 3))  # never overwrites
        if not paths.legacy_db.exists():
            _anchor_empty_legacy_chain(paths, now=now)
        return ProvisionReport(tuple(created), tuple(legacy))
    finally:
        if conn is not None:
            conn.close()
        lock.release()


def rotate_db_key(paths: CommsPaths, *, runtime_dir: Path) -> int:
    """``comms keys rotate comms-db-key`` (A14): the rekey protocol, local, daemon stopped.

    The rekey closes and reopens the database connection every service of a running daemon
    holds, so it takes the runtime lock and refuses while a daemon runs.
    """
    try:
        lock = acquire_lock(
            Path(runtime_dir) / "runtime.lock", runtime_id=os.urandom(16), mode="rekey"
        )
    except RuntimeActive:
        raise ProvisionRefused("stop the daemon before rotating the database key") from None
    try:
        secrets, pointer = FileSecretStore(paths.secrets_dir), KeyPointer(paths.db_key_pointer)
        try:
            conn = open_with_recovery(paths.db, secrets, pointer)
        except (CommsDbKeyError, SecretStoreError):
            raise ProvisionRefused("the comms database key does not open comms.db") from None
        return rekey(conn, paths.db, secrets, pointer)  # closes conn on success
    finally:
        lock.release()
