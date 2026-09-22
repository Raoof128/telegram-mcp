"""Run the bounded model exhaustively (Appendix L, Gate Q)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from formal.model import ASSERTIONS, explore


def test_every_reachable_state_satisfies_every_assertion():
    visited, _hits, violations = explore()

    assert violations == [], violations[:3]
    assert visited > 100, f"state space too small to be meaningful: {visited}"
    print(f"\nexplored {visited} reachable states, {len(ASSERTIONS)} assertions")


def test_no_assertion_is_unreachable():
    """An assertion that never evaluates is not passing -- it is dead.

    This is the check that stops a model from proving nothing: every
    predicate must actually be exercised by at least one reachable state.
    """
    visited, hits, _ = explore()

    dead = [name for name, count in hits.items() if count == 0]
    assert dead == [], f"assertions never evaluated: {dead}"
    for name, count in sorted(hits.items()):
        print(f"  {name:42} {count}/{visited}")
