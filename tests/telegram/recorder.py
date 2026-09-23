"""Runtime outbound-RPC recorder (design §6.2), at the gateway's own _call.

The source guard shows what our code can build; this shows what actually
reached the sender, including anything Telethon might issue on its own
through ``_call``. It subclasses the adapter's ``_GatewayClient`` and is
injected through ``TelethonSession(client_factory=...)``; there is no flag
for it in the adapter. ``ALLOWED`` is the reviewer's own copy, deliberately
not imported from the adapter, so the two must agree independently.
"""

from __future__ import annotations

from collections.abc import Callable
from contextvars import ContextVar
from typing import Any

from telegram_mcp.telegram.telethon_adapter import _GatewayClient, qualified

PHASE: ContextVar[str] = ContextVar("telegram_mcp_rpc_phase", default="unscoped")

# Design §3.3: connection plumbing Telethon issues itself, allowed in every phase.
TRANSPORT = frozenset(
    {
        "functions.InvokeWithLayerRequest",
        "functions.InitConnectionRequest",
        "functions.InvokeWithoutUpdatesRequest",
        "functions.PingRequest",
        "help.GetConfigRequest",
        "auth.ExportAuthorizationRequest",
        "auth.ImportAuthorizationRequest",
    }
)
ALLOWED: dict[str, frozenset[str]] = {
    "admin.login": frozenset(
        {
            "auth.SendCodeRequest",
            "auth.SignInRequest",
            "account.GetPasswordRequest",
            "auth.CheckPasswordRequest",
            "updates.GetStateRequest",
            "users.GetUsersRequest",
        }
    ),
    "admin.discover": frozenset({"messages.GetDialogsRequest"}),
    "mcp.retrieval": frozenset(
        {
            "messages.GetPeerDialogsRequest",
            "messages.GetHistoryRequest",
            "messages.GetMessagesRequest",
            "channels.GetMessagesRequest",
            "messages.GetRepliesRequest",
            "messages.SearchRequest",
        }
    ),
}


def violations(log: list[tuple[str, str]]) -> list[tuple[str, str]]:
    return [
        (phase, name)
        for phase, name in log
        if name not in TRANSPORT and name not in ALLOWED.get(phase, frozenset())
    ]


def recording_factory(log: list[tuple[str, str]]) -> Callable[..., Any]:
    class RecordingClient(_GatewayClient):
        async def _call(self, sender, request, ordered=False, flood_sleep_threshold=None):  # type: ignore[no-untyped-def]
            log.append((PHASE.get(), qualified(request)))
            return await super()._call(sender, request, ordered, flood_sleep_threshold)

    def factory(path: str, api_id: int, api_hash: str, **kwargs: Any) -> Any:
        return RecordingClient(path, api_id, api_hash, **kwargs)

    return factory
