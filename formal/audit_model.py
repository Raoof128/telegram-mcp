"""The audit-chain model (comms v0.3 Task B31, design §E.2): epochs, truncation, cutover lineage.

An explicit finite state machine explored exhaustively, in the style of ``model.py`` and
``campaign_model.py``. The chain is abstracted to retained ``(epoch, seq)`` positions; a
checkpoint is a signed position with a creation time; the anchor names the head the
writer last committed; an attacker may delete any retained event without the truncation
guard. ``verify`` is the abstract ``verify_chain`` plus the anchor comparison; the
differential walk (``tests/core/audit/test_audit_differential_walk.py``) checks it against
the real engine on 200 seeded sequences.

``full`` is the chain as legitimate operations alone would leave it (retention included).
A prefix cut at a signed checkpoint is, by design, indistinguishable from retention, so
the chains verify must accept are exactly the suffixes of ``full`` that start at the
genesis or at a checkpoint and end at the anchor.

Properties:

- ``Contiguity``: the legitimate chain's epochs are contiguous.
- ``EveryNonFinalEpochSealed``: in the legitimate chain every epoch but the last is sealed.
- ``TruncationOnlyAtRootBeforeCutoff``: truncation cuts only at a checkpoint created at or
  before the cutoff whose row is retained.
- ``NoAppendAfterSealed``: the legacy chain takes no append once sealed.
- ``LineageEqualOrFailClosed``: the cutover completes only with the lineage equal to the
  legacy seal; a different seal fails closed.
- ``VerifyAcceptsExactlyLegitimate``: ``verify`` accepts exactly the acceptable chains.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, replace

MAX_EPOCH = 3
PER_EPOCH = 2
MAX_TIME = 1

Position = tuple[int, int]


@dataclass(frozen=True)
class State:
    events: tuple[Position, ...] = ((1, 1),)  # what is retained now
    full: tuple[Position, ...] = ((1, 1),)  # what legitimate operations alone would retain
    anchor: Position = (1, 1)  # the head the writer last anchored
    seals: frozenset[Position] = frozenset()  # the positions EPOCH_SEAL checkpoints sign
    checkpoints: frozenset[tuple[int, int, int]] = frozenset()  # (epoch, seq, created time)
    time: int = 0
    bad_truncation: bool = False
    legacy_sealed: bool = False
    legacy_append_after_seal: bool = False
    lineage: str = "none"  # "none" | "equal" | "different" | "refused"
    cutover_complete: bool = False


def _signed(s: State, position: Position) -> bool:
    return any((e, q) == position for e, q, _t in s.checkpoints)


def verify(s: State) -> bool:
    """The abstract verify_chain + anchor: continuity, contiguity, seals, root, head."""
    if not s.events or s.events[-1] != s.anchor:
        return False
    if s.events[0] != (1, 1) and not _signed(s, s.events[0]):
        return False  # a cut chain must start at a signed root
    epochs = [e for e, _q in s.events]
    if sorted(set(epochs)) != list(range(epochs[0], epochs[-1] + 1)):
        return False
    for before, after in zip(s.events, s.events[1:], strict=False):
        if after[0] == before[0]:
            expected = (before[0], before[1] + 1)
        else:
            if before not in s.seals:
                return False  # an epoch must end exactly at its seal
            expected = (before[0] + 1, 1)
        if after != expected:
            return False
    return all(e != epochs[-1] for e, _q in s.seals)  # a sealed last epoch lost its successor


def acceptable(s: State) -> bool:
    """A suffix of the legitimate chain, from the genesis or a signed root, to the anchor."""
    n = len(s.events)
    if n == 0 or n > len(s.full) or s.events != s.full[len(s.full) - n :]:
        return False
    return (s.events[0] == (1, 1) or _signed(s, s.events[0])) and s.events[-1] == s.anchor


def _grow(s: State, *positions: Position) -> State:
    return replace(
        s, events=(*s.events, *positions), full=(*s.full, *positions), anchor=positions[-1]
    )


def step(s: State, op: tuple) -> State | None:
    kind = op[0]
    head = s.full[-1]
    if kind == "append":
        if head[1] >= PER_EPOCH or s.events[-1:] != (head,):
            return None  # the writer appends only to an intact head
        return _grow(s, (head[0], head[1] + 1))
    if kind == "seal_and_open":
        if head[0] >= MAX_EPOCH or s.events[-1:] != (head,):
            return None
        sealed = replace(s, seals=s.seals | {head}, checkpoints=s.checkpoints | {(*head, s.time)})
        return _grow(sealed, (head[0] + 1, 1))
    if kind == "checkpoint":
        if s.events[-1:] != (head,):
            return None
        return replace(s, checkpoints=s.checkpoints | {(*head, s.time)})
    if kind == "tick":
        return replace(s, time=s.time + 1) if s.time < MAX_TIME else None
    if kind == "truncate":
        cutoff = op[1]
        roots = [(e, q) for e, q, t in s.checkpoints if t <= cutoff and (e, q) in s.events]
        if not roots:
            return None
        root = max(roots)
        eligible = any(t <= cutoff for e, q, t in s.checkpoints if (e, q) == root)
        return replace(
            s,
            events=tuple(p for p in s.events if p >= root),
            full=tuple(p for p in s.full if p >= root),
            bad_truncation=s.bad_truncation or not eligible,
        )
    if kind == "attack_delete":
        index = op[1]
        if index >= len(s.events):
            return None
        return replace(s, events=s.events[:index] + s.events[index + 1 :])
    if kind == "legacy_append":
        if s.legacy_sealed:
            return None  # the legacy database refuses (the seal trigger)
        return replace(s, legacy_append_after_seal=s.legacy_append_after_seal or s.legacy_sealed)
    if kind == "legacy_seal":
        return None if s.legacy_sealed else replace(s, legacy_sealed=True)
    if kind == "record_lineage":
        if not s.legacy_sealed or s.lineage != "none":
            return None
        return replace(s, lineage=op[1])  # "equal": the recorded seal; "different": another
    if kind == "complete":
        if s.lineage == "different":
            return replace(s, lineage="refused")  # a conflicting lineage fails closed
        if s.lineage != "equal" or s.cutover_complete:
            return None
        return replace(s, cutover_complete=True)
    raise ValueError(kind)


def operations(s: State) -> list[tuple]:
    ops: list[tuple] = [("append",), ("seal_and_open",), ("checkpoint",), ("tick",)]
    ops += [("truncate", cutoff) for cutoff in range(MAX_TIME + 1)]
    ops += [("attack_delete", i) for i in range(len(s.events))]
    ops += [("legacy_append",), ("legacy_seal",), ("complete",)]
    ops += [("record_lineage", seal) for seal in ("equal", "different")]
    return ops


def _epochs(s: State) -> list[int]:
    return sorted({e for e, _q in s.full})


PROPERTIES: dict[str, Callable[[State], bool]] = {
    "Contiguity": lambda s: _epochs(s) == list(range(_epochs(s)[0], _epochs(s)[-1] + 1)),
    "EveryNonFinalEpochSealed": lambda s: all(
        any(e == sealed for sealed, _q in s.seals) for e in _epochs(s)[:-1]
    ),
    "TruncationOnlyAtRootBeforeCutoff": lambda s: not s.bad_truncation,
    "NoAppendAfterSealed": lambda s: not s.legacy_append_after_seal,
    "LineageEqualOrFailClosed": lambda s: not s.cutover_complete or s.lineage == "equal",
    "VerifyAcceptsExactlyLegitimate": lambda s: verify(s) == acceptable(s),
}


def explore(stop_on: str | None = None) -> tuple[int, dict[str, int], list[tuple[str, State]]]:
    """Exhaustive BFS: visited count, per-property hits, violations (``stop_on``: fast path)."""
    start = State()
    seen = {start}
    queue = deque([start])
    hits = dict.fromkeys(PROPERTIES, 0)
    violations: list[tuple[str, State]] = []
    while queue:
        state = queue.popleft()
        for name, holds in PROPERTIES.items():
            if holds(state):
                hits[name] += 1
            else:
                violations.append((name, state))
                if name == stop_on:
                    return len(seen), hits, violations
        for op in operations(state):
            nxt = step(state, op)
            if nxt is not None and nxt not in seen:
                seen.add(nxt)
                queue.append(nxt)
    return len(seen), hits, violations
