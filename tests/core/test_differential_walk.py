"""comms 5b-4 Task 14: the differential walk (design §11).

Seeded random operation sequences drive the bounded model (formal/campaign_model.py,
an independent restatement of the rules) and the real library side by side; after
every step the job states, lifecycle and summary must agree. A disagreement prints
the seed and the sequence so far.
"""

import random
import sys
import time
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from comms.core.campaigns import directory as d
from comms.core.campaigns import drafts
from comms.core.campaigns.drafts import LifecycleError
from comms.core.delivery import freeze
from comms.core.delivery import operations as ops
from comms.core.delivery.engine import Engine, ExecutorLease
from comms.core.delivery.recovery import recover
from comms.core.delivery.scheduling import run_due
from formal import campaign_model as m
from tests.core import fakes
from tests.core import schema_fixtures as fx
from tests.core.campaign_helpers import NOW

SEEDS = 300
MAX_LENGTH = 25
PHONES = ("+61400000001", "+61400000002")
OUTCOMES = {
    "accept": "accept",
    "deliver": "deliver",
    "transient_unsent": "transient",
    "permanent": "permanent",
    "unknown": "unknown_sent",
    "raise": "unknown_unsent",
}
SUCCESS = ("ACCEPTED", "DELIVERED")


class World:
    """The real library, driven through its public API with fake transports."""

    def __init__(self, tmp_path, name):
        self.conn = fx.migrated(tmp_path, name)
        self.wa = fakes.FakeWhatsApp(conn=self.conn)
        self.lease = ExecutorLease(fakes.FakeLock())
        self.now = NOW
        self.send_at = NOW
        self.body = 0
        audience = d.add_audience(self.conn, "A", now=NOW)
        self.points = []
        for phone in PHONES:
            rcp = d.add_recipient(self.conn, now=NOW)
            self.points.append(
                d.add_contact_point(self.conn, rcp, "whatsapp", phone, normalize=fx.wa, now=NOW)
            )
            d.add_audience_member(self.conn, audience, rcp)
        self.cmp = drafts.create_campaign(self.conn, "walk", now=NOW)
        drafts.set_content(self.conn, self.cmp, canonical="body-0", now=NOW)
        drafts.set_targets(
            self.conn, self.cmp, {"audiences": [audience]}, frozenset({"whatsapp"}), now=NOW
        )
        drafts.validate(self.conn, self.cmp, now=NOW)

    def engine(self):
        return Engine(self.conn, {"whatsapp": self.wa}, clock=lambda: self.now)

    def script(self, outcomes):
        self.wa.script = {phone: [outcome] for phone, outcome in zip(PHONES, outcomes, strict=True)}

    def jobs(self):
        return self.conn.execute(
            "SELECT j.ref, j.state FROM delivery_jobs j JOIN campaigns c ON c.current_generation_id ="
            " j.generation_id ORDER BY j.id"
        ).fetchall()

    def observe(self):
        lifecycle, summary = self.conn.execute(
            "SELECT lifecycle, summary FROM campaigns"
        ).fetchone()
        return lifecycle, summary or "", tuple(state for _, state in self.jobs())

    def reenable(self):
        for point in self.points:
            d.set_enabled(self.conn, point, True)


def model_execute(s, outcomes, crash_at=None):
    """The engine as a composition of the model's fine steps."""
    for i in range(len(s.jobs)):
        claimed = m.step(s, ("claim", i))
        if claimed is None:
            continue
        s = claimed
        if s.jobs[i].state != "IN_FLIGHT":
            continue  # skipped by revalidation
        if crash_at == i:
            s = m.step(s, ("deliver", i, "accept"))
            s = m.step(s, ("crash",))
            return m.step(s, ("recover",)) or s
        s = m.step(s, ("deliver", i, OUTCOMES[outcomes[i]]))
        s = m.step(s, ("record", i))
    return s


def model_observe(s):
    return s.lifecycle, s.summary, tuple(j.state for j in s.jobs)


def random_op(rng):
    kind = rng.choice(
        [
            "send",
            "schedule",
            "advance_clock",
            "run_due",
            "unschedule",
            "cancel",
            "invalidate",
            "execute",
            "execute",
            "crash",
            "provider",
            "retry",
            "resolve",
        ]
    )
    outcomes = tuple(rng.choice(list(OUTCOMES)) for _ in PHONES)
    return (
        kind,
        rng.randrange(len(PHONES)),
        outcomes,
        rng.choice(["ACCEPTED", "DELIVERED", "FAILED_PERMANENT"]),
        rng.choice(["sent", "not_sent"]),
    )


def apply(world, s, op, serial):
    kind, i, outcomes, status, verdict = op
    w = world
    if kind in ("send", "schedule"):
        nxt = m.step(s, (kind, ("PENDING",) * len(PHONES)))
        if nxt is None and s.lifecycle == "READY":
            return s  # the model's generation bound; the library is left alone too
        if nxt is not None:
            w.reenable()  # a new generation starts from a fully enabled directory, as the model's does
        at = w.now if kind == "send" else w.now + timedelta(hours=1)
        try:
            if kind == "send":
                freeze.send(w.conn, w.cmp, {"whatsapp": w.wa}, now=w.now)
            else:
                freeze.schedule(w.conn, w.cmp, at, {"whatsapp": w.wa}, now=w.now)
            w.send_at = at
        except LifecycleError:
            pass
        return nxt or s
    if kind == "advance_clock":
        nxt = m.step(s, ("advance_clock",))
        if nxt is not None:
            w.now = max(w.now, w.send_at + timedelta(seconds=1))
        return nxt or s
    if kind == "run_due":
        w.script(outcomes)
        run_due(w.lease, w.conn, w.engine(), now=w.now)
        nxt = m.step(s, ("run_due",))
        return model_execute(nxt, outcomes) if nxt is not None else s
    if kind == "unschedule":
        try:
            freeze.unschedule(w.conn, w.cmp, now=w.now)
            drafts.edit(w.conn, w.cmp, now=w.now)
            w.body += 1
            drafts.set_content(w.conn, w.cmp, canonical=f"body-{w.body}", now=w.now)
            drafts.validate(w.conn, w.cmp, now=w.now)
        except LifecycleError:
            pass
        return m.step(s, ("unschedule",)) or s
    if kind == "cancel":
        try:
            freeze.cancel(w.conn, w.cmp, now=w.now)
        except LifecycleError:
            pass
        return m.step(s, ("cancel",)) or s
    if kind == "invalidate":
        nxt = m.step(s, ("invalidate", i))
        if nxt is not None:
            d.set_enabled(w.conn, w.points[i], False)
        return nxt or s
    if kind == "execute":
        w.script(outcomes)
        w.engine().execute(w.lease, w.cmp)
        return model_execute(s, outcomes)
    if kind == "crash":
        w.wa.script = {PHONES[i]: ["crash_after_accept"]}
        try:
            w.engine().execute(w.lease, w.cmp)
        except fakes.SimulatedCrash:
            recover(w.lease, w.conn, now=w.now)
        return model_execute(s, ("accept",) * len(PHONES), crash_at=i)
    if kind == "provider":
        if i >= len(s.jobs) or not (s.jobs[i].provider_has and s.jobs[i].bound):
            return s
        ref = w.conn.execute(
            "SELECT a.provider_message_ref FROM delivery_attempts a JOIN delivery_jobs j ON j.id = a.job_id"
            " WHERE j.ref = ? ORDER BY a.attempt_no DESC",
            (w.jobs()[i][0],),
        ).fetchone()[0]
        ops.record_provider_update(w.conn, "whatsapp", f"evt-{serial}", ref, status, now=w.now)
        return m.step(s, ("provider", i, status)) or s
    if kind == "retry":
        try:
            ops.retry_failed(w.conn, w.cmp, now=w.now, cap=m.MODEL_RETRY_CAP)
        except LifecycleError:
            pass
        return m.step(s, ("retry",)) or s
    if kind == "resolve":
        if i < len(s.jobs):
            try:
                ops.resolve_outcome(w.conn, w.jobs()[i][0], verdict, now=w.now)
            except LifecycleError:
                pass
        return m.step(s, ("resolve", i, verdict)) or s
    raise AssertionError(kind)


def walk(tmp_path, seeds) -> tuple[str | None, int]:
    """Run the seeded sequences; return the first divergence (or None) and the step count."""
    steps = 0
    for seed in seeds:
        rng = random.Random(seed)
        world = World(tmp_path, f"s{seed}.db")
        state = m.State()
        history: list[str] = []
        try:
            if world.observe() != model_observe(state):
                return f"seed {seed} diverged at the start", steps
            for serial in range(rng.randint(1, MAX_LENGTH)):
                op = random_op(rng)
                history.append(op[0])
                state = apply(world, state, op, serial)
                steps += 1
                if world.observe() != model_observe(state):
                    return (
                        f"seed {seed} diverged after {history}: library {world.observe()}"
                        f" model {model_observe(state)}"
                    ), steps
        finally:
            world.conn.close()
    return None, steps


def test_the_model_and_the_library_agree_on_every_step(tmp_path):
    started = time.monotonic()
    divergence, steps = walk(tmp_path, range(SEEDS))
    elapsed = time.monotonic() - started
    assert divergence is None, divergence
    print(f"\ndifferential walk: {SEEDS} sequences, {steps} steps, {elapsed:.1f} s")
    assert elapsed < 45, elapsed


def test_the_walk_catches_a_planted_library_defect(tmp_path, monkeypatch):
    from comms.core.delivery import reducer

    real = reducer.summarize

    def sent_on_any_success(states, any_attempt):
        result = real(states, any_attempt)
        return "SENT" if result == "PARTIAL" else result

    monkeypatch.setattr(reducer, "summarize", sent_on_any_success)
    divergence, _ = walk(tmp_path, range(SEEDS))
    assert divergence is not None and "diverged" in divergence
