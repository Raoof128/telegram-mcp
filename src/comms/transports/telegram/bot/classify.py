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

from dataclasses import dataclass

from comms.core.delivery.transport import ResultKind
from comms.transports.telegram.bot.http import BotResponse, BotTransportError

__all__ = ["PERMANENT_DESCRIPTIONS", "Classified", "classify_send"]

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


@dataclass(frozen=True)
class Classified:
    kind: ResultKind
    provider_message_ref: str | None = None
    retry_after: int | None = None


_UNKNOWN = Classified(ResultKind.OUTCOME_UNKNOWN)


def classify_send(outcome: BotResponse | BotTransportError) -> Classified:
    if isinstance(outcome, BotTransportError):
        return Classified(ResultKind.FAILED_TRANSIENT) if outcome.stage == "not_sent" else _UNKNOWN
    envelope = outcome.envelope
    if outcome.http_status >= 500 or envelope is None or not isinstance(envelope.get("ok"), bool):
        return _UNKNOWN
    if envelope["ok"]:
        result = envelope.get("result")
        message_id = result.get("message_id") if isinstance(result, dict) else None
        ref = str(message_id) if type(message_id) is int else None
        return Classified(ResultKind.ACCEPTED, provider_message_ref=ref)
    code = envelope.get("error_code")
    if code == 429:
        parameters = envelope.get("parameters")
        retry = parameters.get("retry_after") if isinstance(parameters, dict) else None
        if type(retry) is int and retry > 0:
            return Classified(ResultKind.FAILED_TRANSIENT, retry_after=retry)
        return _UNKNOWN
    if code in (400, 403) and envelope.get("description") in PERMANENT_DESCRIPTIONS:
        return Classified(ResultKind.FAILED_PERMANENT)
    return _UNKNOWN
