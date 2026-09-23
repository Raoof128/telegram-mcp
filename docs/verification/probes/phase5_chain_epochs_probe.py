"""Probe: can verify_chain accept (a) a second chain epoch, (b) a truncated prefix?"""

import sqlite3

from telegram_mcp.disclosure.audit import chain as C
from telegram_mcp.storage.migrations import migrate

conn = sqlite3.connect(":memory:", isolation_level=None)
conn.execute("PRAGMA foreign_keys=ON")
migrate(conn)
key = b"k" * 32


def ev(name="admin.lock"):
    e = {c: None for c in C._EVENT_COLUMNS}
    e.update(event_id=C.mint_event_id(), ts="2026-09-24T00:00:00Z", tool_name=name, status="ok")
    return e


for _ in range(3):
    with C.immediate_transaction(conn):
        C.append_event(conn, key, ev())
C.verify_chain(conn, key)
print("epoch1 x3: verifies")

# (a) open epoch 2 by hand, exactly as a sealed-checkpoint + new-epoch rotation would
e = ev("admin.key_rotation")
mac = C.event_mac(key, chain_epoch=2, chain_seq=1, prev_event_mac=C.genesis_mac(2), event=e)
cols = ", ".join((*C._EVENT_COLUMNS, "chain_epoch", "chain_seq", "prev_event_mac", "event_mac"))
conn.execute(
    f"INSERT INTO audit_events ({cols}) VALUES ({', '.join('?' * (len(C._EVENT_COLUMNS) + 4))})",
    (*(e[c] for c in C._EVENT_COLUMNS), 2, 1, C.genesis_mac(2), mac),
)
try:
    C.verify_chain(conn, key)
    print("(a) epoch 2: verifies")
except C.ChainError as x:
    print("(a) epoch 2 REJECTED:", x)
conn.execute("DELETE FROM audit_events WHERE chain_epoch = 2")

# (b) truncate the prefix before seq 2
conn.execute("DELETE FROM audit_events WHERE chain_epoch = 1 AND chain_seq = 1")
try:
    C.verify_chain(conn, key)
    print("(b) truncated: verifies")
except C.ChainError as x:
    print("(b) truncated REJECTED:", x)
