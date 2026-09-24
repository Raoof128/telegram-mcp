"""Meta Cloud API send outcomes by an explicit per-code table (comms v0.3 Task C22; A19).

``META_CODES`` maps each documented Graph/Cloud API error code to a result kind and a Comms
code. Only a documented rejection before acceptance is ``FAILED_TRANSIENT`` (rate and
throughput limits; an expired or invalid token, which refuses before any send); a documented
refusal that a resend cannot fix is ``FAILED_PERMANENT``. An undocumented code, a 5xx, a
malformed body, a success without a message id, and any transport failure after connecting
are ``OUTCOME_UNKNOWN``. A connection never made is ``FAILED_TRANSIENT``.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType

from comms.core.delivery.transport import ResultKind
from comms.transports.whatsapp.cloud.http import GraphResponse, GraphTransportError

__all__ = ["META_CODES", "MetaOutcome", "classify_send"]

_T, _P, _U = ResultKind.FAILED_TRANSIENT, ResultKind.FAILED_PERMANENT, ResultKind.OUTCOME_UNKNOWN
META_CODES: Mapping[int, tuple[ResultKind, str | None]] = MappingProxyType(
    {
        # throttling: refused before acceptance, safe to resend later
        4: (_T, "RATE_LIMITED"),
        80007: (_T, "RATE_LIMITED"),
        130429: (_T, "RATE_LIMITED"),
        131048: (_T, "RATE_LIMITED"),
        131056: (_T, "RATE_LIMITED"),
        # the credential: refused before any send; resend after rotation
        0: (_T, "CREDENTIAL"),
        190: (_T, "CREDENTIAL"),
        # permission and account standing
        3: (_P, "NOT_AUTHORIZED"),
        10: (_P, "NOT_AUTHORIZED"),
        200: (_P, "NOT_AUTHORIZED"),
        131005: (_P, "NOT_AUTHORIZED"),
        131031: (_P, "ACCOUNT_LOCKED"),
        131042: (_P, "ACCOUNT_INELIGIBLE"),
        368: (_P, "ACCOUNT_BLOCKED"),
        133010: (_P, "NOT_REGISTERED"),
        # the request itself
        100: (_P, "INVALID_REQUEST"),
        131008: (_P, "INVALID_REQUEST"),
        131009: (_P, "INVALID_REQUEST"),
        131051: (_P, "INVALID_REQUEST"),
        131053: (_P, "MEDIA_REJECTED"),
        # the recipient
        131021: (_P, "UNDELIVERABLE"),
        131026: (_P, "UNDELIVERABLE"),
        131030: (_P, "UNDELIVERABLE"),
        131047: (_P, "WINDOW_CLOSED"),
        # templates (A23: never swapped for another)
        132000: (_P, "TEMPLATE_MISMATCH"),
        132005: (_P, "TEMPLATE_MISMATCH"),
        132007: (_P, "TEMPLATE_MISMATCH"),
        132012: (_P, "TEMPLATE_MISMATCH"),
        132001: (_P, "TEMPLATE_UNAVAILABLE"),
        132015: (_P, "TEMPLATE_UNAVAILABLE"),
        132016: (_P, "TEMPLATE_UNAVAILABLE"),
        # documented as generic: the outcome is not known
        131000: (_U, None),
        131016: (_U, None),
    }
)


@dataclass(frozen=True)
class MetaOutcome:
    kind: ResultKind
    provider_message_ref: str | None = None
    code: str | None = None


_UNKNOWN = MetaOutcome(ResultKind.OUTCOME_UNKNOWN)


def classify_send(outcome: GraphResponse | GraphTransportError) -> MetaOutcome:
    if isinstance(outcome, GraphTransportError):
        return MetaOutcome(ResultKind.FAILED_TRANSIENT) if outcome.stage == "not_sent" else _UNKNOWN
    envelope = outcome.envelope
    if outcome.http_status >= 500 or envelope is None:
        return _UNKNOWN
    if outcome.http_status == 200 and "error" not in envelope:
        messages = envelope.get("messages")
        first = messages[0] if isinstance(messages, list) and len(messages) == 1 else None
        wamid = first.get("id") if isinstance(first, dict) else None
        if isinstance(wamid, str) and wamid.startswith("wamid."):
            return MetaOutcome(ResultKind.ACCEPTED, provider_message_ref=wamid)
        return _UNKNOWN
    error = envelope.get("error")
    code = error.get("code") if isinstance(error, dict) else None
    if type(code) is not int or code not in META_CODES:
        return _UNKNOWN
    kind, name = META_CODES[code]
    return MetaOutcome(kind, code=name) if kind is not ResultKind.OUTCOME_UNKNOWN else _UNKNOWN
