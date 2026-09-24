"""First-run bootstrap: create the encrypted databases and their keys.

`connection.provision_db` and the migration runner both existed and were tested,
but nothing called them — every entry point used `open_existing`, which requires
databases that no code path could produce. This module is that missing path.

The one genuinely dangerous operation here is minting a database key. SQLCipher
keys are not recoverable, so provisioning a fresh key beside an existing database
file leaves that ciphertext permanently unreadable. `KeyStore.require` refuses to
regenerate for exactly this reason (INV-ATREST); init applies the same care one
level up, by refusing whenever a database exists without its key.
"""

import os

from ..db import connection, migrations
from ..mcp import audit, auth
from . import fsperms

# Keys the MCP daemon needs, alongside the two database keys.
_SERVICE_KEYS = (auth.TOKEN_KEY_NAME, audit.AUDIT_KEY_NAME)


def _has_key(ks, name: str) -> bool:
    try:
        ks.require(name, 32)
    except Exception:  # noqa: BLE001 - absent, wrong length, or unreadable: all "unusable"
        return False
    return True


def _stranded(ks, paths) -> list[str]:
    """Databases present on disk whose key is missing. Never auto-repairable."""
    out = []
    for kind, path in (("vault", paths.vault_db), ("control", paths.control_db)):
        if os.path.isfile(path) and not _has_key(ks, connection.DB_KEY_NAMES[kind]):
            out.append(kind)
    return out


def _ensure_database(kind: str, path: str, ks):
    """Bring one database into existence and up to the shipped schema.

    Returns (state, key_name_provisioned). state is "created", "migrated", or
    "current".

    An existing database used to be skipped entirely, so a migration shipped
    after a vault was created never reached it: the vault kept working until a
    query touched the new table and failed with `no such table` on a live
    system. Forward-only migration is what the runner is built for — each step
    is transactional and bumps user_version atomically — so applying it here is
    the safe, idempotent operation, and skipping it protected nothing.
    """
    if os.path.isfile(path):
        conn = connection.open_existing(kind, path, ks)
        before = migrations.user_version(conn)
        after = migrations.migrate(conn, kind)
        conn.close()
        return ("migrated" if after != before else "current"), None

    # Reuse an existing key rather than rotating: the reverse of the stranded
    # case is safe, and rotating would discard a key the user may still need.
    key_name = connection.DB_KEY_NAMES[kind]
    minted = None
    if _has_key(ks, key_name):
        conn = connection.open_existing(kind, path, ks)
    else:
        conn = connection.provision_db(kind, path, ks)
        minted = key_name
    migrations.migrate(conn, kind)
    conn.close()
    return "created", minted


def init_vault(paths, ks, *, reveal: bool = False) -> dict:
    """Create the runtime layout, both databases, and every key. Idempotent.

    Returns a report rather than raising, so the CLI can print an actionable
    result. A database whose key is missing is reported, never overwritten.
    """
    stranded = _stranded(ks, paths)
    if stranded:
        names = ", ".join(stranded)
        return {
            "ok": False,
            "error": (
                f"{names} database exists but its key is missing from the keystore. "
                "Minting a new key would leave the existing data permanently "
                "unreadable, so nothing was changed. Restore the Keychain entry, or "
                f"move the {names} database aside and re-run to start fresh."
            ),
            "stranded": stranded,
        }

    fsperms.harden_umask()
    for directory in paths.all_dirs():
        fsperms.ensure_dir(directory)

    created, already, migrated = [], [], []
    # Every key minted here, including the two provision_db mints internally. The
    # report listed only the service keys, so an operator saw two where there were
    # four — and the omitted pair is the one whose loss is unrecoverable.
    provisioned = []
    buckets = {"created": created, "migrated": migrated, "current": already}
    for kind, path in (("vault", paths.vault_db), ("control", paths.control_db)):
        state, key_name = _ensure_database(kind, path, ks)
        if key_name:
            provisioned.append(key_name)
        buckets[state].append(kind)
        if state == "migrated":  # a migrated database was also already present
            already.append(kind)

    for name in _SERVICE_KEYS:
        if not _has_key(ks, name):
            ks.provision(name, 32)
            provisioned.append(name)

    for secret in paths.secret_files():
        if os.path.isfile(secret):
            fsperms.ensure_secret_file(secret)

    report = {
        "ok": True,
        "home": paths.home,
        "created": created,
        "already_present": already,
        "migrated": migrated,
        "keys_provisioned": provisioned,
        "endpoint": "http://127.0.0.1:8765/mcp",
    }
    if reveal:
        report["token"] = ks.require(auth.TOKEN_KEY_NAME, 32).hex()
    else:
        report["note"] = (
            "MCP token withheld; re-run with --reveal to print it "
            "(avoid doing so where stdout is captured to a log)"
        )
    return report
