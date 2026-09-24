"""MTProto ``AdminOperations`` (comms v0.3 Tasks C18–C19; A27, A41).

``invoke`` validates the arguments with the pure request tables, then asks the adapter for one
administrative RPC (``admin_request``) on the session's loop through the injected ``run``. A
saga (``member.remove``) is never one call here; the executor runs its steps (D4). A private
chat is not a group. MTProto admin RPCs carry no idempotency key, so ``op_key`` is not sent.
"""

from __future__ import annotations

from collections.abc import Callable, Coroutine, Mapping
from typing import Any, Protocol

from comms.core.providers.capability import Capability
from comms.core.providers.protocols import ProviderResult, ProviderTarget, SemanticOperation
from comms.core.providers.semantics import SEMANTICS
from comms.transports.telegram.peers import unmark_chat_id
from comms.transports.telegram.user.admin_members import MEMBER_SPECS

__all__ = ["UserAdmin"]

ACTOR = "telegram_user"
ADMIN_TIMEOUT_S = 15.0
_SPECS: Mapping[Capability, Callable[[Mapping[str, Any]], dict[str, Any]]] = {**MEMBER_SPECS}
assert all(not SEMANTICS[(c, ACTOR)].steps for c in _SPECS)  # no saga is ever one call


class AdminSession(Protocol):
    async def admin_request(
        self,
        capability: Capability,
        peer_type: str,
        peer_id: int,
        spec: Mapping[str, Any],
        *,
        timeout: float,
    ) -> ProviderResult: ...


Runner = Callable[[Coroutine[Any, Any, ProviderResult]], ProviderResult]


class UserAdmin:
    operations = frozenset(_SPECS)

    def __init__(self, session: AdminSession, *, run: Runner) -> None:
        self._session, self._run = session, run

    def __repr__(self) -> str:
        return "UserAdmin(<redacted>)"

    def invoke(self, op: SemanticOperation, target: ProviderTarget, op_key: str) -> ProviderResult:
        if target.actor != ACTOR:
            raise ValueError("not a telegram_user destination")
        build = _SPECS.get(op.capability)
        if build is None:
            raise ValueError("the user actor does not perform this operation as one call")
        spec = build(op.args)
        peer_type, peer_id = unmark_chat_id(target.identity)
        if peer_type == "user":
            raise ValueError("a private chat is not a group")
        return self._run(
            self._session.admin_request(
                op.capability, peer_type, peer_id, spec, timeout=ADMIN_TIMEOUT_S
            )
        )
