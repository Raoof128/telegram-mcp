"""5b-3 test accounting (owner gate G2): unexpectedly missing = 0.

Re-derives the set of test IDs collected before 5b-3 and absent after it from
the two committed manifests, and requires it to equal exactly the classified
set: removed with the consent subsystem, or replaced by a named owner-direct
test that is itself collected.
"""

import json
from pathlib import Path

EVIDENCE = Path(__file__).resolve().parents[2] / "docs" / "verification"


def _manifest(name: str) -> set[str]:
    text = (EVIDENCE / name).read_text(encoding="utf-8")
    return {line for line in text.splitlines() if line}


def test_every_missing_test_is_classified_and_nothing_else_is():
    before = _manifest("comms-5b3-collected-before.txt")
    after = _manifest("comms-5b3-collected-after.txt")
    classified = json.loads((EVIDENCE / "comms-5b3-classification.json").read_text("utf-8"))
    removed = set(classified["removed_with_consent"])
    replaced = classified["replaced_by_owner_direct"]
    assert not removed & set(replaced), "a test is either removed or replaced, never both"
    assert before - after == removed | set(replaced)


def test_every_replacement_is_a_collected_test():
    after = _manifest("comms-5b3-collected-after.txt")
    classified = json.loads((EVIDENCE / "comms-5b3-classification.json").read_text("utf-8"))
    for old, new in classified["replaced_by_owner_direct"].items():
        assert new in after, (old, new)
