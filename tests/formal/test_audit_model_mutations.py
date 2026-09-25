"""Each audit-model property must be able to fail (comms v0.3 Task B31)."""

import sys
import types
from pathlib import Path

import pytest

MODEL = Path(__file__).resolve().parents[2] / "formal" / "audit_model.py"

MUTATIONS = {
    "skip_an_epoch": (
        "return _grow(sealed, (head[0] + 1, 1))",
        "return _grow(sealed, (head[0] + 2, 1))",
        "Contiguity",
    ),
    "open_without_sealing": (
        "sealed = replace(s, seals=s.seals | {head}, checkpoints=s.checkpoints | {(*head, s.time)})",
        "sealed = replace(s, checkpoints=s.checkpoints | {(*head, s.time)})",
        "EveryNonFinalEpochSealed",
    ),
    "root_after_the_cutoff": (
        "roots = [(e, q) for e, q, t in s.checkpoints if t <= cutoff and (e, q) in s.events]",
        "roots = [(e, q) for e, q, t in s.checkpoints if (e, q) in s.events]",
        "TruncationOnlyAtRootBeforeCutoff",
    ),
    "append_after_the_seal": (
        "        if s.legacy_sealed:\n            return None  # the legacy database refuses (the seal trigger)\n",
        "",
        "NoAppendAfterSealed",
    ),
    "complete_with_a_different_lineage": (
        (
            '        if s.lineage == "different":\n'
            '            return replace(s, lineage="refused")  # a conflicting lineage fails closed\n'
            '        if s.lineage != "equal" or s.cutover_complete:'
        ),
        '        if s.lineage == "none" or s.cutover_complete:',
        "LineageEqualOrFailClosed",
    ),
    "verify_without_continuity": (
        "        if after != expected:\n            return False\n",
        "",
        "VerifyAcceptsExactlyLegitimate",
    ),
}


def test_every_property_has_a_mutation():
    sys.path.insert(0, str(MODEL.parents[1]))
    from formal.audit_model import PROPERTIES

    assert {invariant for _old, _new, invariant in MUTATIONS.values()} == set(PROPERTIES)


@pytest.mark.parametrize("case", sorted(MUTATIONS))
def test_breaking_the_guard_is_caught_by_its_property(case):
    old, new, invariant = MUTATIONS[case]
    source = MODEL.read_text(encoding="utf-8")
    assert source.count(old) == 1, "the mutation no longer matches the model"
    module = types.ModuleType(f"audit_mutant_{case}")
    sys.modules[module.__name__] = module  # dataclasses resolve their module
    try:
        exec(compile(source.replace(old, new), module.__name__, "exec"), module.__dict__)  # noqa: S102
        _visited, _hits, violations = module.explore(stop_on=invariant)
    finally:
        del sys.modules[module.__name__]
    assert any(name == invariant for name, _state in violations), case
