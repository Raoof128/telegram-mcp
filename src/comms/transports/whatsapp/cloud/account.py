"""WhatsApp account and phone status, and the capability snapshot (comms v0.3 Task C26; P §14).

The snapshot covers every ``whatsapp_cloud`` capability. Without credentials everything is
``NOT_CONFIGURED``. The phone's status gates sending: ``CONNECTED`` makes the send
capabilities available; any other status makes them ``TEMPORARILY_UNAVAILABLE``, and a failed
lookup ``UNKNOWN``. Group capabilities come from group discovery (P §16). Snapshots are
advisory; Meta's answer to a send is final (A25).
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import datetime
from typing import Any

from comms.core import timeutil
from comms.core.providers.capability import Capability as C
from comms.core.providers.capability import CapabilityState as S
from comms.core.providers.protocols import CapabilitySnapshot, ProviderTarget
from comms.core.providers.semantics import SUPPORT
from comms.transports.whatsapp.cloud.groups import GROUP_CAPABILITIES, GroupDiscovery
from comms.transports.whatsapp.cloud.http import GraphApi, GraphTransportError

__all__ = ["WHATSAPP_CAPABILITIES", "WhatsAppCapability"]

ACTOR = "whatsapp_cloud"
WHATSAPP_CAPABILITIES = tuple(c for c in C if ACTOR in SUPPORT[c])
_SENDS = frozenset(
    {
        C.MESSAGE_SEND,
        C.MESSAGE_REPLY,
        C.MESSAGE_SEND_TEXT,
        C.MESSAGE_SEND_IMAGE,
        C.MESSAGE_SEND_VIDEO,
        C.MESSAGE_SEND_AUDIO,
        C.MESSAGE_SEND_DOCUMENT,
        C.MESSAGE_SEND_LOCATION,
        C.MESSAGE_SEND_CONTACTS,
        C.MESSAGE_SEND_INTERACTIVE,
        C.MESSAGE_SEND_TEMPLATE,
    }
)


class WhatsAppCapability:
    def __init__(
        self,
        api: GraphApi | None,
        discovery: GroupDiscovery | None,
        *,
        clock: Callable[[], datetime],
    ) -> None:
        self._api, self._discovery, self._clock = api, discovery, clock

    def __repr__(self) -> str:
        return "WhatsAppCapability(<redacted>)"

    def inspect_phone(self) -> Mapping[str, Any] | None:
        """``phone_number.inspect``: status and quality; the business name is untrusted."""
        if self._api is None:
            return None
        try:
            response = self._api.phone_info()
        except GraphTransportError:
            return None
        body = response.envelope or {}
        if response.http_status != 200:
            return None
        name = body.get("verified_name")
        return {
            "status": body.get("status"),
            "quality_rating": body.get("quality_rating"),
            "untrusted": {"verified_name": name} if isinstance(name, str) else {},
        }

    def snapshot(self, actor: str, destination: ProviderTarget) -> CapabilitySnapshot:
        if actor != ACTOR or destination.actor != ACTOR:
            raise ValueError("not a whatsapp_cloud destination")
        stamp = timeutil.iso(self._clock())
        if self._api is None or self._discovery is None:
            states = dict.fromkeys(WHATSAPP_CAPABILITIES, S.NOT_CONFIGURED)
            return CapabilitySnapshot(ACTOR, destination.destination_ref, states, stamp)
        phone = self.inspect_phone()
        if phone is None:
            sending = S.UNKNOWN
        else:
            sending = S.AVAILABLE if phone["status"] == "CONNECTED" else S.TEMPORARILY_UNAVAILABLE
        states = {}
        for cap in WHATSAPP_CAPABILITIES:
            if cap in GROUP_CAPABILITIES:
                states[cap] = self._discovery.states.get(cap, S.UNKNOWN)
            elif cap in _SENDS:
                states[cap] = sending
            else:
                states[cap] = S.AVAILABLE
        return CapabilitySnapshot(ACTOR, destination.destination_ref, states, stamp)
