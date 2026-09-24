"""The key and credential rotation model (comms v0.3 Task B32).

Two processes explored together, exhaustively:

**The comms.db rekey (A14, Task B12).** Versions are 1..3 and the key of version ``v`` is
``v``. The sequence is stage → ``PRAGMA rekey`` → verified reopen → pointer switch →
destroy the old version; a crash may stop it at any boundary, and startup recovery tries
the pointer's key, then every other stored version, repairing the pointer to the one that
opens the file. A new rekey first removes versions a crash left behind.

**A provider credential rotation (A13, Task B13).** A candidate is proved; only a proved
candidate activates; a re-check after activation either keeps it (the old one is then
destroyed) or rolls back to the intact old one.

Properties:

- ``ExactlyOneKeyOpensAndRecoveryFindsIt``: the file's key is stored and recovery finds it.
- ``FailedCandidateNeverActive``: a candidate that failed its proof is never active.
- ``OldDestroyedOnlyAfterVerifiedReopen``: the old database key is destroyed only after the
  new one reopened the file.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, replace

MAX_VERSION = 3
REKEY_PHASES = ("idle", "staged", "rekeyed", "reopened", "pointed")


@dataclass(frozen=True)
class State:
    # the rekey
    stored: frozenset[int] = frozenset({1})
    pointer: int = 1
    file_key: int = 1
    phase: str = "idle"
    new: int = 0  # the version being introduced
    reopened: frozenset[int] = frozenset({1})  # versions that have verifiably opened the file
    destroyed_unverified: bool = False
    # the credential: "good" passes both checks, "bad" fails its proof, "flaky" passes its
    # proof and fails the re-check after activation
    active: str = "old"
    previous: str = "old"  # the intact old credential a failed re-check restores
    candidate: str = "none"
    proved: bool = False
    rechecking: bool = False


def recover(s: State) -> int | None:
    """``open_with_recovery``: the pointer's key, else the one other stored key that opens."""
    if s.pointer in s.stored and s.pointer == s.file_key:
        return s.pointer
    opening = [v for v in sorted(s.stored) if v != s.pointer and v == s.file_key]
    return opening[0] if len(opening) == 1 else None


def _rekey(s: State, op: str) -> State | None:
    if op == "begin":
        if s.phase != "idle":
            return None
        new = max(s.stored | {s.pointer}) + 1
        if new > MAX_VERSION:
            return None
        cleaned = frozenset({s.pointer}) if s.pointer == s.file_key else s.stored
        return replace(s, stored=cleaned | {new}, phase="staged", new=new)
    if op == "rekey":
        return replace(s, file_key=s.new, phase="rekeyed") if s.phase == "staged" else None
    if op == "reopen":
        if s.phase != "rekeyed":
            return None
        return replace(s, reopened=s.reopened | {s.new}, phase="reopened")
    if op == "switch":
        return replace(s, pointer=s.new, phase="pointed") if s.phase == "reopened" else None
    if op == "destroy":
        if s.phase != "pointed":
            return None
        old = max(v for v in s.stored if v != s.new)
        unverified = s.destroyed_unverified or s.new not in s.reopened
        return replace(
            s, stored=s.stored - {old}, phase="idle", new=0, destroyed_unverified=unverified
        )
    if op == "crash_and_recover":
        if s.phase == "idle":
            return None
        found = recover(s)
        if found is None:
            return replace(s, phase="idle", new=0, pointer=-1)  # recovery failed: fail closed
        return replace(s, pointer=found, phase="idle", new=0)
    raise ValueError(op)


def _credential(s: State, op: str) -> State | None:
    if op.startswith("stage_"):
        if s.candidate != "none" or s.rechecking:
            return None
        return replace(s, candidate=op.removeprefix("stage_"), proved=False)
    if op == "prove":
        if s.candidate == "none" or s.proved:
            return None
        if s.candidate == "bad":
            return replace(s, candidate="none")  # the rejected candidate is destroyed
        return replace(s, proved=True)
    if op == "activate":
        if s.candidate == "none" or not s.proved:
            return None
        return replace(
            s,
            previous=s.active,
            active=s.candidate,
            candidate="none",
            proved=False,
            rechecking=True,
        )
    if op == "recheck":
        if not s.rechecking:
            return None
        if s.active == "flaky":
            return replace(
                s, active=s.previous, rechecking=False
            )  # roll back to the intact old one
        return replace(s, previous=s.active, rechecking=False)  # only now is the old one destroyed
    raise ValueError(op)


def step(s: State, op: tuple[str, str]) -> State | None:
    process, name = op
    return _rekey(s, name) if process == "rekey" else _credential(s, name)


def operations(_s: State) -> list[tuple[str, str]]:
    rekey = ("begin", "rekey", "reopen", "switch", "destroy", "crash_and_recover")
    credential = ("stage_good", "stage_bad", "stage_flaky", "prove", "activate", "recheck")
    return [("rekey", o) for o in rekey] + [("credential", o) for o in credential]


PROPERTIES: dict[str, Callable[[State], bool]] = {
    "ExactlyOneKeyOpensAndRecoveryFindsIt": lambda s: (
        s.file_key in s.stored and recover(s) == s.file_key
    ),
    "FailedCandidateNeverActive": lambda s: (
        s.active != "bad" and not (s.active == "flaky" and not s.rechecking)
    ),
    "OldDestroyedOnlyAfterVerifiedReopen": lambda s: not s.destroyed_unverified,
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
