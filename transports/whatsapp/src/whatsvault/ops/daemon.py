"""Shared daemon startup (ledger #57).

Every launchd unit runs under KeepAlive={"SuccessfulExit": false}: crash restart,
but a clean exit stays stopped. That only helps if an unmet precondition produces
a CLEAN exit. Opening the databases raises KeyMissing on a machine whose Keychain
is not provisioned, which would exit non-zero and restart forever — so the open is
funnelled through here and turned into a reported, content-free record instead.
"""

from ..crypto import keystore
from . import structlog


def open_databases(service: str, *, check_same_thread: bool = True):
    """Return (vault_conn, control_conn, None) or (None, None, blocked_record).

    A service that dispatches work onto worker threads passes
    check_same_thread=False and takes responsibility for serialising access.
    """
    from ..crypto.keystore import KeyringKeyStore
    from ..db import connection as C
    from . import fsperms, paths

    fsperms.harden_umask()
    p = paths.from_env()
    try:
        ks = KeyringKeyStore()
        return (
            C.open_existing("vault", p.vault_db, ks, check_same_thread=check_same_thread),
            C.open_existing("control", p.control_db, ks, check_same_thread=check_same_thread),
            None,
        )
    except keystore.KeyMissing as exc:
        # Distinct from a broken backend: the operator action is to provision, not
        # to repair the Keychain. KeyMissing subclasses KeyStoreError, so it must
        # be caught first or it would be reported as an unavailable keystore.
        return (
            None,
            None,
            blocked(
                service,
                "keys_not_provisioned",
                f"{exc} absent; run `whatsvault mcp-provision` (or the vault/control key setup)",
            ),
        )
    except keystore.KeyStoreError as exc:
        return None, None, blocked(service, "keystore_unavailable", f"{type(exc).__name__}: {exc}")
    except FileNotFoundError as exc:
        return None, None, blocked(service, "databases_absent", type(exc).__name__)


def blocked(service: str, blocked_on: str, detail: str) -> dict:
    return structlog.event(
        {"service": service, "status": "not_started", "blocked_on": blocked_on, "detail": detail}
    )
