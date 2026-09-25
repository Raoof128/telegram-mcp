"""comms v0.3 Task D35: the admin-operation differential walk — 300 seeds against the real
executor, checking the model's claims on every run.

Each seed picks a retry class (a CREATE, resolve-only; a SET_STATE, one same-key retry), then
replays one request id 1–4 times with random provider outcomes (success, refusal, a lost
outcome, an exception at the call boundary) and random crashes after the call or after the
record, each crash followed by recovery. What the model proves, the walk observes:

- a CREATE reaches the provider at most once for its request id; a SET_STATE at most twice;
- a record only moves forward, and a success is final;
- every answer agrees with the record it was read from.
"""

import random
from itertools import pairwise

import pytest

from comms.core.providers.capability import Capability as C
from comms.core.providers.protocols import ProviderResult, ProviderTarget, SemanticOperation
from comms.services.mutations import CallContext, MutationCrash, MutationExecutor
from comms.services.recovery import recover_mutations
from tests.core.audit.legacy_fixtures import comms_world

CTX = CallContext(client_ref="cli_" + "c" * 26)
BOT = ProviderTarget("telegram", "telegram_bot", "dst_" + "g" * 26, "-1000000000077")
REQ = "req_" + "w" * 26
KINDS = {
    "create": (SemanticOperation(C.INVITE_CREATE, {}), 1),
    "set_state": (SemanticOperation(C.MEMBER_BAN, {"user_id": 42}), 2),
}
ORDER = {"IN_FLIGHT": 0, "OUTCOME_UNKNOWN": 1, "FAILED": 2, "SUCCEEDED": 2}


class Scripted:
    def __init__(self, rng):
        self.rng, self.calls = rng, 0

    def validate(self, op, target):
        return None

    def invoke(self, op, target, key):
        self.calls += 1
        outcome = self.rng.choice(["ok", "ok", "refused", "lost", "raise"])
        if outcome == "raise":
            raise RuntimeError("the provider connection dropped")
        if outcome == "lost":
            return ProviderResult("OUTCOME_UNKNOWN", None)
        if outcome == "refused":
            return ProviderResult("FAILED", "NOT_AUTHORIZED")
        return ProviderResult("SUCCEEDED", None, provider_ref="https://t.me/+AbCdEf123")


def _record(conn):
    row = conn.execute("SELECT state FROM mutations").fetchone()
    return None if row is None else row[0]


@pytest.mark.parametrize("seed", range(300))
def test_the_walk_agrees_with_the_model(tmp_path, seed):
    rng = random.Random(seed)
    world = comms_world(tmp_path)
    kind = rng.choice(sorted(KINDS))
    op, max_calls = KINDS[kind]
    admin = Scripted(rng)
    history = []
    for _ in range(rng.randint(1, 4)):
        crash = rng.choice([None, None, None, "after_call", "after_record"])
        executor = MutationExecutor(world["writer"], {"telegram_bot": admin}, crash_at=crash)
        try:
            outcome = executor.provider(CTX, "comms_x", BOT, op, REQ)
        except MutationCrash:
            recover_mutations(world["writer"])
            outcome = None
        state = _record(world["conn"])
        if outcome is not None:
            assert outcome.state == state or outcome.code == "AUDIT_INTEGRITY_DEGRADED", (
                seed,
                outcome,
            )
        history.append(state)
    assert admin.calls <= max_calls, (seed, kind, admin.calls)
    seen = [s for s in history if s is not None]
    assert all(ORDER[a] <= ORDER[b] for a, b in pairwise(seen)), (seed, seen)
    if "SUCCEEDED" in seen:
        assert seen[-1] == "SUCCEEDED"
