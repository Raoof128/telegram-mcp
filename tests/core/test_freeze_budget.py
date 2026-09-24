"""comms 5b-4 Task 14: the freeze of 5,000 endpoints stays inside a measured budget (R22, M2, P13).

The measured floor for inserting 5,000 jobs and 5,000 origins is 88 ms (M2); the budget
of 3 s is about 30× that, room for resolution, prepare and hashing, while a pathological
regression (a per-endpoint scan, a transaction per job) still fails it. Only the freeze
call is timed; the directory is built beforehand in one transaction.
"""

import time

from comms.core import refs
from comms.core.campaigns import drafts
from comms.core.delivery import freeze
from comms.core.storage.db import write_tx
from tests.core import fakes
from tests.core import schema_fixtures as fx
from tests.core.campaign_helpers import NOW

ENDPOINTS = 5_000
BUDGET_SECONDS = 3.0


def test_freeze_of_5000_endpoints_is_within_budget(tmp_path):
    conn = fx.migrated(tmp_path)
    audience = refs.mint("audience")
    with write_tx(conn):
        conn.execute(
            "INSERT INTO audiences (ref, name, created_at) VALUES (?, 'all', ?)", (audience, fx.T0)
        )
        for n in range(ENDPOINTS):
            rid, _ = fx.recipient(conn)
            ident = fx.identity(conn, "whatsapp", f"+614{n:08d}")
            fx.contact_point(conn, rid, ident, "whatsapp")
            conn.execute(
                "INSERT INTO audience_members (audience_id, member_recipient_id) VALUES (1, ?)",
                (rid,),
            )
    cmp = drafts.create_campaign(conn, "big", now=NOW)
    drafts.set_content(conn, cmp, canonical="hello", now=NOW)
    drafts.set_targets(conn, cmp, {"audiences": [audience]}, frozenset({"whatsapp"}), now=NOW)
    drafts.validate(conn, cmp, now=NOW)
    wa = fakes.FakeWhatsApp(conn=conn)
    started = time.perf_counter()
    freeze.send(conn, cmp, {"whatsapp": wa}, now=NOW)
    elapsed = time.perf_counter() - started
    print(
        f"\nfreeze of {ENDPOINTS} endpoints: {elapsed * 1000:.0f} ms (budget {BUDGET_SECONDS:.0f} s)"
    )
    assert conn.execute("SELECT count(*) FROM delivery_jobs").fetchone()[0] == ENDPOINTS
    assert conn.execute("SELECT count(*) FROM job_origins").fetchone()[0] == ENDPOINTS
    assert elapsed < BUDGET_SECONDS
