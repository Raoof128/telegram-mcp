"""How much can a compliant client extract? Run it once, seal the number.

A scripted client extracts as much as the rules permit from the fake adapter
across a simulated twenty-four hours under a named synthetic corpus and the
default budget configuration.

**The figure is benchmark-observed, not a property of the world.** It is what
one greedy client got out of a fake adapter under a named corpus and a named
configuration. It is not a measurement of real private content — Phase 3
never touches any — and it is a lower bound on what a cleverer client might
achieve even here.
"""

from tests.authority_fixtures import PROJECT_REF
from tests.coordinator_fixtures import build_coordinator

# Named synthetic corpus: 20 records per call, ~120 bytes each.
CORPUS_NAME = "synthetic-uniform-v1"
RECORDS_PER_CALL = 20
SIMULATED_HOURS = 24
WINDOW_MINUTES = 30


def _corpus():
    return [
        {
            "message_ref": "tgm_" + f"{i:026d}".replace("0", "a"),
            "origin_project_refs": [PROJECT_REF],
            "text": "x" * 100,
            "text_truncated": False,
        }
        for i in range(RECORDS_PER_CALL)
    ]


async def test_a_greedy_client_is_bounded_and_the_total_is_recorded(tmp_path, capsys):
    coordinator, conn, adapter = build_coordinator(tmp_path, records=_corpus())

    released = 0
    refused = 0
    records = 0
    # Each window is a fresh budget. A patient extractor paces across
    # windows, which the budget deliberately permits -- see the honest bound
    # in the design: budgets limit burst rate, not lifetime exposure.
    windows = SIMULATED_HOURS * 60 // WINDOW_MINUTES
    for _window in range(windows):
        conn.execute("DELETE FROM exposure_ledger")  # the window rolls over
        conn.commit()
        for _call in range(200):  # ask until refused
            outcome = await coordinator.disclose(
                tool_name="telegram_get_messages", arguments={}, adapter=adapter
            )
            if outcome.released:
                released += 1
                records += outcome.meta["disclosure"]["proof_payload"]["records_disclosed"]
            else:
                refused += 1
                break

    ledger_rows = conn.execute("SELECT count(*) FROM disclosure_receipts").fetchone()[0]

    print(
        f"\ncorpus={CORPUS_NAME} window={WINDOW_MINUTES}min hours={SIMULATED_HOURS}"
        f"\nreleased={released} refused={refused} records={records} receipts={ledger_rows}"
    )

    # Every release is accounted: nothing escaped without a receipt.
    assert ledger_rows == released
    # The client was stopped in every window rather than running unbounded.
    assert refused == windows, "a greedy client must hit the ceiling in every window"
    # The per-window ceiling is the client-global hard limit of 1500 records.
    per_window = records / windows
    assert per_window <= 1500, per_window
