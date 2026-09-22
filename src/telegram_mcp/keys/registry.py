"""Normative Phase-2 key registry (design §3, values verbatim).

Twelve spec purposes plus one implementation row (``agent-transport-key``,
origin ``impl``). Phase-2 provisioning creates only the file-backed rows it
requires; Phase-3 rows (disclosure/audit/backup) are registry-only
placeholders — their private material must not be generated early.
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = ["KEY_REGISTRY", "KeySpec"]

_VALID_ORIGINS = ("spec", "impl")
_VALID_PHASES = (2, 3)


@dataclass(frozen=True)
class KeySpec:
    """One registry row: algorithm, owner, persistence, phase, provenance."""

    algorithm: str
    owner: str
    persistent: bool
    required_phase: int
    origin: str  # "spec" | "impl"

    def __post_init__(self) -> None:
        if self.origin not in _VALID_ORIGINS:
            raise ValueError(f"invalid key origin: {self.origin!r}")
        if self.required_phase not in _VALID_PHASES:
            raise ValueError(f"invalid required phase: {self.required_phase!r}")


KEY_REGISTRY: dict[str, KeySpec] = {
    # Phase-2 file-backed runtime-account secrets.
    "principal-key": KeySpec("HMAC-SHA-256", "runtime account", True, 2, "spec"),
    "cursor-key": KeySpec("HMAC-SHA-256", "runtime account", True, 2, "spec"),
    "privacy-key": KeySpec("HMAC-SHA-256", "runtime account", True, 2, "spec"),
    "challenge-key": KeySpec("Ed25519", "runtime account", True, 2, "spec"),
    # Per-client lease-seed template row (files are per-client, on demand).
    "lease-seed": KeySpec("HMAC-SHA-256", "runtime account", True, 2, "spec"),
    # Tunnel references: provisioned by install, never by the file store.
    "tunnel-tls-key": KeySpec("X.509/SPKI", "respective service accounts", True, 2, "spec"),
    "tunnel-mtls-key": KeySpec("X.509/SPKI", "respective service accounts", True, 2, "spec"),
    # Agent approval key: P-256 Secure Enclave, device-bound, never here.
    "agent-approval-key": KeySpec("P-256 Secure Enclave", "operator", True, 2, "spec"),
    # Implementation row beyond the twelve spec purposes: Ed25519 software
    # key in the operator keychain; the daemon stores only the pinned public.
    "agent-transport-key": KeySpec("Ed25519 software", "operator keychain", True, 2, "impl"),
    # Phase-3 registry-only rows: no private material before Phase 3.
    "disclosure-key": KeySpec("Ed25519", "runtime account", False, 3, "spec"),
    "audit-checkpoint-key": KeySpec("Ed25519", "runtime account", False, 3, "spec"),
    "audit-chain-key": KeySpec("HMAC-SHA-256", "runtime account", False, 3, "spec"),
    "backup-key": KeySpec("HMAC-SHA-256", "runtime account", False, 3, "spec"),
}
