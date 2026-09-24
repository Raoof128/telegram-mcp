# src/telegram_mcp/disclosure/exposure.py
"""The exposure snapshot the operator approves (Phase-3 design §5.3-§5.5).

Phase 2 signed a synthetic-zero snapshot. Phase 4 signs the real one: every
bucket this call would charge, with its *projected* quantity (committed rows
in the window + live reservations + this call's worst case), and the tier.
The wire field ``exposure_snapshot_digest`` is unchanged; only its input is.

The digest is recomputed at step 6. Any movement in any bucket or in the tier
voids the approval and forces exactly one re-prompt.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from comms.transports.telegram.consent.challenge import exposure_snapshot_digest
from comms.transports.telegram.disclosure.budget import BucketKey, Usage

__all__ = ["exposure_digest", "exposure_snapshot"]

_TIERS = ("normal", "elevated", "refuse")


def exposure_snapshot(tier: str, projected: Mapping[BucketKey, Usage]) -> dict[str, Any]:
    """Canonical snapshot object: tier plus every bucket, sorted."""
    if tier not in _TIERS:
        raise ValueError("unknown exposure tier")
    buckets = sorted(
        (
            {
                "kind": key.kind,
                "subject_digest": key.subject_digest,
                "projected_records": usage.records,
                "projected_bytes": usage.bytes,
            }
            for key, usage in projected.items()
        ),
        key=lambda bucket: (bucket["kind"], bucket["subject_digest"]),
    )
    return {
        "buckets": buckets,
        "mode": "projected",
        "schema": "tg-mcp-exposure-snapshot/v1",
        "tier": tier,
    }


def exposure_digest(tier: str, projected: Mapping[BucketKey, Usage]) -> str:
    """``sha256(JCS(snapshot))``, the value signed into the challenge."""
    return exposure_snapshot_digest(exposure_snapshot(tier, projected))
