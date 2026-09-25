"""What an operator command may reach (D39-PRE E7)."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from comms.core.audit.verify_all import LegacyVerify
from comms.core.audit.writer import AuditWriter
from comms.core.keys.slots import KeySlotStore

__all__ = ["LegacySide", "OperatorContext", "OperatorHandler"]


@dataclass(frozen=True)
class LegacySide:
    """The retained legacy chain: ``verify --all``'s legacy half and the cutover's port."""

    conn: Any
    port: Callable[[], Any]  # a fresh LegacyPort per cutover run
    verify: LegacyVerify
    retention: Callable[[], Any] | None = None  # its LegacyRetention (retention run)
    retention_days: Callable[[], Mapping[str, int]] | None = None  # the legacy periods

    def __repr__(self) -> str:
        return "LegacySide(<redacted>)"


@dataclass(frozen=True)
class OperatorContext:
    writer: AuditWriter
    store: KeySlotStore
    clock: Callable[[], datetime]
    oauth: Any = None  # the remote authorization server, when the remote listener is configured
    legacy: LegacySide | None = None  # absent outside the daemon (the smoke, unit tests)
    secrets: Any = None  # the provider-credential store (credential commands)
    proofs: Mapping[str, Callable[[bytes], None]] | None = None  # A13 live proofs by purpose
    reload: Callable[[], dict[str, Any]] | None = None  # rebuild the adapters after a change
    transfers: Any = None  # the backup TransferRegistry (32 KiB chunks, peer-bound)
    staged: Any = None  # StagedImports: memory-only, peer-bound
    backup_recipient: str | None = None  # an age public key (comms.json)
    providers: Mapping[str, str] | None = None  # the account ids a backup is bound to
    retention_days: Mapping[str, int] | None = None  # campaign_body_days, identity_retention_days

    @property
    def conn(self) -> Any:
        return self.writer.conn

    def require_legacy(self) -> LegacySide:
        if self.legacy is None:
            raise ValueError("the legacy chain is not attached to this runtime")
        return self.legacy

    def __repr__(self) -> str:
        return "OperatorContext(<redacted>)"


OperatorHandler = Callable[[OperatorContext, dict[str, Any]], dict[str, Any]]
