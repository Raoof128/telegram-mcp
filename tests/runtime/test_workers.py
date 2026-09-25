"""D39-PRE Task E5: startup recovery and supervised workers, with the owner's two failure classes."""

import asyncio
import logging

import pytest
import sqlcipher3

from comms.core.audit.chain import ChainError
from comms.core.audit.integrity import is_degraded
from comms.core.delivery.engine import ExecutorLease
from comms.runtime.workers import DaemonStop, Loop, Workers, startup_recovery
from comms.transports.telegram.bot.http import BotTransportError
from tests.core.audit.legacy_fixtures import comms_world
from tests.core.campaign_helpers import NOW


class Held:
    def held(self):
        return True


@pytest.fixture
def conn(tmp_path):
    return comms_world(tmp_path)["conn"]


class Script:
    """A step that raises the scripted exceptions in turn, then succeeds; counts its calls."""

    def __init__(self, *raises):
        self.raises, self.calls = list(raises), 0

    def __call__(self):
        self.calls += 1
        if self.raises:
            raise self.raises.pop(0)


async def _run_until(workers, predicate, timeout=5.0):
    stop = asyncio.Event()
    task = asyncio.create_task(workers.run(stop))
    for _ in range(int(timeout / 0.01)):
        if predicate() or task.done():
            break
        await asyncio.sleep(0.01)
    stop.set()
    await asyncio.wait_for(task, timeout)


def test_a_recoverable_failure_backs_off_and_the_others_keep_running(conn, caplog):
    flaky = Script(BotTransportError("not_sent"), BotTransportError("ambiguous"))
    steady = Script()
    workers = Workers(conn, (Loop("flaky", flaky, 0.01, False), Loop("steady", steady, 0.01, False)),
                      clock=lambda: NOW)  # fmt: skip
    with caplog.at_level(logging.WARNING, logger="comms.workers"):
        asyncio.run(_run_until(workers, lambda: flaky.calls >= 3 and steady.calls >= 3))
    assert flaky.calls >= 3 and steady.calls >= 3
    failures = [r.getMessage() for r in caplog.records]
    assert failures.count("worker_failed name=flaky class=recoverable") == 2
    assert not is_degraded(conn)


def test_a_safety_failure_latches_degraded_and_stops_effect_loops(conn):
    effect = Script(ChainError("mac mismatch"))
    reader = Script()
    workers = Workers(conn, (Loop("effect", effect, 0.01, True), Loop("reader", reader, 0.01, False)),
                      clock=lambda: NOW)  # fmt: skip
    asyncio.run(_run_until(workers, lambda: reader.calls >= 5))
    assert is_degraded(conn)
    assert effect.calls == 1  # never called again once the trail is degraded
    assert reader.calls >= 5  # reads go on


def test_an_unknown_exception_type_fails_closed(conn):
    effect = Script(KeyError("surprise"))
    workers = Workers(conn, (Loop("effect", effect, 0.01, True),), clock=lambda: NOW)
    asyncio.run(_run_until(workers, lambda: effect.calls >= 1 and is_degraded(conn)))
    assert is_degraded(conn)


def test_database_corruption_stops_the_daemon(conn):
    broken = Script(sqlcipher3.dbapi2.DatabaseError("database disk image is malformed"))
    workers = Workers(conn, (Loop("broken", broken, 0.01, False),), clock=lambda: NOW)
    with pytest.raises(DaemonStop):
        asyncio.run(_run_until(workers, lambda: False))


def test_the_log_carries_no_exception_text(conn, caplog):
    leaky = Script(TimeoutError("token 123:ABC in the url"))
    workers = Workers(conn, (Loop("leaky", leaky, 0.01, False),), clock=lambda: NOW)
    with caplog.at_level(logging.DEBUG, logger="comms.workers"):
        asyncio.run(_run_until(workers, lambda: leaky.calls >= 2))
    assert "123:ABC" not in caplog.text


def test_stop_returns_promptly_even_with_long_periods(conn):
    slow = Script()
    workers = Workers(conn, (Loop("slow", slow, 3600.0, False),), clock=lambda: NOW)

    async def go():
        stop = asyncio.Event()
        task = asyncio.create_task(workers.run(stop))
        await asyncio.sleep(0.05)
        stop.set()
        await asyncio.wait_for(task, 1.0)

    asyncio.run(go())
    assert slow.calls == 1


def test_startup_recovery_reports_counts_only(tmp_path):
    world = comms_world(tmp_path)
    report = startup_recovery(_State(world), ExecutorLease(Held()), now=NOW)
    assert set(report) == {"mutations_settled", "mutations_unknown", "mutations_resumable",
                           "deliveries_marked_unknown", "campaigns_resumable"}  # fmt: skip
    assert all(isinstance(v, int) for v in report.values())


class _State:
    def __init__(self, world):
        self.conn, self.writer = world["conn"], world["writer"]


def test_the_deliver_loop_delivers_a_frozen_send(tmp_path):
    """campaign.send only freezes; before E5 nothing in production ever delivered it."""
    from comms.core.delivery import freeze
    from comms.runtime.adapters import Adapters
    from comms.runtime.workers import build_workers
    from tests.core import fakes
    from tests.core import schema_fixtures as fx
    from tests.core.campaign_helpers import job_states, person, ready

    conn = fx.migrated(tmp_path)
    tx = {"whatsapp": fakes.FakeWhatsApp(conn=conn)}
    rcp, _ = person(conn, phone="+61400000001")
    cmp = ready(conn, {"recipients": [rcp]})
    freeze.send(conn, cmp, tx, now=NOW)
    assert set(job_states(conn, cmp).values()) == {"PENDING"}

    state = _State({"conn": conn, "writer": None})
    workers = build_workers(state, Adapters(delivery=tx), ExecutorLease(Held()), clock=lambda: NOW,
                            intervals={"deliver": 0.01, "schedule": 3600})  # fmt: skip
    asyncio.run(_run_until(workers, lambda: "PENDING" not in job_states(conn, cmp).values()))
    assert "PENDING" not in job_states(conn, cmp).values()
    assert [loop.name for loop in workers.loops] == ["deliver", "schedule"]  # no bot, no webhook


def test_startup_recovery_settles_a_mutation_a_crash_left_in_flight(tmp_path):
    from comms.core.providers.capability import Capability as C
    from comms.core.providers.protocols import ProviderResult, ProviderTarget, SemanticOperation
    from comms.services.mutations import CallContext, MutationCrash, MutationExecutor

    class Admin:
        def validate(self, op, target):
            return None

        def invoke(self, op, target, key):
            return ProviderResult("SUCCEEDED", None)

    world = comms_world(tmp_path)
    target = ProviderTarget("telegram", "telegram_bot", "dst_" + "g" * 26, "-1000000000077")
    executor = MutationExecutor(world["writer"], {"telegram_bot": Admin()}, crash_at="after_call")
    with pytest.raises(MutationCrash):
        executor.provider(CallContext(client_ref="cli_" + "c" * 26), "comms_x", target,
                          SemanticOperation(C.MEMBER_BAN, {"user_id": 42}), "req_" + "w" * 26)  # fmt: skip
    assert world["conn"].execute("SELECT state FROM mutations").fetchone()[0] == "IN_FLIGHT"
    report = startup_recovery(_State(world), ExecutorLease(Held()), now=NOW)
    assert (
        report["mutations_settled"] + report["mutations_unknown"] + report["mutations_resumable"]
        == 1
    )
    assert world["conn"].execute("SELECT state FROM mutations").fetchone()[0] != "IN_FLIGHT"
