"""Bot API ``AdminOperations`` (comms v0.3 Tasks C9–C11; A27, A41).

``invoke`` performs exactly one provider call for a single-call operation the bot supports,
built by the per-area request tables (``admin_members``, …), and classifies it with
``classify_admin``. It refuses before any call: a target that is not a bot destination, an
operation outside the tables, and malformed arguments. A compound operation (a ``SEMANTICS``
saga such as ``member.remove``) is never a call here: the executor runs its steps (D4, G10).
The bot has no idempotency key, so ``op_key`` is not sent (A20). A created object's opaque
ref (``REF_FIELDS``) is the provider ref; a success without it is ``OUTCOME_UNKNOWN``.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from comms.core.providers.capability import Capability
from comms.core.providers.protocols import ProviderResult, ProviderTarget, SemanticOperation
from comms.core.providers.semantics import SEMANTICS
from comms.transports.telegram.bot.admin_chat import CHAT_REQUESTS
from comms.transports.telegram.bot.admin_invites import INVITE_REQUESTS, REF_FIELDS
from comms.transports.telegram.bot.admin_members import MEMBER_REQUESTS
from comms.transports.telegram.bot.classify import classify_admin
from comms.transports.telegram.bot.http import BotApi, BotResponse, BotTransportError

__all__ = ["BotAdmin"]

ACTOR = "telegram_bot"
Request = Callable[[int, Mapping[str, Any]], tuple[str, dict[str, Any]]]
_REQUESTS: Mapping[Capability, Request] = {**MEMBER_REQUESTS, **CHAT_REQUESTS, **INVITE_REQUESTS}
assert all(not SEMANTICS[(c, ACTOR)].steps for c in _REQUESTS)  # no saga is ever one call


class BotAdmin:
    operations = frozenset(_REQUESTS)

    def __init__(self, api: BotApi) -> None:
        self._api = api

    def __repr__(self) -> str:
        return "BotAdmin(<redacted>)"

    def validate(self, op: SemanticOperation, target: ProviderTarget) -> None:
        self._request(op, target)

    def _request(self, op: SemanticOperation, target: ProviderTarget) -> tuple[str, dict[str, Any]]:
        if target.actor != ACTOR:
            raise ValueError("not a telegram_bot destination")
        build = _REQUESTS.get(op.capability)
        if build is None:
            raise ValueError("the bot does not perform this operation as one call")
        return build(int(target.identity), op.args)

    def invoke(self, op: SemanticOperation, target: ProviderTarget, op_key: str) -> ProviderResult:
        method, params = self._request(op, target)
        try:
            outcome: BotResponse | BotTransportError = self._api.call(method, params)
        except BotTransportError as exc:
            outcome = exc
        result = classify_admin(outcome)
        field = REF_FIELDS.get(op.capability)
        if field is None or result.outcome != "SUCCEEDED":
            return result
        ref = result.detail.get(field)
        if type(ref) not in (int, str) or ref in ("", 0):
            return ProviderResult("OUTCOME_UNKNOWN", None)  # created, but no ref to name it by
        return ProviderResult("SUCCEEDED", None, provider_ref=str(ref), detail=result.detail)
