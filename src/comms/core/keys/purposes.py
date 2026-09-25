"""Every key purpose, its rotation and its destruction rules (comms v0.3 design §B.4; A9–A11, A38).

Each purpose names how it rotates, whether its public half is registered in
``verification_keys`` (every signer is), and exactly when its private material is
destroyed. No purpose keeps a private key without a rule. Retired purposes (``refused``)
are never minted again; their material leaves only by the rule named here.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Literal

__all__ = ["PURPOSES", "KeyPurpose"]

Kind = Literal["hmac", "ed25519", "p256", "raw256", "opaque"]
Rotation = Literal["seal_epoch", "new_id", "invalidate", "rekey", "staged", "refused"]
Destroy = Literal[
    "at_rotation",  # after the new version is active (and, for staged credentials, re-checked)
    "after_epoch_truncated",  # the chain epoch it MACs is truncated behind a verified root
    "while_commitments_retained",  # kept while a retained commitment names its key ID
    "at_once",  # rotation invalidates everything it produced
    "after_verified_reopen",  # the database reopens under the new key (A14)
    "while_dependency_proven",  # A10: kept only while a proven dependency exists (Task B10)
    "owner_runbook",  # retired signers: removed only by the owner-approved runbook
]


@dataclass(frozen=True)
class KeyPurpose:
    name: str
    kind: Kind
    rotation: Rotation
    public_registry: bool
    private_destroy: Destroy


def _p(
    name: str, kind: Kind, rotation: Rotation, public: bool, destroy: Destroy
) -> tuple[str, KeyPurpose]:
    return name, KeyPurpose(name, kind, rotation, public, destroy)


PURPOSES: Mapping[str, KeyPurpose] = MappingProxyType(
    dict(
        (
            _p("audit-chain-key", "hmac", "seal_epoch", False, "after_epoch_truncated"),
            _p("audit-checkpoint-key", "ed25519", "new_id", True, "at_rotation"),
            _p("campaign-commit-key", "hmac", "new_id", False, "while_commitments_retained"),
            _p("backup-key", "ed25519", "new_id", True, "at_rotation"),
            _p("cursor-key", "hmac", "invalidate", False, "at_once"),
            _p("comms-db-key", "raw256", "rekey", False, "after_verified_reopen"),
            _p("cml1-client-seed", "hmac", "new_id", False, "at_rotation"),
            _p("oauth-signing-key", "ed25519", "new_id", True, "at_rotation"),
            _p("oauth-refresh-key", "hmac", "new_id", False, "at_rotation"),
            _p("oauth-registration-key", "hmac", "new_id", False, "at_rotation"),
            _p("telegram-session", "opaque", "staged", False, "at_rotation"),
            _p("telegram-bot-token", "opaque", "staged", False, "at_rotation"),
            _p("meta-access-token", "opaque", "staged", False, "at_rotation"),
            _p("meta-app-secret", "opaque", "staged", False, "at_rotation"),
            _p("meta-webhook-secret", "opaque", "staged", False, "at_rotation"),
            _p("tls-key", "opaque", "new_id", False, "at_rotation"),
            _p("principal-key", "hmac", "refused", False, "while_dependency_proven"),
            _p("privacy-key", "hmac", "refused", False, "while_dependency_proven"),
            _p("disclosure-key", "ed25519", "refused", True, "owner_runbook"),
            _p("consent-approval-key", "p256", "refused", True, "owner_runbook"),
            _p("consent-transport-key", "p256", "refused", True, "owner_runbook"),
        )
    )
)
