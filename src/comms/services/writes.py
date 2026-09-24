"""The one provider write path shared by the group and message services (comms v0.3 D12–D14).

The actor is chosen by capability and preference; object refs in the arguments become their
provider identities only here, and only for an object of the same group; a fresh snapshot must
say ``AVAILABLE``; the call runs through ``MutationExecutor.provider``; and a provider refusal
drops the cached snapshot (A25).
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from comms.core.campaigns.directory import destination_id
from comms.core.errors import CommsError
from comms.core.objects import resolve_object
from comms.core.providers.capability import Capability
from comms.core.providers.protocols import ProviderResult, ProviderTarget, SemanticOperation
from comms.core.providers.semantics import SEMANTICS
from comms.services.capability import CapabilityService
from comms.services.mutations import CallContext, MutationExecutor, MutationOutcome

__all__ = ["ProviderWrites", "summary", "transport_of"]

# An object argument: (object kind, the provider field its identity fills).
ObjectArg = tuple[str, str]


def transport_of(targets: Mapping[str, ProviderTarget]) -> str:
    transports = {t.transport for t in targets.values()}
    if len(transports) != 1:
        raise CommsError("INVALID_ARGUMENT")
    (transport,) = transports
    return transport


def summary(actor: str, outcome: MutationOutcome) -> dict[str, Any]:
    return {
        "result": outcome.state,
        "code": outcome.code,
        "actor": actor,
        "op_ref": outcome.op_ref,
        "replayed": outcome.replayed,
    }


class ProviderWrites:
    def __init__(
        self, conn: Any, capability: CapabilityService, executor: MutationExecutor
    ) -> None:
        self.conn, self._capability, self._executor = conn, capability, executor

    def write(
        self,
        ctx: CallContext,
        tool: str,
        targets: Mapping[str, ProviderTarget],
        capability: Capability,
        args: Mapping[str, Any],
        request_id: str,
        actor: str | None,
        *,
        objects: Mapping[ObjectArg, object] | None = None,
        object_kind: str | None = None,
    ) -> tuple[str, ProviderTarget, MutationOutcome]:
        able = {a: t for a, t in targets.items() if (capability, a) in SEMANTICS}
        if not able:  # no named actor can ever perform it, whatever a snapshot says
            raise CommsError("PROVIDER_UNSUPPORTED")
        if actor is not None and actor != "auto" and actor not in able:
            raise CommsError("PROVIDER_UNSUPPORTED")
        chosen = self._capability.choose_actor(able, capability, preferred=actor)
        target = targets[chosen]
        call = dict(args)
        for (kind, field), ref in (objects or {}).items():
            call[field] = self.identity(ref, kind, target)
        self._capability.require_for_write(chosen, target, capability)
        outcome = self._executor.provider(
            ctx,
            "comms_" + tool.replace(".", "_"),
            target,
            SemanticOperation(capability, call),
            request_id,
            object_kind=object_kind,
        )
        if outcome.state == "FAILED":
            self._capability.authoritative(chosen, target, ProviderResult("FAILED", outcome.code))
        return chosen, target, outcome

    def identity(self, ref: object, kind: str, target: ProviderTarget) -> Any:
        """The provider identity an object ref names, if the object is this group's."""
        if not isinstance(ref, str):
            raise CommsError("INVALID_ARGUMENT")
        found = resolve_object(self.conn, ref, kind)
        if found.transport != target.transport or found.destination_id != destination_id(
            self.conn, target.destination_ref
        ):
            raise CommsError("NOT_FOUND")  # another group's object is not this group's
        identity = found.provider_identity
        if kind == "message":
            chat, _sep, message_id = identity.rpartition(":")
            if chat != target.identity:
                raise CommsError("NOT_FOUND")
            telegram = target.transport == "telegram"  # a Telegram message id is an integer
            return int(message_id) if telegram and message_id.isdigit() else message_id
        if kind == "topic":
            if not identity.isdigit():
                raise CommsError("NOT_FOUND")
            return int(identity)
        return identity
