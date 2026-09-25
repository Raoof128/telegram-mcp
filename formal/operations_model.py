"""The admin-operation model (comms v0.3 Task D35; design §E.2, A27, A28, A41, A8).

One ``(client, request_id)`` explored exhaustively under duplicate requests, retries,
reconnects, a crash at any point with recovery, a provider outcome that is lost, and an audit
anchor that fails. Two retry classes are explored: a ``CREATE`` (resolve-only: an ambiguous
outcome is never re-invoked) and a ``SET_STATE`` (retry the same key once: repeating it has
the same effect). Separately, an MTProto send carrying a ``random_id``: at most one identical
reissue inside the window, and Telegram shows at most one message per ``random_id``.

Properties:

- ``AtMostOneNonIdempotentEffect``: a CREATE is effected at most once for one request id.
- ``NormalSucceededIsDurableChainedAnchored``: a normal ``SUCCEEDED`` answer implies the
  record is ``SUCCEEDED`` and its events are chained and anchored.
- ``DegradedSuccessGivesTheDegradedAnswer``: a success whose finish could not be anchored is
  never answered as a normal success.
- ``DegradedBlocksNewEffects``: once degraded, no new mutation is born and no call starts; a
  started one can still be recorded (completion stays reachable: checked on the graph).
- ``MtprotoOneReissueOneVisibleEffect``: at most one extra RPC with the same ``random_id``, at
  most one visible message, and a finished send is ``ACCEPTED`` or ``UNKNOWN``.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, replace

MAX_REQUESTS = 3
MAX_CRASHES = 1


@dataclass(frozen=True)
class State:
    retry_class: str = "CREATE"  # or "SET_STATE"
    record: str = "NONE"  # NONE | IN_FLIGHT | SUCCEEDED | FAILED | OUTCOME_UNKNOWN
    anchored: bool = True  # every event of this record is chained and anchored
    degraded: bool = False  # the audit-integrity latch
    in_call: bool = False  # a provider call is running (between before_call and the record)
    retried: bool = False
    effects: int = 0  # provider effects of this request id (a SET_STATE repeat is the same one)
    requests: int = 0
    crashes: int = 0
    answers: frozenset[str] = frozenset()  # what the client has been told
    born_degraded: bool = False  # a record was born, or a call started, while degraded
    # the MTProto send
    rpcs: int = 0  # sendMessage RPCs carrying this random_id
    visible: int = 0  # messages Telegram shows for it
    send: str = "idle"  # idle | first | ambiguous | ACCEPTED | UNKNOWN


def _effect(s: State) -> int:
    return s.effects + 1 if s.retry_class == "CREATE" or s.effects == 0 else s.effects


def _answer(s: State, answer: str) -> State:
    return replace(s, answers=s.answers | {answer})


def _admin(s: State, op: str) -> State | None:
    if op == "request":  # a new call, a duplicate, a retry or a reconnect: same request id
        if s.requests >= MAX_REQUESTS or s.in_call:
            return None
        s = replace(s, requests=s.requests + 1)
        if s.record == "NONE":
            if s.degraded:  # AUDIT_INTEGRITY_DEGRADED: nothing is born
                return _answer(s, "REFUSED_DEGRADED")
            return replace(s, record="IN_FLIGHT", in_call=True)  # born with its chained event
        if s.record in ("SUCCEEDED", "FAILED"):  # the replay answers from the record
            normal = s.anchored
            return _answer(s, f"{s.record}_{'NORMAL' if normal else 'DEGRADED'}")
        if s.record == "OUTCOME_UNKNOWN":
            if s.retry_class == "SET_STATE" and not s.retried and not s.degraded:
                return replace(s, in_call=True, retried=True)  # re-invoked once, same key
            return _answer(s, "UNKNOWN")
        return _answer(s, "IN_FLIGHT")  # a started call is not started again
    if op in ("provider_succeeded", "provider_lost_after_effect", "provider_lost_before_effect"):
        if not s.in_call:
            return None
        effected = op != "provider_lost_before_effect"
        effects = _effect(s) if effected else s.effects
        record = "SUCCEEDED" if op == "provider_succeeded" else "OUTCOME_UNKNOWN"
        return replace(s, in_call=False, effects=effects, record=record)
    if op == "provider_refused":
        return replace(s, in_call=False, record="FAILED") if s.in_call else None
    if op in ("finish_anchored", "finish_degraded"):  # the finish event and its answer
        if s.in_call or s.record not in ("SUCCEEDED", "FAILED", "OUTCOME_UNKNOWN"):
            return None
        if f"{s.record}_NORMAL" in s.answers or f"{s.record}_DEGRADED" in s.answers:
            return None
        if s.record == "OUTCOME_UNKNOWN" and "UNKNOWN" in s.answers:
            return None
        if op == "finish_degraded":
            s = replace(s, anchored=False, degraded=True)
            label = "UNKNOWN" if s.record == "OUTCOME_UNKNOWN" else f"{s.record}_DEGRADED"
            return _answer(s, label)
        label = "UNKNOWN" if s.record == "OUTCOME_UNKNOWN" else f"{s.record}_NORMAL"
        return _answer(s, label)
    if op == "crash_mid_call_before_effect" or op == "crash_mid_call_after_effect":
        if not s.in_call or s.crashes >= MAX_CRASHES:
            return None
        effects = _effect(s) if op.endswith("after_effect") else s.effects
        return replace(s, in_call=False, effects=effects, crashes=s.crashes + 1)
    if op == "other_operation_degraded":  # another request's anchor failed: the latch is set
        return None if s.degraded else replace(s, degraded=True)
    if op == "recover":  # recovery settles, never re-invokes a started call
        if s.in_call or s.record != "IN_FLIGHT":
            return None
        return replace(s, record="OUTCOME_UNKNOWN")
    raise ValueError(op)


def _mtproto(s: State, op: str) -> State | None:
    if op == "send":
        return replace(s, send="first", rpcs=s.rpcs + 1) if s.send == "idle" else None
    if op == "sent":
        if s.send not in ("first", "reissued"):
            return None
        return replace(s, send="ACCEPTED", visible=1)  # Telegram dedupes by random_id
    if op == "ambiguous":
        if s.send == "first":
            return replace(s, send="ambiguous", visible=max(s.visible, 0))
        if s.send == "reissued":
            return replace(s, send="UNKNOWN")
        return None
    if op == "ambiguous_but_delivered":
        if s.send not in ("first", "reissued"):
            return None
        return replace(s, send="ambiguous" if s.send == "first" else "UNKNOWN", visible=1)
    if op == "reissue":  # the one identical reissue inside the window
        return replace(s, send="reissued", rpcs=s.rpcs + 1) if s.send == "ambiguous" else None
    if op == "window_closes":
        return replace(s, send="UNKNOWN") if s.send == "ambiguous" else None
    raise ValueError(op)


def step(s: State, op: tuple[str, str]) -> State | None:
    process, name = op
    return _admin(s, name) if process == "admin" else _mtproto(s, name)


ADMIN_OPS = (
    "request",
    "provider_succeeded",
    "provider_refused",
    "provider_lost_after_effect",
    "provider_lost_before_effect",
    "finish_anchored",
    "finish_degraded",
    "crash_mid_call_before_effect",
    "crash_mid_call_after_effect",
    "recover",
    "other_operation_degraded",
)
MTPROTO_OPS = ("send", "sent", "ambiguous", "ambiguous_but_delivered", "reissue", "window_closes")


def operations(_s: State) -> list[tuple[str, str]]:
    return [("admin", o) for o in ADMIN_OPS] + [("mtproto", o) for o in MTPROTO_OPS]


PROPERTIES: dict[str, Callable[[State], bool]] = {
    "AtMostOneNonIdempotentEffect": lambda s: s.retry_class != "CREATE" or s.effects <= 1,
    "NormalSucceededIsDurableChainedAnchored": lambda s: (
        "SUCCEEDED_NORMAL" not in s.answers or (s.record == "SUCCEEDED" and s.anchored)
    ),
    "DegradedSuccessGivesTheDegradedAnswer": lambda s: (
        not (s.record == "SUCCEEDED" and not s.anchored and "SUCCEEDED_NORMAL" in s.answers)
    ),
    "DegradedBlocksNewEffects": lambda s: not s.born_degraded,
    "MtprotoOneReissueOneVisibleEffect": lambda s: (
        s.rpcs <= 2
        and s.visible <= 1
        and s.send in ("idle", "first", "ambiguous", "reissued", "ACCEPTED", "UNKNOWN")
    ),
}


def _mark_birth(before: State, after: State) -> State:
    """A record born, or a call started, while the latch is set violates the degraded rule."""
    started = (not before.in_call and after.in_call) or (before.record == "NONE" != after.record)
    if started and before.degraded:
        return replace(after, born_degraded=True)
    return after


def explore(stop_on: str | None = None) -> tuple[int, dict[str, int], list[tuple[str, State]]]:
    """Exhaustive BFS over both retry classes; also checks completion stays reachable."""
    starts = [State(retry_class="CREATE"), State(retry_class="SET_STATE")]
    seen = set(starts)
    queue = deque(starts)
    edges: dict[State, list[State]] = {}
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
        nexts = []
        for op in operations(state):
            nxt = step(state, op)
            if nxt is None:
                continue
            nxt = _mark_birth(state, nxt)
            nexts.append(nxt)
            if nxt not in seen:
                seen.add(nxt)
                queue.append(nxt)
        edges[state] = nexts
    for state in seen:  # completion stays reachable from every started record, degraded or not
        if state.record == "IN_FLIGHT" and not _reaches_a_record(state, edges):
            violations.append(("DegradedBlocksNewEffects", state))
    return len(seen), hits, violations


def _reaches_a_record(start: State, edges: dict[State, list[State]]) -> bool:
    stack, seen = [start], {start}
    while stack:
        state = stack.pop()
        if state.record in ("SUCCEEDED", "FAILED", "OUTCOME_UNKNOWN"):
            return True
        for nxt in edges.get(state, ()):
            if nxt not in seen:
                seen.add(nxt)
                stack.append(nxt)
    return False
