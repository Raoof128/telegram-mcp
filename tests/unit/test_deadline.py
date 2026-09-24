import asyncio

import pytest

from comms.transports.telegram.telegram.deadline import (
    Deadline,
    DeadlineExceeded,
    FairScheduler,
    WorkBudget,
    WorkBudgetExceeded,
)


def test_deadline_counts_down_and_expires():
    now = [0.0]
    deadline = Deadline(15, clock=lambda: now[0])
    assert deadline.remaining() == 15
    now[0] = 14.5
    assert deadline.remaining() == pytest.approx(0.5)
    now[0] = 15
    with pytest.raises(DeadlineExceeded):
        deadline.remaining()


def test_work_budget_caps_rpcs():
    budget = WorkBudget(max_rpcs=2)
    budget.spend()
    budget.spend()
    with pytest.raises(WorkBudgetExceeded):
        budget.spend()
    assert budget.used == 2


async def test_one_client_cannot_hold_every_slot():
    scheduler = FairScheduler(total=4, per_client=2)
    entered: list[str] = []
    release = asyncio.Event()

    async def call(client):
        async with scheduler.slot(client):
            entered.append(client)
            await release.wait()

    tasks = [asyncio.create_task(call("a")) for _ in range(4)]
    tasks.append(asyncio.create_task(call("b")))
    await asyncio.sleep(0.05)
    assert entered.count("a") == 2 and entered.count("b") == 1
    release.set()
    await asyncio.gather(*tasks)
    assert entered.count("a") == 4
