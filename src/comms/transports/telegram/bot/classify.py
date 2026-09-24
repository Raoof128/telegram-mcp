"""Named-case outcome classification for Bot API sends (comms v0.3 Task C6; A19).

Only named provider cases leave ``OUTCOME_UNKNOWN``:

- ``ok=true`` → ``ACCEPTED`` (the ``message_id`` is the opaque provider ref);
- ``ok=false``, ``error_code=429`` with a positive integer ``parameters.retry_after`` →
  ``FAILED_TRANSIENT`` (a documented rejection before acceptance);
- ``ok=false``, 400/403 with a description in the closed ``PERMANENT_DESCRIPTIONS`` table →
  ``FAILED_PERMANENT``;
- a connection never established → ``FAILED_TRANSIENT``.

Everything else — other codes and descriptions, 5xx, malformed bodies, timeouts and resets after
connecting — is ``OUTCOME_UNKNOWN``.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any

from comms.core.delivery.transport import ResultKind
from comms.core.providers.protocols import ProviderResult
from comms.transports.telegram.bot.http import BotResponse, BotTransportError

__all__ = [
    "ADMIN_REFUSALS",
    "PERMANENT_DESCRIPTIONS",
    "Classified",
    "classify_admin",
    "classify_send",
]

# Exact descriptions the Bot API returns for sends that were refused and will stay refused.
PERMANENT_DESCRIPTIONS = frozenset(
    {
        "Bad Request: chat not found",
        "Bad Request: PEER_ID_INVALID",
        "Bad Request: message is too long",
        "Bad Request: message text is empty",
        "Bad Request: group chat was upgraded to a supergroup chat",
        "Bad Request: have no rights to send a message",
        "Bad Request: not enough rights to send text messages to the chat",
        "Forbidden: bot was blocked by the user",
        "Forbidden: user is deactivated",
        "Forbidden: bot can't initiate conversation with a user",
        "Forbidden: bot was kicked from the group chat",
        "Forbidden: bot was kicked from the supergroup chat",
        "Forbidden: bot is not a member of the channel chat",
        "Forbidden: bot is not a member of the supergroup chat",
    }
)


# Exact descriptions of refused admin calls, and the code each maps to.
ADMIN_REFUSALS: Mapping[str, str] = MappingProxyType(
    {
        **dict.fromkeys(
            (
                "Bad Request: not enough rights to restrict/unrestrict chat member",
                "Bad Request: not enough rights to change chat permissions",
                "Bad Request: user is an administrator of the chat",
                "Bad Request: can't remove chat owner",
                "Bad Request: CHAT_ADMIN_REQUIRED",
                "Bad Request: RIGHT_FORBIDDEN",
                "Forbidden: bot was kicked from the group chat",
                "Forbidden: bot was kicked from the supergroup chat",
                "Forbidden: bot is not a member of the channel chat",
                "Forbidden: bot is not a member of the supergroup chat",
            ),
            "NOT_AUTHORIZED",
        ),
        "Bad Request: user not found": "TARGET_NOT_FOUND",
        "Bad Request: PARTICIPANT_ID_INVALID": "TARGET_NOT_FOUND",
        "Bad Request: chat not found": "DESTINATION_NOT_FOUND",
    }
)


@dataclass(frozen=True)
class Classified:
    kind: ResultKind
    provider_message_ref: str | None = None
    retry_after: int | None = None


_UNKNOWN = Classified(ResultKind.OUTCOME_UNKNOWN)


def _parsed(outcome: BotResponse | BotTransportError) -> Mapping[str, Any] | ResultKind:
    """The envelope, or the verdict every Bot API call shares when there is none to read."""
    if isinstance(outcome, BotTransportError):
        return ResultKind.FAILED_TRANSIENT if outcome.stage == "not_sent" else _UNKNOWN.kind
    envelope = outcome.envelope
    if outcome.http_status >= 500 or envelope is None or not isinstance(envelope.get("ok"), bool):
        return _UNKNOWN.kind
    return envelope


def _retry_after(envelope: Mapping[str, Any]) -> int | None:
    parameters = envelope.get("parameters")
    retry = parameters.get("retry_after") if isinstance(parameters, dict) else None
    return retry if type(retry) is int and retry > 0 else None


def classify_send(outcome: BotResponse | BotTransportError) -> Classified:
    envelope = _parsed(outcome)
    if isinstance(envelope, ResultKind):
        return Classified(envelope)
    if envelope["ok"]:
        result = envelope.get("result")
        message_id = result.get("message_id") if isinstance(result, dict) else None
        ref = str(message_id) if type(message_id) is int else None
        return Classified(ResultKind.ACCEPTED, provider_message_ref=ref)
    code = envelope.get("error_code")
    if code == 429:
        retry = _retry_after(envelope)
        return Classified(ResultKind.FAILED_TRANSIENT, retry_after=retry) if retry else _UNKNOWN
    if code in (400, 403) and envelope.get("description") in PERMANENT_DESCRIPTIONS:
        return Classified(ResultKind.FAILED_PERMANENT)
    return _UNKNOWN


def classify_admin(outcome: BotResponse | BotTransportError) -> ProviderResult:
    """An admin call: ``SUCCEEDED`` on ``ok=true``; ``FAILED`` only for a named case (a code
    from ``ADMIN_REFUSALS``, ``RATE_LIMITED`` with its ``retry_after``, or
    ``PROVIDER_UNAVAILABLE`` when no connection was made); ``OUTCOME_UNKNOWN`` otherwise."""
    envelope = _parsed(outcome)
    if envelope is ResultKind.FAILED_TRANSIENT:
        return ProviderResult("FAILED", "PROVIDER_UNAVAILABLE")
    if isinstance(envelope, ResultKind):
        return ProviderResult("OUTCOME_UNKNOWN", None)
    if envelope["ok"]:
        result = envelope.get("result")
        return ProviderResult("SUCCEEDED", None, detail=result if isinstance(result, dict) else {})
    code = envelope.get("error_code")
    if code == 429 and (retry := _retry_after(envelope)):
        return ProviderResult("FAILED", "RATE_LIMITED", detail={"retry_after": retry})
    refusal = ADMIN_REFUSALS.get(str(envelope.get("description")))
    if code in (400, 403) and refusal is not None:
        return ProviderResult("FAILED", refusal)
    return ProviderResult("OUTCOME_UNKNOWN", None)
