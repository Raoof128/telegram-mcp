"""comms v0.3 Task C17: MTProto capability discovery across group kinds (P §9–12; A25)."""

import asyncio
from datetime import UTC, datetime

import pytest

from comms.core.providers.capability import Capability as C
from comms.core.providers.capability import CapabilityState as S
from comms.core.providers.protocols import ProviderTarget
from comms.transports.telegram.capabilities import TELEGRAM_CAPABILITIES
from comms.transports.telegram.telegram.errors import GatewayError
from comms.transports.telegram.telegram.rights import SelfRights
from comms.transports.telegram.user.capability import UserCapability
from tests.conformance.registry import REGISTRY
from tests.conformance.runner import run_suite

NOW = datetime(2026, 9, 25, tzinfo=UTC)
SUPER = ProviderTarget("telegram", "telegram_user", "dst_s", "-1000000000077")
BASIC = ProviderTarget("telegram", "telegram_user", "dst_b", "-55")
PRIVATE = ProviderTarget("telegram", "telegram_user", "dst_p", "42")
RIGHTS = frozenset({"delete_messages", "ban_users", "invite_users", "pin_messages"})
KIND_SPECIFIC = {C.ADMIN_LOG_READ, C.GROUP_MIGRATE}  # channel-only log; basic-group-only migrate


class Session:
    def __init__(self, answer, readiness=None):
        self.answer, self._readiness, self.asked = answer, readiness, []

    def readiness(self):
        return self._readiness

    async def self_rights(self, peer_type, peer_id, *, timeout):
        self.asked.append((peer_type, peer_id))
        if isinstance(self.answer, Exception):
            raise self.answer
        return self.answer


def _states(answer, target=SUPER, readiness=None):
    session = Session(answer, readiness)
    provider = UserCapability(session, run=asyncio.run, clock=lambda: NOW)
    return provider.snapshot("telegram_user", target).states, session


def test_basic_group_and_supergroup_give_same_capability_shape():
    supergroup, asked_super = _states(SelfRights("megagroup", "admin", RIGHTS))
    basic, asked_basic = _states(SelfRights("chat", "admin", RIGHTS), target=BASIC)
    assert set(supergroup) == set(basic) == set(TELEGRAM_CAPABILITIES)
    assert {c: s for c, s in supergroup.items() if c not in KIND_SPECIFIC} == {
        c: s for c, s in basic.items() if c not in KIND_SPECIFIC
    }
    assert (supergroup[C.ADMIN_LOG_READ], basic[C.ADMIN_LOG_READ]) == (S.AVAILABLE, S.UNAVAILABLE)
    assert asked_super.asked == [("channel", 77)] and asked_basic.asked == [("chat", 55)]


def test_user_has_history_search_bot_does_not():
    states, _ = _states(SelfRights("megagroup", "member"))
    assert states[C.HISTORY_SEARCH] is S.AVAILABLE and states[C.HISTORY_READ] is S.AVAILABLE
    assert states[C.MEMBER_LIST] is S.AVAILABLE


def test_admin_rights_map_to_capabilities():
    states, _ = _states(SelfRights("megagroup", "admin", RIGHTS))
    for cap in (
        C.MESSAGE_DELETE,
        C.MEMBER_BAN,
        C.MEMBER_REMOVE,
        C.INVITE_CREATE,
        C.JOIN_REQUEST_APPROVE,
        C.MESSAGE_PIN,
    ):
        assert states[cap] is S.AVAILABLE, cap
    for cap in (C.ADMIN_PROMOTE, C.CHAT_SET_TITLE, C.GROUP_DELETE):
        assert states[cap] is S.NOT_AUTHORIZED, cap
    assert states[C.TOPIC_CREATE] is S.UNAVAILABLE  # not a forum


def test_creator_and_forum():
    states, _ = _states(
        SelfRights(
            "megagroup",
            "creator",
            RIGHTS | {"add_admins", "change_info", "manage_topics"},
            is_forum=True,
        )
    )
    assert states[C.TOPIC_CREATE] is S.AVAILABLE and states[C.GROUP_DELETE] is S.AVAILABLE
    assert states[C.GROUP_MIGRATE] is S.UNAVAILABLE  # already a supergroup
    basic, _ = _states(SelfRights("chat", "creator", RIGHTS), target=BASIC)
    assert basic[C.GROUP_MIGRATE] is S.AVAILABLE


def test_a_muted_member_cannot_send_but_can_read():
    states, _ = _states(SelfRights("megagroup", "member", denied=frozenset({"send_messages"})))
    assert states[C.MESSAGE_SEND] is S.NOT_AUTHORIZED and states[C.HISTORY_READ] is S.AVAILABLE


def test_a_broadcast_subscriber_reads_but_does_not_post():
    states, _ = _states(SelfRights("broadcast", "member"))
    assert states[C.MESSAGE_SEND] is S.NOT_AUTHORIZED and states[C.HISTORY_READ] is S.AVAILABLE
    assert states[C.MEMBER_LIST] is S.NOT_AUTHORIZED


@pytest.mark.parametrize("status", ["left", "banned"])
def test_outside_the_chat_nothing_but_creating_a_group(status):
    states, _ = _states(SelfRights("megagroup", status))
    assert {s for c, s in states.items() if c is not C.GROUP_CREATE} == {S.NOT_AUTHORIZED}
    assert states[C.GROUP_CREATE] is S.AVAILABLE


def test_a_private_chat_offers_messages_and_history_without_a_lookup():
    states, session = _states(SelfRights("chat", "member"), target=PRIVATE)
    assert session.asked == []
    assert states[C.MESSAGE_SEND] is S.AVAILABLE and states[C.HISTORY_SEARCH] is S.AVAILABLE
    assert states[C.MEMBER_BAN] is S.UNAVAILABLE


@pytest.mark.parametrize(
    "readiness,state",
    [
        ("AUTH_REQUIRED", S.NOT_CONFIGURED),
        ("SESSION_REVOKED", S.ACCOUNT_INELIGIBLE),
        ("ACCOUNT_UNAVAILABLE", S.ACCOUNT_INELIGIBLE),
    ],
)
def test_an_unusable_session(readiness, state):
    states, session = _states(SelfRights("megagroup", "creator"), readiness=readiness)
    assert set(states.values()) == {state} and session.asked == []


@pytest.mark.parametrize(
    "code,state",
    [
        ("NOT_ACCESSIBLE", S.NOT_AUTHORIZED),
        ("FLOOD_WAIT", S.TEMPORARILY_UNAVAILABLE),
        ("TELEGRAM_UNAVAILABLE", S.TEMPORARILY_UNAVAILABLE),
        ("INTERNAL_ERROR", S.UNKNOWN),
    ],
)
def test_a_failed_lookup_is_never_available(code, state):
    states, _ = _states(GatewayError(code))
    assert {s for c, s in states.items() if c is not C.GROUP_CREATE} == {state}


def test_the_conformance_capability_contract_passes():
    report = run_suite(
        REGISTRY.subset("telegram_user", "capability"), {"telegram_user": frozenset({"capability"})}
    )
    assert report.ok and report.passed == 2, report.failures
