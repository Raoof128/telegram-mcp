"""WhatsApp groups: discovery and capability-gated operations (comms v0.3 Task C26; P §16).

Group management is a capability-gated extension: ``GroupDiscovery.discover()`` asks Graph for
the number's groups once and sets every group capability from the answer —
``AVAILABLE`` on success, ``ACCOUNT_INELIGIBLE`` when Meta refuses on permission grounds,
``PROVIDER_UNSUPPORTED`` when Meta does not know the path, ``UNKNOWN`` otherwise (never treated
as available). ``WhatsAppAdmin`` refuses a group operation that discovery did not make available,
with that state as its code and no call; nothing is ever simulated (no unofficial automation).
The group endpoint shapes follow Meta's Groups API documentation and are confirmed by the
live acceptance runbook.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from typing import Any

from comms.core.providers.capability import Capability as C
from comms.core.providers.capability import CapabilityState as S
from comms.core.providers.protocols import ProviderResult, ProviderTarget, SemanticOperation
from comms.transports.whatsapp.cloud.classify import admin_call
from comms.transports.whatsapp.cloud.http import GraphApi, GraphTransportError

__all__ = ["GROUP_CAPABILITIES", "GroupDiscovery", "WhatsAppAdmin", "group_id_of"]

ACTOR = "whatsapp_cloud"
GROUP_CAPABILITIES = (
    C.GROUP_LIST,
    C.GROUP_GET,
    C.GROUP_MEMBERS,
    C.GROUP_MEMBER_REMOVE,
    C.GROUP_INVITE_GET,
    C.GROUP_INVITE_RESET,
    C.GROUP_SETTINGS_UPDATE,
    C.GROUP_MESSAGE_SEND,
)
_PERMISSION_CODES = frozenset({3, 10, 200, 131005})
_UNKNOWN_PATH_CODES = frozenset({2500})
_WA_ID = re.compile(r"\A[0-9]{8,15}\Z")


def group_id_of(target: ProviderTarget) -> str:
    kind, sep, group_id = target.identity.partition(":")
    if target.actor != ACTOR or kind != "group" or not sep or not group_id.isdigit():
        raise ValueError("not a whatsapp group destination")
    return group_id


class GroupDiscovery:
    def __init__(self, api: GraphApi) -> None:
        self._api = api
        self.states: dict[C, S] = dict.fromkeys(GROUP_CAPABILITIES, S.UNKNOWN)

    def discover(self) -> Mapping[C, S]:
        try:
            response = self._api.list_groups(limit=1)
        except GraphTransportError:
            state = S.UNKNOWN
        else:
            state = _discovered(response.http_status, response.envelope)
        self.states = dict.fromkeys(GROUP_CAPABILITIES, state)
        return self.states


def _discovered(status: int, envelope: Mapping[str, Any] | None) -> S:
    if envelope is None or status >= 500:
        return S.UNKNOWN
    if status == 200 and isinstance(envelope.get("data"), list):
        return S.AVAILABLE
    error = envelope.get("error")
    code = error.get("code") if isinstance(error, dict) else None
    if code in _PERMISSION_CODES:
        return S.ACCOUNT_INELIGIBLE
    if code in _UNKNOWN_PATH_CODES or status == 404:
        return S.PROVIDER_UNSUPPORTED
    return S.UNKNOWN


def _remove(api: GraphApi, group_id: str, args: Mapping[str, Any]) -> ProviderResult:
    if set(args) != {"wa_id"} or not (
        isinstance(args["wa_id"], str) and _WA_ID.match(args["wa_id"])
    ):
        raise ValueError("operation arguments are malformed")
    return admin_call(lambda: api.remove_group_participant(group_id, args["wa_id"]))


def _reset(api: GraphApi, group_id: str, args: Mapping[str, Any]) -> ProviderResult:
    if args:
        raise ValueError("operation arguments are malformed")
    result = admin_call(lambda: api.reset_group_invite(group_id))
    if result.outcome != "SUCCEEDED":
        return result
    link = result.detail.get("invite_link")
    if not isinstance(link, str) or not link.startswith("https://chat.whatsapp.com/"):
        return ProviderResult("OUTCOME_UNKNOWN", None)
    return ProviderResult("SUCCEEDED", None, provider_ref=link)


def _settings(api: GraphApi, group_id: str, args: Mapping[str, Any]) -> ProviderResult:
    checks = {
        "subject": lambda v: isinstance(v, str) and 1 <= len(v) <= 100,
        "description": lambda v: isinstance(v, str) and len(v) <= 2048,
    }
    if not args or not set(args) <= set(checks) or not all(checks[k](v) for k, v in args.items()):
        raise ValueError("operation arguments are malformed")
    return admin_call(lambda: api.update_group(group_id, args))


_OPERATIONS: Mapping[C, Callable[[GraphApi, str, Mapping[str, Any]], ProviderResult]] = {
    C.GROUP_MEMBER_REMOVE: _remove,
    C.GROUP_INVITE_RESET: _reset,
    C.GROUP_SETTINGS_UPDATE: _settings,
}


class WhatsAppAdmin:
    operations = frozenset(_OPERATIONS)

    def __init__(self, api: GraphApi, discovery: GroupDiscovery) -> None:
        self._api, self._discovery = api, discovery

    def __repr__(self) -> str:
        return "WhatsAppAdmin(<redacted>)"

    def invoke(self, op: SemanticOperation, target: ProviderTarget, op_key: str) -> ProviderResult:
        operation = _OPERATIONS.get(op.capability)
        if operation is None:
            raise ValueError("whatsapp_cloud does not perform this operation")
        group_id = group_id_of(target)
        state = self._discovery.states.get(op.capability, S.UNKNOWN)
        if state is not S.AVAILABLE:
            return ProviderResult("FAILED", state.value)  # gated: nothing sent, nothing simulated
        return operation(self._api, group_id, op.args)
