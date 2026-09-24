"""Bounded executable model of the comms campaign core (comms 5b-4 design §11, R17).

A second, smaller statement of the rules — it imports nothing from ``src`` — explored
exhaustively. Every variable is bounded, so the breadth-first search visits every
reachable state. Monitors are flags a transition sets when something forbidden
*happens* (a claim of an unresolved unknown, an early claim); they are never cleared,
so a property over them is a property over every path.

The differential walk (tests/core/test_differential_walk.py) drives this model and the
real library with the same operations and compares them after every step.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, replace
from itertools import product

__all__ = [
    "MODEL_RETRY_CAP",
    "N_JOBS",
    "PROPERTIES",
    "Job",
    "State",
    "explore",
    "step",
    "summarize",
]

MODEL_RETRY_CAP = 2
N_JOBS = 2
MAX_GENERATIONS = 2

RANK = {
    "PENDING": 0,
    "IN_FLIGHT": 1,
    "FAILED_TRANSIENT": 2,
    "OUTCOME_UNKNOWN": 2,
    "ACCEPTED": 3,
    "DELIVERED": 4,
}
ACTIVE = ("PENDING", "IN_FLIGHT")
SUCCESS = ("ACCEPTED", "DELIVERED")
KINDS = ("ACCEPTED", "DELIVERED", "FAILED_TRANSIENT", "FAILED_PERMANENT", "OUTCOME_UNKNOWN")
DELIVER_OUTCOMES = {  # outcome → (result kind, did the provider receive it)
    "accept": ("ACCEPTED", True),
    "deliver": ("DELIVERED", True),
    "transient": ("FAILED_TRANSIENT", False),
    "permanent": ("FAILED_PERMANENT", False),
    "unknown_sent": ("OUTCOME_UNKNOWN", True),
    "unknown_unsent": ("OUTCOME_UNKNOWN", False),
}


@dataclass(frozen=True)
class Job:
    state: str = "PENDING"
    attempts: int = 0
    eligible: bool = True
    phase: str = ""  # "" | "claimed" | a result kind returned by deliver, not yet recorded
    provider_has: bool = False  # the provider holds a message for this job
    bound: bool = False  # the current attempt's provider reference is recorded
    pending: str = ""  # a provider status received before the reference was bound
    blocked: bool = False  # OUTCOME_UNKNOWN, awaiting an explicit resolution
    ever_delivered: bool = False


@dataclass(frozen=True)
class State:
    lifecycle: str = "READY"
    clock: str = "before"  # relative to the current generation's send time
    scheduled: bool = False  # the current generation was scheduled, not sent now
    gens: int = 0  # generations created so far
    content: int = 0  # content version; an unschedule is followed by an edit
    keys: tuple[tuple[int, int, int], ...] = ()  # (generation, job, content) per key minted
    jobs: tuple[Job, ...] = ()
    summary: str = ""
    # Monitors: set when a forbidden thing happens, never cleared.
    claimed_blocked: bool = False
    early_claim: bool = False
    claimed_ineligible: bool = False
    empty_generation: bool = False
    late_touched: bool = False


def reduce_job(job: Job, evidence: str, value: str = "", current: bool = True) -> Job:
    """The reducer, restated: the job after ``evidence``; unchanged if not applicable."""
    s = job.state
    new = None
    if evidence == "PROVIDER":
        if value in SUCCESS:
            new = value if s in RANK and RANK[value] > RANK[s] else None
        elif current and s in ("IN_FLIGHT", "ACCEPTED", "OUTCOME_UNKNOWN"):
            new = "FAILED_PERMANENT"
    elif evidence == "RESULT":
        new = value if s == "IN_FLIGHT" and current else None
    elif evidence == "CLAIM" and s == "PENDING":
        new = "IN_FLIGHT"
    elif evidence in ("CANCEL", "SKIP") and s == "PENDING":
        new = "CANCELLED" if evidence == "CANCEL" else "SKIPPED_REVALIDATION"
    elif evidence == "RETRY" and s == "FAILED_TRANSIENT" and job.attempts < MODEL_RETRY_CAP:
        new = "PENDING"
    elif evidence == "RECOVER" and s == "IN_FLIGHT":
        new = "OUTCOME_UNKNOWN"
    elif evidence == "RESOLVE_SENT" and s == "OUTCOME_UNKNOWN":
        new = "ACCEPTED"
    elif evidence == "RESOLVE_NOT_SENT" and s == "OUTCOME_UNKNOWN":
        new = "FAILED_TRANSIENT"
    if new is None:
        return job
    # An unknown stays blocked until an operator resolves it; only then may it be retried.
    blocked = new == "OUTCOME_UNKNOWN" or (
        job.blocked and evidence not in ("RESOLVE_NOT_SENT", "RESOLVE_SENT")
    )
    return replace(
        job, state=new, blocked=blocked, ever_delivered=job.ever_delivered or new == "DELIVERED"
    )


def summarize(states: tuple[str, ...], any_attempt: bool) -> str:
    """§6.3, restated."""
    if any(s in ACTIVE for s in states):
        return "IN_PROGRESS"
    if "OUTCOME_UNKNOWN" in states:
        return "INDETERMINATE"
    wins = sum(s in SUCCESS for s in states)
    if wins == len(states):
        return "SENT"
    if wins:
        return "PARTIAL"
    if "CANCELLED" in states and not any_attempt:
        return "CANCELLED"
    return "FAILED"


def _settle(s: State) -> State:
    """Re-derive the summary and complete an idle sending campaign (same transaction, R7)."""
    if not s.jobs:
        return s
    summary = summarize(tuple(j.state for j in s.jobs), any(j.attempts for j in s.jobs))
    lifecycle = s.lifecycle
    if lifecycle == "SENDING" and not any(j.state in ACTIVE for j in s.jobs):
        lifecycle = "COMPLETE"
    return replace(s, summary=summary, lifecycle=lifecycle)


def _set(s: State, i: int, job: Job) -> State:
    return replace(s, jobs=s.jobs[:i] + (job,) + s.jobs[i + 1 :])


def _freeze(s: State, mask: tuple[str, ...], scheduled: bool) -> State | None:
    if s.lifecycle != "READY" or s.gens >= MAX_GENERATIONS:
        return None
    empty = "PENDING" not in mask
    if empty:
        return None  # NO_ELIGIBLE_ENDPOINTS (R8)
    gen = s.gens
    jobs = tuple(Job(state=m) for m in mask)
    return _settle(
        replace(
            s,
            lifecycle="SCHEDULED" if scheduled else "SENDING",
            clock="before",
            scheduled=scheduled,
            gens=s.gens + 1,
            keys=s.keys + tuple((gen, i, s.content) for i in range(len(mask))),
            jobs=jobs,
            empty_generation=s.empty_generation or empty,
        )
    )


def step(s: State, op: tuple) -> State | None:
    """Apply one operation; ``None`` if it is not enabled in ``s``."""
    name = op[0]
    if name in ("send", "schedule"):
        return _freeze(s, op[1] if len(op) > 1 else ("PENDING",) * N_JOBS, name == "schedule")
    if name == "advance_clock":
        return replace(s, clock="after") if s.clock == "before" else None
    if name == "run_due":
        if s.lifecycle == "SCHEDULED" and s.clock == "after":
            return _settle(replace(s, lifecycle="SENDING"))
        return None
    if name in ("unschedule", "cancel") and s.lifecycle == "SCHEDULED":
        # The generation is discarded (its PENDING jobs cancelled); nothing of it stays current.
        return replace(
            s,
            lifecycle="READY" if name == "unschedule" else "CANCELLED",
            jobs=(),
            summary="",
            content=s.content + (name == "unschedule"),
        )
    if name == "cancel" and s.lifecycle == "READY":
        return replace(s, lifecycle="CANCELLED")
    if name == "cancel" and s.lifecycle == "SENDING":
        return _settle(replace(s, jobs=tuple(reduce_job(j, "CANCEL") for j in s.jobs)))
    if name == "retry":
        if s.lifecycle not in ("SENDING", "COMPLETE"):
            return None
        jobs = tuple(reduce_job(j, "RETRY") for j in s.jobs)
        if jobs == s.jobs:
            return None
        return _settle(replace(s, lifecycle="SENDING", jobs=jobs))
    if name == "crash":
        if not any(j.phase for j in s.jobs):
            return None
        return replace(s, jobs=tuple(replace(j, phase="") for j in s.jobs))
    if name == "recover":
        if any(j.phase for j in s.jobs) or not any(j.state == "IN_FLIGHT" for j in s.jobs):
            return None
        return _settle(replace(s, jobs=tuple(reduce_job(j, "RECOVER") for j in s.jobs)))
    if len(op) < 2:
        return None  # a campaign-level operation not enabled in this lifecycle
    i = op[1]
    if i >= len(s.jobs):
        return None
    job = s.jobs[i]
    if name == "invalidate":
        return (
            _set(s, i, replace(job, eligible=False))
            if job.eligible and job.state == "PENDING"
            else None
        )
    if name == "claim":
        if job.state != "PENDING" or s.lifecycle != "SENDING":
            return None
        if not job.eligible:
            return _settle(_set(s, i, reduce_job(job, "SKIP")))
        claimed = replace(
            reduce_job(job, "CLAIM"), attempts=job.attempts + 1, phase="claimed", bound=False
        )
        s = replace(
            s,
            claimed_blocked=s.claimed_blocked or job.blocked,
            early_claim=s.early_claim or (s.scheduled and s.clock == "before"),
            claimed_ineligible=s.claimed_ineligible or not job.eligible,
        )
        return _settle(_set(s, i, claimed))
    if name == "deliver":
        if job.phase != "claimed":
            return None
        kind, sent = DELIVER_OUTCOMES[op[2]]
        return _set(s, i, replace(job, phase=kind, provider_has=job.provider_has or sent))
    if name == "record":
        if job.phase not in KINDS:
            return None
        kind = job.phase
        done = replace(reduce_job(job, "RESULT", kind), phase="", bound=kind in SUCCESS)
        if done.bound and done.pending:
            done = replace(reduce_job(done, "PROVIDER", done.pending), pending="")
        return _settle(_set(s, i, done))
    if name == "provider":
        if not job.provider_has:
            return None
        if not job.bound:
            return _set(s, i, replace(job, pending=job.pending or op[2]))
        return _settle(_set(s, i, reduce_job(job, "PROVIDER", op[2])))
    if name == "late_failure":
        if job.attempts < 2:
            return None
        after = reduce_job(job, "PROVIDER", "FAILED_PERMANENT", current=False)
        if after == job:
            return s
        return _settle(replace(_set(s, i, after), late_touched=True))
    if name == "resolve":
        evidence = "RESOLVE_SENT" if op[2] == "sent" else "RESOLVE_NOT_SENT"
        after = reduce_job(job, evidence)
        if after == job:
            return None
        return _settle(_set(s, i, after))
    return None


def operations(s: State) -> list[tuple]:
    ops: list[tuple] = []
    for mask in product(("PENDING", "SKIPPED_PLATFORM_POLICY"), repeat=N_JOBS):
        ops += [("send", mask), ("schedule", mask)]
    ops += [
        ("advance_clock",),
        ("run_due",),
        ("unschedule",),
        ("cancel",),
        ("retry",),
        ("crash",),
        ("recover",),
    ]
    for i in range(len(s.jobs)):
        ops += [("invalidate", i), ("claim", i), ("record", i), ("late_failure", i)]
        ops += [("deliver", i, outcome) for outcome in DELIVER_OUTCOMES]
        ops += [("provider", i, status) for status in ("ACCEPTED", "DELIVERED", "FAILED_PERMANENT")]
        ops += [("resolve", i, verdict) for verdict in ("sent", "not_sent")]
    return ops


def _all_success(s: State) -> bool:
    return all(j.state in SUCCESS for j in s.jobs)


PROPERTIES: dict[str, Callable[[State], bool]] = {
    "NeverBelowDeliveredOnceThere": lambda s: all(
        j.state == "DELIVERED" for j in s.jobs if j.ever_delivered
    ),
    "UnknownGainsAttemptOnlyAfterResolveNotSent": lambda s: not s.claimed_blocked,
    "OneKeyNamesOnePayload": lambda s: len({(g, j) for g, j, _c in s.keys}) == len(s.keys),
    "NoExecutionBeforeSendAt": lambda s: not s.early_claim,
    "ExecutionSetWithinFrozenSet": lambda s: not s.claimed_ineligible,
    "NeverStrandedInSending": lambda s: (
        not (s.lifecycle == "SENDING" and s.jobs and not any(j.state in ACTIVE for j in s.jobs))
    ),
    "NoEmptyGeneration": lambda s: not s.empty_generation,
    "SentOnlyIfEveryJobSucceeded": lambda s: s.summary != "SENT" or _all_success(s),
    "UnknownNeverYieldsFailed": lambda s: (
        not (s.summary == "FAILED" and any(j.state == "OUTCOME_UNKNOWN" for j in s.jobs))
    ),
    "EarlyProviderUpdateNeverLost": lambda s: not any(j.bound and j.pending for j in s.jobs),
    "EarlierAttemptFailureNeverTouchesTheJob": lambda s: not s.late_touched,
}


def explore(stop_on: str | None = None) -> tuple[int, dict[str, int], list[tuple[str, State]]]:
    """Exhaustive BFS: visited count, per-property hits, violations. With ``stop_on``,
    return at the first violation of that property (the mutation tests' fast path)."""
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
