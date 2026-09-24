"""comms v0.3 Task D5: the mutation crash table and degraded outcomes (G14–G16, A8, A41).

There is no "after PREPARED" boundary: PREPARED is not a stored state (D3). After a crash at any
boundary, ``recover_mutations`` plus a replay of the same request leaves every mutation terminal
or correctly OUTCOME_UNKNOWN; no resolve-only call is ever repeated; SET_STATE saga steps resume.
Recovery itself never calls a provider.
"""

import pytest

from comms.core.audit import writer as writer_module
from comms.core.providers.capability import Capability as C
from comms.core.providers.protocols import ProviderResult, ProviderTarget, SemanticOperation
from comms.services.mutations import CallContext, MutationCrash, MutationExecutor
from comms.services.recovery import recover_mutations
from tests.core.audit.legacy_fixtures import comms_world

REQ = "req_" + "a" * 26
CTX = CallContext(client_ref="cli_" + "c" * 26)
BOT = ProviderTarget("telegram", "telegram_bot", "dst_" + "g" * 26, "-1000000000077")


class Died(BaseException):
    """The process dies inside the provider call."""


class Admin:
    def __init__(self, die_on=None):
        self.calls, self.die_on = [], die_on

    def validate(self, op, target):
        return None

    def invoke(self, op, target, key):
        self.calls.append(op.capability)
        if self.die_on is not None and len(self.calls) == self.die_on:
            raise Died
        return ProviderResult("SUCCEEDED", None)


OPERATIONS = {
    "set_state": ("comms_group_member_ban", SemanticOperation(C.MEMBER_BAN, {"user_id": 42})),
    "saga": ("comms_group_member_remove", SemanticOperation(C.MEMBER_REMOVE, {"user_id": 42})),
    "create": ("comms_invite_create", SemanticOperation(C.INVITE_CREATE, {})),
}


@pytest.fixture
def world(tmp_path):
    return comms_world(tmp_path)


def _run(world, admin, kind, **kw):
    tool, op = OPERATIONS[kind]
    return MutationExecutor(world["writer"], {"telegram_bot": admin}, **kw).provider(
        CTX, tool, BOT, op, REQ
    )


def _state(world):
    return (
        world["conn"].execute("SELECT state, provider_code, audit_status FROM mutations").fetchone()
    )


def _fail_anchor_on(monkeypatch, nth):
    """The anchor refresh fails on its nth call from now (the commit before it stands)."""
    real, calls = writer_module.write_anchor, []

    def flaky(*args, **kwargs):
        calls.append(1)
        if len(calls) == nth:
            raise OSError("anchor volume gone")
        return real(*args, **kwargs)

    monkeypatch.setattr(writer_module, "write_anchor", flaky)


BOUNDARIES = ["before_call", "during_call", "after_call", "after_record"]
CASES = [(kind, boundary) for kind in OPERATIONS for boundary in BOUNDARIES] + [
    ("saga", "between_steps")
]


@pytest.mark.parametrize("kind,boundary", CASES)
def test_every_boundary_converges(world, kind, boundary):
    admin = Admin(die_on=1 if boundary == "during_call" else None)
    with pytest.raises((MutationCrash, Died)):
        _run(world, admin, kind, crash_at=None if boundary == "during_call" else boundary)
    admin.die_on = None
    recover_mutations(world["writer"])
    outcome = _run(world, admin, kind)
    state, code, _audit = _state(world)
    assert outcome.state == state and state in ("SUCCEEDED", "FAILED", "OUTCOME_UNKNOWN")
    if boundary == "before_call":
        assert (state, code) == ("FAILED", "NOT_ATTEMPTED") and admin.calls == []
    elif kind == "create":
        assert len(admin.calls) == 1  # a resolve-only call is never repeated
        assert state == ("SUCCEEDED" if boundary == "after_record" else "OUTCOME_UNKNOWN")
    else:
        assert state == "SUCCEEDED"  # set-state steps are retried once or resumed
        if kind == "saga":
            assert admin.calls[-1] is C.MEMBER_UNBAN


def test_anchor_failure_after_provider_success_is_degraded_not_success(world, monkeypatch):
    admin = Admin()
    _fail_anchor_on(monkeypatch, 3)  # start (1), step start (2), the step's record (3)
    outcome = _run(world, admin, "set_state")
    assert outcome.code == "AUDIT_INTEGRITY_DEGRADED" and len(admin.calls) == 1
    assert _state(world)[2] == "DEGRADED"
    recover_mutations(world["writer"])
    assert _state(world)[:2] == ("SUCCEEDED", None)  # the known outcome is kept, never re-effected
    assert len(admin.calls) == 1


def test_recover_marks_pre_call_degraded_mutations_failed_not_attempted(world, monkeypatch):
    admin = Admin()
    _fail_anchor_on(monkeypatch, 1)  # the IN_FLIGHT commit's own anchor
    outcome = _run(world, admin, "saga")
    assert outcome.code == "AUDIT_INTEGRITY_DEGRADED" and admin.calls == []
    assert _state(world) == ("IN_FLIGHT", None, "DEGRADED")
    report = recover_mutations(world["writer"])
    assert report.not_attempted == 1
    assert _state(world)[:2] == ("FAILED", "NOT_ATTEMPTED")
    steps = [
        r[0] for r in world["conn"].execute("SELECT state FROM mutation_steps ORDER BY step_no")
    ]
    assert steps == ["FAILED", "FAILED"] and admin.calls == []


def test_recovery_is_idempotent_and_never_calls_a_provider(world):
    admin = Admin()
    with pytest.raises(MutationCrash):
        _run(world, admin, "saga", crash_at="between_steps")
    first = recover_mutations(world["writer"])
    second = recover_mutations(world["writer"])
    assert second.settled == 0 and first.resumable == 1 and len(admin.calls) == 1


def test_a_saga_that_failed_before_finishing_keeps_its_code(world):
    class Refusing(Admin):
        def invoke(self, op, target, key):
            self.calls.append(op.capability)
            return ProviderResult("FAILED", "NOT_AUTHORIZED")

    with pytest.raises(MutationCrash):
        _run(world, Refusing(), "saga", crash_at="after_record")
    assert recover_mutations(world["writer"]).settled == 1
    assert _state(world)[:2] == ("FAILED", "NOT_AUTHORIZED")
