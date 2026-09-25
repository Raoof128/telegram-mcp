"""The comms state layout (D39-PRE Task E1).

Pure path arithmetic: nothing here creates, opens or reads a file. The layout under the
installer's state directory (``/var/db/telegram-mcp`` by default)::

    meta.db                 the legacy Telegram database (unchanged)
    keys/                   the legacy key store (the retained legacy chain and login)
    anchor/anchor.json      the legacy chain's anchor
    comms/comms.db          SQLCipher
    comms/db-key.pointer    0600, the active comms-db-key version
    comms/secrets/          0700, the secret store (comms-db-key, provider credentials)
    comms/slots/            0700, the key-slot store
    comms/anchor/head.anchor
    comms/comms.json        0600, the daemon settings (E3), no secrets
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

__all__ = ["CommsPaths", "default_state_dir"]

_STATE_DIR_ENV = "TELEGRAM_MCP_STATE_DIR"
_DEFAULT_STATE_DIR = Path("/var/db/telegram-mcp")  # scripts/install_paths.sh STATE_DIR


def default_state_dir() -> Path:
    """The installer's state directory, or ``$TELEGRAM_MCP_STATE_DIR``."""
    env = os.environ.get(_STATE_DIR_ENV)
    return Path(env) if env else _DEFAULT_STATE_DIR


@dataclass(frozen=True)
class CommsPaths:
    state_dir: Path

    @property
    def root(self) -> Path:
        return Path(self.state_dir) / "comms"

    @property
    def db(self) -> Path:
        return self.root / "comms.db"

    @property
    def db_key_pointer(self) -> Path:
        return self.root / "db-key.pointer"

    @property
    def secrets_dir(self) -> Path:
        return self.root / "secrets"

    @property
    def slots_dir(self) -> Path:
        return self.root / "slots"

    @property
    def anchor_dir(self) -> Path:
        return self.root / "anchor"

    @property
    def anchor(self) -> Path:
        return self.anchor_dir / "head.anchor"

    @property
    def settings(self) -> Path:
        return self.root / "comms.json"

    @property
    def legacy_keys(self) -> Path:
        return Path(self.state_dir) / "keys"

    @property
    def legacy_anchor(self) -> Path:
        return Path(self.state_dir) / "anchor" / "anchor.json"

    @property
    def legacy_db(self) -> Path:
        return Path(self.state_dir) / "meta.db"
