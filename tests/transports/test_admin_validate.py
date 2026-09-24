"""Every admin adapter validates an operation purely, before the executor records it (D12 prereq).

``validate`` raises ``ValueError`` for a malformed operation and returns ``None`` for a
well-formed one, and in both cases touches neither the network nor the session.
"""

from datetime import UTC, datetime

import pytest

from comms.core.providers.capability import Capability as C
from comms.core.providers.protocols import ProviderTarget, SemanticOperation
from comms.transports.telegram.bot.admin import BotAdmin
from comms.transports.telegram.user.admin import UserAdmin
from comms.transports.whatsapp.cloud.groups import WhatsAppAdmin


class Tripwire:
    def __getattr__(self, name):
        raise AssertionError(f"validate touched the provider: {name}")


def _never(coroutine):
    raise AssertionError("validate ran a provider call")


BOT = ProviderTarget("telegram", "telegram_bot", "dst_g", "-1001234567890")
USER = ProviderTarget("telegram", "telegram_user", "dst_g", "-1001234567890")
WA = ProviderTarget("whatsapp", "whatsapp_cloud", "dst_w", "group:120363049891234567")
ADAPTERS = {
    "telegram_bot": (BotAdmin(Tripwire()), BOT),
    "telegram_user": (
        UserAdmin(Tripwire(), run=_never, clock=lambda: datetime(2026, 9, 25, tzinfo=UTC)),
        USER,
    ),
    "whatsapp_cloud": (WhatsAppAdmin(Tripwire(), Tripwire()), WA),
}
GOOD = {
    "telegram_bot": SemanticOperation(C.MEMBER_BAN, {"user_id": 42}),
    "telegram_user": SemanticOperation(C.MEMBER_BAN, {"user_id": 42}),
    "whatsapp_cloud": SemanticOperation(C.GROUP_MEMBER_REMOVE, {"wa_id": "61400000001"}),
}
BAD = {
    "telegram_bot": [
        SemanticOperation(C.MEMBER_BAN, {"user_id": -1}),
        SemanticOperation(C.MEMBER_BAN, {"user_id": 42, "extra": 1}),
        SemanticOperation(C.ADMIN_PROMOTE, {"user_id": 42, "profile": "everything"}),
    ],
    "telegram_user": [
        SemanticOperation(C.MEMBER_BAN, {"user_id": "42"}),
        SemanticOperation(C.ADMIN_PROMOTE, {"user_id": 42}),
    ],
    "whatsapp_cloud": [
        SemanticOperation(C.GROUP_MEMBER_REMOVE, {"wa_id": "+61 400"}),
        SemanticOperation(C.GROUP_SETTINGS_UPDATE, {}),
    ],
}
UNPERFORMED = {
    "telegram_bot": SemanticOperation(C.GROUP_DELETE, {}),
    "telegram_user": SemanticOperation(C.CHAT_SET_PHOTO, {}),
    "whatsapp_cloud": SemanticOperation(C.MEMBER_BAN, {"user_id": 42}),
}


@pytest.mark.parametrize("actor", sorted(ADAPTERS))
def test_a_well_formed_operation_validates_without_a_call(actor):
    adapter, target = ADAPTERS[actor]
    assert adapter.validate(GOOD[actor], target) is None


@pytest.mark.parametrize(("actor", "op"), [(a, op) for a, ops in BAD.items() for op in ops])
def test_a_malformed_operation_is_refused_without_a_call(actor, op):
    adapter, target = ADAPTERS[actor]
    with pytest.raises(ValueError):
        adapter.validate(op, target)


@pytest.mark.parametrize("actor", sorted(ADAPTERS))
def test_another_actors_destination_is_refused(actor):
    adapter, _target = ADAPTERS[actor]
    other = BOT if actor != "telegram_bot" else USER
    with pytest.raises(ValueError):
        adapter.validate(GOOD[actor], other)


def test_a_lifted_restriction_grants_every_permission_again():
    from comms.transports.telegram.args import CHAT_PERMISSIONS
    from comms.transports.telegram.bot.admin_members import MEMBER_REQUESTS
    from comms.transports.telegram.user.admin_members import MEMBER_SPECS

    args = {"user_id": 42, "permissions": "all"}
    _method, params = MEMBER_REQUESTS[C.MEMBER_RESTRICT](-100, args)
    spec = MEMBER_SPECS[C.MEMBER_RESTRICT](args)
    for built in (params["permissions"], spec["permissions"]):
        assert built == dict.fromkeys(sorted(CHAT_PERMISSIONS), True)
    for adapter, target in (ADAPTERS["telegram_bot"], ADAPTERS["telegram_user"]):
        adapter.validate(SemanticOperation(C.MEMBER_RESTRICT, args), target)
        with pytest.raises(ValueError):
            adapter.validate(
                SemanticOperation(C.MEMBER_RESTRICT, {"user_id": 42, "permissions": "some"}),
                target,
            )


@pytest.mark.parametrize("actor", sorted(ADAPTERS))
def test_an_operation_the_actor_does_not_perform_is_not_implemented(actor):
    adapter, target = ADAPTERS[actor]
    with pytest.raises(NotImplementedError):
        adapter.validate(UNPERFORMED[actor], target)
