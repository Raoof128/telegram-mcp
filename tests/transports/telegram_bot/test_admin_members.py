"""comms v0.3 Task C9: Bot API membership operations — single calls only (A27, A41, G10)."""

import json

import httpx
import pytest

from comms.core.providers.capability import Capability as C
from comms.core.providers.protocols import ProviderTarget, SemanticOperation
from comms.core.providers.semantics import SEMANTICS
from comms.transports.telegram.bot.admin import BotAdmin
from comms.transports.telegram.bot.http import BotApi
from tests.conformance.registry import REGISTRY
from tests.conformance.runner import run_suite
from tests.transports.telegram_bot.helpers import Secrets, routed

GROUP = ProviderTarget("telegram", "telegram_bot", "dst_" + "g" * 26, "-1001234567890")
CASES = [
    (C.MEMBER_BAN, {"user_id": 42}, "banChatMember", {"chat_id": -1001234567890, "user_id": 42}),
    (
        C.MEMBER_BAN,
        {"user_id": 42, "until_date": 1790000000, "revoke_messages": True},
        "banChatMember",
        {
            "chat_id": -1001234567890,
            "user_id": 42,
            "until_date": 1790000000,
            "revoke_messages": True,
        },
    ),
    (
        C.MEMBER_UNBAN,
        {"user_id": 42},
        "unbanChatMember",
        {"chat_id": -1001234567890, "user_id": 42},
    ),
    (
        C.MEMBER_UNBAN,
        {"user_id": 42, "only_if_banned": True},
        "unbanChatMember",
        {"chat_id": -1001234567890, "user_id": 42, "only_if_banned": True},
    ),
    (
        C.MEMBER_RESTRICT,
        {"user_id": 42, "permissions": {"can_send_messages": False}},
        "restrictChatMember",
        {"chat_id": -1001234567890, "user_id": 42, "permissions": {"can_send_messages": False}},
    ),
]


def _admin(answer, seen):
    methods = ("banChatMember", "unbanChatMember", "restrictChatMember")
    return BotAdmin(
        BotApi(Secrets(), version=1, transport=routed(dict.fromkeys(methods, answer), seen))
    )


@pytest.mark.parametrize("cap,args,method,params", CASES)
def test_each_operation_one_call_and_classified(cap, args, method, params):
    seen = []
    result = _admin("admin_true", seen).invoke(SemanticOperation(cap, args), GROUP, "op_key")
    assert (result.outcome, result.code) == ("SUCCEEDED", None)
    assert [r.url.path.rsplit("/", 1)[1] for r in seen] == [method]
    assert json.loads(seen[0].content) == params
    assert SEMANTICS[(cap, "telegram_bot")].retry_class == "SET_STATE"


@pytest.mark.parametrize(
    "answer,outcome,code",
    [
        ("admin_400_not_enough_rights", "FAILED", "NOT_AUTHORIZED"),
        ("admin_400_user_is_admin", "FAILED", "NOT_AUTHORIZED"),
        ("getChat_403_kicked", "FAILED", "NOT_AUTHORIZED"),
        ("admin_400_user_not_found", "FAILED", "TARGET_NOT_FOUND"),
        ("sendMessage_429", "FAILED", "RATE_LIMITED"),
        ("sendMessage_400_undocumented", "OUTCOME_UNKNOWN", None),
        ("sendMessage_500_envelope", "OUTCOME_UNKNOWN", None),
        (httpx.ReadTimeout, "OUTCOME_UNKNOWN", None),
        (httpx.ConnectError, "FAILED", "PROVIDER_UNAVAILABLE"),
    ],
)
def test_not_authorized_error_maps_to_not_authorized(answer, outcome, code):
    seen = []
    op = SemanticOperation(C.MEMBER_BAN, {"user_id": 42})
    result = _admin(answer, seen).invoke(op, GROUP, "op_key")
    assert (result.outcome, result.code, len(seen)) == (outcome, code, 1)


def test_rate_limit_carries_retry_after():
    result = _admin("sendMessage_429", []).invoke(
        SemanticOperation(C.MEMBER_BAN, {"user_id": 42}), GROUP, "k"
    )
    assert result.detail == {"retry_after": 17}


def test_bot_adapter_exposes_no_compound_member_remove_call():
    seen = []
    admin = _admin("admin_true", seen)
    assert C.MEMBER_REMOVE not in admin.operations
    assert SEMANTICS[(C.MEMBER_REMOVE, "telegram_bot")].steps == (C.MEMBER_BAN, C.MEMBER_UNBAN)
    with pytest.raises(NotImplementedError):
        admin.invoke(SemanticOperation(C.MEMBER_REMOVE, {"user_id": 42}), GROUP, "k")
    assert seen == []


@pytest.mark.parametrize(
    "cap,args",
    [
        (C.MEMBER_BAN, {}),
        (C.MEMBER_BAN, {"user_id": 0}),
        (C.MEMBER_BAN, {"user_id": True}),
        (C.MEMBER_BAN, {"user_id": "42"}),
        (C.MEMBER_BAN, {"user_id": 42, "chat_id": 1}),
        (C.MEMBER_UNBAN, {"user_id": 42, "only_if_banned": "yes"}),
        (C.MEMBER_RESTRICT, {"user_id": 42}),
        (C.MEMBER_RESTRICT, {"user_id": 42, "permissions": {"can_fly": True}}),
        (C.MEMBER_RESTRICT, {"user_id": 42, "permissions": {"can_send_messages": 1}}),
        (C.HISTORY_READ, {}),
    ],
)
def test_malformed_arguments_are_refused_before_any_call(cap, args):
    seen = []
    with pytest.raises((ValueError, NotImplementedError)):  # unperformed: NotImplementedError
        _admin("admin_true", seen).invoke(SemanticOperation(cap, args), GROUP, "k")
    assert seen == []


def test_a_non_bot_target_is_refused():
    target = ProviderTarget("telegram", "telegram_user", "dst_x", "-1001")
    with pytest.raises(ValueError):
        _admin("admin_true", []).invoke(
            SemanticOperation(C.MEMBER_BAN, {"user_id": 42}), target, "k"
        )


def test_the_conformance_admin_contract_passes():
    report = run_suite(
        REGISTRY.subset("telegram_bot", "admin"), {"telegram_bot": frozenset({"admin"})}
    )
    assert report.ok and report.passed == 2, report.failures
