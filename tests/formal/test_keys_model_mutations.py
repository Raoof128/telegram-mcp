"""Each key/credential-model property must be able to fail (comms v0.3 Task B32)."""

import sys
import types
from pathlib import Path

import pytest

MODEL = Path(__file__).resolve().parents[2] / "formal" / "keys_model.py"

MUTATIONS = {
    "begin_deletes_the_live_key": (
        "cleaned = frozenset({s.pointer}) if s.pointer == s.file_key else s.stored",
        "cleaned = frozenset()",
        "ExactlyOneKeyOpensAndRecoveryFindsIt",
    ),
    "activate_an_unproved_candidate": (
        'if op == "activate":\n        if s.candidate == "none" or not s.proved:',
        'if op == "activate":\n        if s.candidate == "none":',
        "FailedCandidateNeverActive",
    ),
    "keep_a_candidate_that_failed_its_recheck": (
        "s, active=s.previous, rechecking=False",
        "s, rechecking=False",
        "FailedCandidateNeverActive",
    ),
    "destroy_before_the_reopen": (
        'if op == "destroy":\n        if s.phase != "pointed":',
        'if op == "destroy":\n        if s.phase not in ("rekeyed", "pointed"):',
        "OldDestroyedOnlyAfterVerifiedReopen",
    ),
}


def test_every_property_has_a_mutation():
    sys.path.insert(0, str(MODEL.parents[1]))
    from formal.keys_model import PROPERTIES

    assert {invariant for _old, _new, invariant in MUTATIONS.values()} == set(PROPERTIES)


@pytest.mark.parametrize("case", sorted(MUTATIONS))
def test_breaking_the_guard_is_caught_by_its_property(case):
    old, new, invariant = MUTATIONS[case]
    source = MODEL.read_text(encoding="utf-8")
    assert source.count(old) == 1, "the mutation no longer matches the model"
    module = types.ModuleType(f"keys_mutant_{case}")
    sys.modules[module.__name__] = module  # dataclasses resolve their module
    try:
        exec(compile(source.replace(old, new), module.__name__, "exec"), module.__dict__)  # noqa: S102
        _visited, _hits, violations = module.explore(stop_on=invariant)
    finally:
        del sys.modules[module.__name__]
    assert any(name == invariant for name, _state in violations), case
