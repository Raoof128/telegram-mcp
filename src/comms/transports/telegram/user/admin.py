"""MTProto ``AdminOperations`` (comms v0.3 Tasks C18–C19; A27, A41), plus ``group.create``
(no destination) and the bounded admin-log read.

``invoke`` validates the arguments with the pure request tables, then asks the adapter for one
administrative RPC (``admin_request``) on the session's loop through the injected ``run``. A
saga (``member.remove``) is never one call here; the executor runs its steps (D4). A private
chat is not a group. MTProto admin RPCs carry no idempotency key, so ``op_key`` is not sent.
"""

from __future__ import annotations

from collections.abc import Callable, Coroutine, Mapping
from datetime import datetime
from typing import Any, Protocol

from comms.core import timeutil
from comms.core.providers.capability import Capability
from comms.core.providers.protocols import (
    ContextPage,
    ProviderResult,
    ProviderTarget,
    SemanticOperation,
)
from comms.core.providers.semantics import SEMANTICS
from comms.transports.telegram.peers import unmark_chat_id
from comms.transports.telegram.user.admin_chat import CHAT_SPECS, group_create
from comms.transports.telegram.user.admin_members import MEMBER_SPECS

__all__ = ["UserAdmin"]

ACTOR = "telegram_user"
ADMIN_TIMEOUT_S = 15.0
MAX_LOG_PAGE = 100
_SPECS: Mapping[Capability, Callable[[Mapping[str, Any]], dict[str, Any]]] = {
    **MEMBER_SPECS,
    **CHAT_SPECS,
}
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

    async def admin_log(
        self, peer_id: int, *, max_id: int, limit: int, timeout: float
    ) -> list[tuple[int, datetime, int, str]]: ...


Runner = Callable[[Coroutine[Any, Any, Any]], Any]


class UserAdmin:
    operations = frozenset(_SPECS)

    def __init__(
        self, session: AdminSession, *, run: Runner, clock: Callable[[], datetime]
    ) -> None:
        self._session, self._run, self._clock = session, run, clock

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
        return self._run(  # type: ignore[no-any-return]
            self._session.admin_request(
                op.capability, peer_type, peer_id, spec, timeout=ADMIN_TIMEOUT_S
            )
        )

    def create_group(self, args: Mapping[str, Any]) -> ProviderResult:
        """``group.create`` (CREATE, resolve-only): no destination; the ref is the new chat's
        marked id."""
        spec = group_create(args)
        return self._run(  # type: ignore[no-any-return]
            self._session.admin_request(
                Capability.GROUP_CREATE, "none", 0, spec, timeout=ADMIN_TIMEOUT_S
            )
        )

    def read_admin_log(
        self, target: ProviderTarget, *, limit: int = 50, cursor: str | None = None
    ) -> ContextPage:
        """One bounded page of the admin log (``admin.log.read``), newest first, event kinds only
        (no content); a basic group has none. ``cursor`` is the last page's lowest event id."""
        if target.actor != ACTOR:
            raise ValueError("not a telegram_user destination")
        if type(limit) is not int or not 1 <= limit <= MAX_LOG_PAGE:
            raise ValueError("admin log page size is 1..100")
        if cursor is not None and not (cursor.isascii() and cursor.isdigit() and int(cursor) > 0):
            raise ValueError("admin log cursor is malformed")
        peer_type, peer_id = unmark_chat_id(target.identity)
        if peer_type != "channel":
            return ContextPage((), "telegram_live")
        max_id = int(cursor) if cursor is not None else 0
        events = self._run(
            self._session.admin_log(peer_id, max_id=max_id, limit=limit, timeout=ADMIN_TIMEOUT_S)
        )
        observed = timeutil.iso(self._clock())
        items = tuple(
            {
                "source": "telegram_live",
                "observed_at": observed,
                "event_id": event_id,
                "date": timeutil.iso(date),
                "user_id": user_id,
                "action": action,
            }
            for event_id, date, user_id, action in events
        )
        next_cursor = str(min(e[0] for e in events)) if len(events) == limit else None
        return ContextPage(items, "telegram_live", next_cursor)
