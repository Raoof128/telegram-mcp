"""Each admin-operation-model property must be able to fail (comms v0.3 Task D35)."""

import sys
import types
from pathlib import Path

import pytest

MODEL = Path(__file__).resolve().parents[2] / "formal" / "operations_model.py"

MUTATIONS = {
    "a_create_is_re_invoked_after_an_unknown_outcome": (
        'if s.retry_class == "SET_STATE" and not s.retried and not s.degraded:',
        "if not s.retried and not s.degraded:",
        "AtMostOneNonIdempotentEffect",
    ),
    "recovery_re_invokes_a_started_call": (
        'return replace(s, record="OUTCOME_UNKNOWN")\n    raise',
        "return replace(s, in_call=True)\n    raise",
        "AtMostOneNonIdempotentEffect",
    ),
    "a_replay_answers_normal_whatever_the_anchor": (
        "normal = s.anchored",
        "normal = True",
        "NormalSucceededIsDurableChainedAnchored",
    ),
    "a_degraded_finish_is_answered_as_normal": (
        'label = "UNKNOWN" if s.record == "OUTCOME_UNKNOWN" else f"{s.record}_DEGRADED"',
        'label = "UNKNOWN" if s.record == "OUTCOME_UNKNOWN" else f"{s.record}_NORMAL"',
        "DegradedSuccessGivesTheDegradedAnswer",
    ),
    "a_record_is_born_while_degraded": (
        "if s.degraded:  # AUDIT_INTEGRITY_DEGRADED: nothing is born",
        "if False:  # AUDIT_INTEGRITY_DEGRADED: nothing is born",
        "DegradedBlocksNewEffects",
    ),
    "a_second_reissue_with_the_same_random_id": (
        'return replace(s, send="reissued", rpcs=s.rpcs + 1) if s.send == "ambiguous" else None',
        'return replace(s, send="ambiguous", rpcs=s.rpcs + 1) if s.send == "ambiguous" else None',
        "MtprotoOneReissueOneVisibleEffect",
    ),
    "telegram_does_not_dedupe_the_random_id": (
        'return replace(s, send="ACCEPTED", visible=1)  # Telegram dedupes by random_id',
        'return replace(s, send="ACCEPTED", visible=s.visible + 1)',
        "MtprotoOneReissueOneVisibleEffect",
    ),
}


def test_every_property_has_a_mutation():
    sys.path.insert(0, str(MODEL.parents[1]))
    from formal.operations_model import PROPERTIES

    assert {invariant for _old, _new, invariant in MUTATIONS.values()} == set(PROPERTIES)


@pytest.mark.parametrize("case", sorted(MUTATIONS))
def test_breaking_the_guard_is_caught_by_its_property(case):
    old, new, invariant = MUTATIONS[case]
    source = MODEL.read_text(encoding="utf-8")
    assert source.count(old) == 1, "the mutation no longer matches the model"
    module = types.ModuleType(f"operations_mutant_{case}")
    sys.modules[module.__name__] = module  # dataclasses resolve their module
    try:
        exec(compile(source.replace(old, new), module.__name__, "exec"), module.__dict__)  # noqa: S102
        _visited, _hits, violations = module.explore(stop_on=invariant)
    finally:
        del sys.modules[module.__name__]
    assert any(name == invariant for name, _state in violations), case
