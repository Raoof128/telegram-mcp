"""Every wire domain the campaign core hashes under (comms 5b-4, G30).

The only home of core ``comms-*`` constants; ``tests/security/test_comms_wire_frozen.py``
pins the multiset and refuses a core domain defined anywhere else.
"""

from __future__ import annotations

__all__ = ["IDEMPOTENCY", "RECIPIENTS", "SNAPSHOT", "TARGET"]

IDEMPOTENCY = b"comms-delivery-idem/v1\0"  # §7.1
SNAPSHOT = b"comms-campaign-snapshot/v1\0"  # §5.4
TARGET = b"comms-campaign-target/v1\0"  # §9, S5
RECIPIENTS = b"comms-campaign-recipients/v1\0"  # §9, S5
