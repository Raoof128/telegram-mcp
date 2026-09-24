"""comms v0.3 Task D4: MutationExecutor — one replay contract for local and provider writes
(G14–G16, A27, A28, A41, A42, A8)."""

import hashlib

import pytest

from comms.core import domains
from comms.core.audit.integrity import latch_degraded
from comms.core.canonical import jcs_dumps
from comms.core.errors import CommsError
from comms.core.providers.capability import Capability as C
from comms.core.providers.protocols import ProviderResult, ProviderTarget, SemanticOperation
from comms.services.mutations import (
    CallContext,
    MutationCrash,
    MutationExecutor,
    op_key,
    request_digest,
)
from tests.core import fakes
from tests.core.audit.legacy_fixtures import comms_world
from tests.core.campaign_helpers import NOW

REQ, REQ2 = "req_" + "a" * 26, "req_" + "b" * 26
CTX = CallContext(client_ref="cli_" + "c" * 26)
BOT = ProviderTarget("telegram", "telegram_bot", "dst_" + "g" * 26, "-1000000000077")
USER = ProviderTarget("telegram", "telegram_user", "dst_" + "g" * 26, "-1000000000077")


class FakeAdmin:
    """An AdminOperations double: scripted outcomes per capability; records every call and what
    the database held at the moment of the call."""

    def __init__(self, conn, script=None):
        self.conn, self.script, self.calls = (
            conn,
            {k: list(v) for k, v in (script or {}).items()},
            [],
        )

    def invoke(self, op, target, op_key_):
        keys = [
            r[0]
            for r in self.conn.execute(
                "SELECT provider_request_key FROM mutation_steps WHERE state = 'IN_FLIGHT'"
            )
        ]
        self.calls.append((op.capability, dict(op.args), op_key_, keys))
        outcome = (
            (self.script.get(op.capability) or ["ok"]).pop(0)
            if self.script.get(op.capability)
            else "ok"
        )
        if outcome == "raise":
            raise RuntimeError("provider exploded")
        if outcome == "unknown":
            return ProviderResult("OUTCOME_UNKNOWN", None)
        if outcome == "refused":
            return ProviderResult("FAILED", "NOT_AUTHORIZED")
        return ProviderResult("SUCCEEDED", None)


@pytest.fixture
def world(tmp_path):
    return comms_world(tmp_path)


def _executor(world, admin, **kw):
    return MutationExecutor(world["writer"], {"telegram_bot": admin, "telegram_user": admin}, **kw)


def _mutation(conn):
    return conn.execute("SELECT state, provider_code, retried, result FROM mutations").fetchone()


def _steps(conn):
    return [
        tuple(r)
        for r in conn.execute(
            "SELECT step_no, capability, state FROM mutation_steps ORDER BY step_no"
        )
    ]


def test_digests_and_op_key_are_domain_separated_jcs():
    args, targets = {"user_id": 42}, {"group": "grp_" + "a" * 26}
    expected = hashlib.sha256(
        domains.REQUEST_DIGEST + jcs_dumps({"args": args, "targets": targets, "tool": "comms_x"})
    ).hexdigest()
    assert request_digest("comms_x", args, targets) == expected
    assert (
        op_key("cli_x", REQ)
        == hashlib.sha256(
            domains.ADMIN_OP + jcs_dumps({"client": "cli_x", "request": REQ})
        ).hexdigest()
    )
    assert (
        domains.REQUEST_DIGEST == b"comms-request-digest/v1\0"
        and domains.ADMIN_OP == b"comms-admin-op/v1\0"
    )
    assert request_digest("comms_x", args, targets) != request_digest("comms_y", args, targets)


def test_request_id_replay_returns_the_existing_operation(world):
    admin = FakeAdmin(world["conn"])
    executor = _executor(world, admin)
    op = SemanticOperation(C.MEMBER_BAN, {"user_id": 42})
    first = executor.provider(CTX, "comms_group_member_ban", BOT, op, REQ)
    again = executor.provider(CTX, "comms_group_member_ban", BOT, op, REQ)
    assert (first.op_ref, first.state, first.replayed) == (again.op_ref, "SUCCEEDED", False)
    assert again.replayed and len(admin.calls) == 1


def test_request_id_reuse_with_new_arguments_is_refused(world):
    executor = _executor(world, FakeAdmin(world["conn"]))
    executor.provider(
        CTX, "comms_group_member_ban", BOT, SemanticOperation(C.MEMBER_BAN, {"user_id": 42}), REQ
    )
    with pytest.raises(CommsError) as refused:
        executor.provider(
            CTX,
            "comms_group_member_ban",
            BOT,
            SemanticOperation(C.MEMBER_BAN, {"user_id": 43}),
            REQ,
        )
    assert refused.value.code == "REQUEST_ID_REUSE"


def test_unknown_set_state_is_retried_once_with_same_key(world):
    admin = FakeAdmin(world["conn"], {C.MEMBER_BAN: ["unknown", "unknown", "ok"]})
    executor = _executor(world, admin)
    op = SemanticOperation(C.MEMBER_BAN, {"user_id": 42})
    assert executor.provider(CTX, "comms_group_member_ban", BOT, op, REQ).state == "OUTCOME_UNKNOWN"
    retried = executor.provider(CTX, "comms_group_member_ban", BOT, op, REQ)
    assert retried.state == "OUTCOME_UNKNOWN" and len(admin.calls) == 2  # the one re-invocation
    assert admin.calls[0][2] == admin.calls[1][2] == op_key(CTX.client_ref, REQ)
    assert executor.provider(CTX, "comms_group_member_ban", BOT, op, REQ).state == "OUTCOME_UNKNOWN"
    assert len(admin.calls) == 2  # never a third


def test_unknown_create_or_bot_send_is_never_recalled(world):
    admin = FakeAdmin(world["conn"], {C.INVITE_CREATE: ["unknown"], C.MESSAGE_SEND: ["unknown"]})
    executor = _executor(world, admin)
    for tool, cap, req in (
        ("comms_invite_create", C.INVITE_CREATE, REQ),
        ("comms_message_send", C.MESSAGE_SEND, REQ2),
    ):
        op = SemanticOperation(cap, {})
        assert executor.provider(CTX, tool, BOT, op, req).state == "OUTCOME_UNKNOWN"
        assert executor.provider(CTX, tool, BOT, op, req).state == "OUTCOME_UNKNOWN"
    assert len(admin.calls) == 2


def test_an_unknown_mtproto_send_is_retried_once(world):
    admin = FakeAdmin(world["conn"], {C.MESSAGE_SEND: ["unknown", "ok"]})
    executor = _executor(world, admin)
    op = SemanticOperation(C.MESSAGE_SEND, {})
    executor.provider(CTX, "comms_message_send", USER, op, REQ)
    assert executor.provider(CTX, "comms_message_send", USER, op, REQ).state == "SUCCEEDED"


def test_local_mutation_and_its_audit_event_and_request_row_commit_together(world):
    conn = world["conn"]
    executor = _executor(world, FakeAdmin(conn))

    def apply(tx):
        tx.conn.execute(
            "INSERT INTO locations (ref, name, created_at) VALUES ('loc_"
            + "a" * 26
            + "', 'MQ', ?)",
            (tx.stamp,),
        )
        return {"location_ref": "loc_" + "a" * 26}

    fakes.plant_failure(conn, "mutations", "UPDATE OF state")
    events_before = conn.execute("SELECT count(*) FROM audit_events").fetchone()[0]
    with pytest.raises(Exception, match="planted"):
        executor.local(CTX, "comms_location_create", {}, {"name": "MQ"}, REQ, apply)
    assert conn.execute("SELECT count(*) FROM locations").fetchone()[0] == 0
    assert conn.execute("SELECT count(*) FROM mutations").fetchone()[0] == 0
    assert conn.execute("SELECT count(*) FROM audit_events").fetchone()[0] == events_before
    fakes.clear_planted(conn)
    outcome = executor.local(CTX, "comms_location_create", {}, {"name": "MQ"}, REQ, apply)
    assert outcome.state == "SUCCEEDED" and outcome.result == {"location_ref": "loc_" + "a" * 26}
    kinds = [
        r[0]
        for r in conn.execute("SELECT kind FROM audit_events WHERE kind LIKE 'admin.mutation%'")
    ]
    assert kinds == ["admin.mutation_started", "admin.mutation_finished"]


def test_local_replay_does_not_apply_twice(world):
    conn = world["conn"]
    executor = _executor(world, FakeAdmin(conn))
    applied = []

    def apply(tx):
        applied.append(1)
        return {"campaign_ref": "cmp_" + "a" * 26}

    first = executor.local(CTX, "comms_campaign_create", {}, {"name": "Nowruz"}, REQ, apply)
    again = executor.local(CTX, "comms_campaign_create", {}, {"name": "Nowruz"}, REQ, apply)
    assert (
        applied == [1]
        and again.replayed
        and again.result == first.result == {"campaign_ref": "cmp_" + "a" * 26}
    )
    with pytest.raises(CommsError) as refused:
        executor.local(CTX, "comms_campaign_create", {}, {"name": "Other"}, REQ, apply)
    assert refused.value.code == "REQUEST_ID_REUSE"


def test_bot_member_remove_saga_persists_each_step(world):
    conn = world["conn"]
    admin = FakeAdmin(conn)
    outcome = _executor(world, admin).provider(
        CTX,
        "comms_group_member_remove",
        BOT,
        SemanticOperation(C.MEMBER_REMOVE, {"user_id": 42}),
        REQ,
    )
    assert outcome.state == "SUCCEEDED"
    assert [(c, a) for c, a, _k, _keys in admin.calls] == [
        (C.MEMBER_BAN, {"user_id": 42}),
        (C.MEMBER_UNBAN, {"user_id": 42, "only_if_banned": True}),
    ]
    assert _steps(conn) == [(1, "member.ban", "SUCCEEDED"), (2, "member.unban", "SUCCEEDED")]
    steps = [
        r[0]
        for r in conn.execute("SELECT kind FROM audit_events WHERE kind = 'admin.mutation_step'")
    ]
    assert len(steps) == 2


def test_saga_resumes_unban_after_crash_between_steps(world):
    conn = world["conn"]
    admin = FakeAdmin(conn)
    op = SemanticOperation(C.MEMBER_REMOVE, {"user_id": 42})
    with pytest.raises(MutationCrash):
        _executor(world, admin, crash_at="between_steps").provider(
            CTX, "comms_group_member_remove", BOT, op, REQ
        )
    assert _steps(conn) == [(1, "member.ban", "SUCCEEDED"), (2, "member.unban", "PENDING")]
    outcome = _executor(world, admin).provider(CTX, "comms_group_member_remove", BOT, op, REQ)
    assert outcome.state == "SUCCEEDED" and [c for c, *_ in admin.calls] == [
        C.MEMBER_BAN,
        C.MEMBER_UNBAN,
    ]


def test_a_crash_after_the_call_retries_a_set_state_step_once(world):
    conn = world["conn"]
    admin = FakeAdmin(conn)
    op = SemanticOperation(C.MEMBER_BAN, {"user_id": 42})
    with pytest.raises(MutationCrash):
        _executor(world, admin, crash_at="after_call").provider(
            CTX, "comms_group_member_ban", BOT, op, REQ
        )
    assert _steps(conn) == [(1, "member.ban", "IN_FLIGHT")]
    assert (
        _executor(world, admin).provider(CTX, "comms_group_member_ban", BOT, op, REQ).state
        == "SUCCEEDED"
    )
    assert len(admin.calls) == 2


def test_a_crash_after_a_create_call_is_never_resent(world):
    conn = world["conn"]
    admin = FakeAdmin(conn)
    op = SemanticOperation(C.INVITE_CREATE, {})
    with pytest.raises(MutationCrash):
        _executor(world, admin, crash_at="after_call").provider(
            CTX, "comms_invite_create", BOT, op, REQ
        )
    assert (
        _executor(world, admin).provider(CTX, "comms_invite_create", BOT, op, REQ).state
        == "OUTCOME_UNKNOWN"
    )
    assert len(admin.calls) == 1


def test_provider_request_key_persisted_before_each_call(world):
    admin = FakeAdmin(world["conn"])
    _executor(world, admin).provider(
        CTX,
        "comms_group_member_remove",
        BOT,
        SemanticOperation(C.MEMBER_REMOVE, {"user_id": 42}),
        REQ,
    )
    key = op_key(CTX.client_ref, REQ)
    assert [keys for *_r, keys in admin.calls] == [[f"{key}:1"], [f"{key}:2"]]


def test_degraded_blocks_new_mutations_not_the_recording_of_started_ones(world):
    conn = world["conn"]

    class LatchingAdmin(FakeAdmin):
        def invoke(self, op, target, op_key_):
            result = super().invoke(op, target, op_key_)
            latch_degraded(self.conn, reason="ANCHOR_REFRESH_FAILED", now=NOW)  # mid-call
            return result

    admin = LatchingAdmin(conn)
    outcome = _executor(world, admin).provider(
        CTX,
        "comms_group_member_remove",
        BOT,
        SemanticOperation(C.MEMBER_REMOVE, {"user_id": 42}),
        REQ,
    )
    assert outcome.code == "AUDIT_INTEGRITY_DEGRADED" and len(admin.calls) == 1  # no second effect
    assert _steps(conn) == [
        (1, "member.ban", "SUCCEEDED"),
        (2, "member.unban", "PENDING"),
    ]  # recorded
    with pytest.raises(CommsError) as refused:
        _executor(world, admin).provider(
            CTX,
            "comms_group_member_ban",
            BOT,
            SemanticOperation(C.MEMBER_BAN, {"user_id": 7}),
            REQ2,
        )
    assert refused.value.code == "AUDIT_INTEGRITY_DEGRADED"
    assert conn.execute("SELECT count(*) FROM mutations").fetchone()[0] == 1


def test_only_the_provider_call_exception_becomes_unknown(world):
    conn = world["conn"]
    admin = FakeAdmin(conn, {C.MEMBER_BAN: ["raise"]})
    outcome = _executor(world, admin).provider(
        CTX, "comms_group_member_ban", BOT, SemanticOperation(C.MEMBER_BAN, {"user_id": 42}), REQ
    )
    assert outcome.state == "OUTCOME_UNKNOWN"

    def broken(tx):
        raise KeyError("a bug in the local apply")

    with pytest.raises(KeyError):
        _executor(world, admin).local(CTX, "comms_location_create", {}, {}, REQ2, broken)
    assert (
        conn.execute("SELECT count(*) FROM mutations WHERE request_id = ?", (REQ2,)).fetchone()[0]
        == 0
    )


def test_a_refusal_fails_the_mutation_and_stops_the_saga(world):
    conn = world["conn"]
    admin = FakeAdmin(conn, {C.MEMBER_BAN: ["refused"]})
    outcome = _executor(world, admin).provider(
        CTX,
        "comms_group_member_remove",
        BOT,
        SemanticOperation(C.MEMBER_REMOVE, {"user_id": 42}),
        REQ,
    )
    assert (outcome.state, outcome.code) == ("FAILED", "NOT_AUTHORIZED")
    assert _steps(conn) == [(1, "member.ban", "FAILED"), (2, "member.unban", "PENDING")]
    state, code, _retried, result = _mutation(conn)
    assert (state, code) == ("FAILED", "NOT_AUTHORIZED") and "42" not in result


def test_an_operation_the_actor_does_not_support_is_refused_without_a_row(world):
    with pytest.raises(CommsError) as refused:
        _executor(world, FakeAdmin(world["conn"])).provider(
            CTX, "comms_x", BOT, SemanticOperation(C.HISTORY_SEARCH, {}), REQ
        )
    assert refused.value.code == "PROVIDER_UNSUPPORTED"
    assert world["conn"].execute("SELECT count(*) FROM mutations").fetchone()[0] == 0
