"""D39-PRE E10a: every request Telethon sends on its own is reviewed and pinned (A21).

The adapter's recorder sees only what passes through ``_call_reviewed``. Telethon itself sends
requests from ``connect``, ``_on_login``, ``get_me``, ``is_user_authorized``, the update loop and
the message box. This test reads those functions in the installed Telethon and requires their
request set to equal ``UPDATE_RPCS``: a Telethon upgrade that adds one fails here first.
"""

import ast
import pathlib

import telethon

from comms.transports.telegram.telegram.telethon_adapter import (
    ADMIN_RPCS,
    READ_RPCS,
    UPDATE_RPCS,
    WRITE_RPCS,
)

ROOT = pathlib.Path(telethon.__file__).parent
SELF_SENT = {
    "client/telegrambaseclient.py": {"connect"},
    "client/auth.py": {"_on_login"},
    "client/users.py": {"get_me", "is_user_authorized"},
    "client/updates.py": {"set_receive_updates", "_update_loop", "_keepalive_loop"},
    "_updates/messagebox.py": None,  # the whole module: it builds the difference requests
}
WRAPPERS = {"InvokeWithLayerRequest", "InvokeWithoutUpdatesRequest", "InitConnectionRequest"}


def _requests():
    found = set()
    for rel, names in SELF_SENT.items():
        tree = ast.parse((ROOT / rel).read_text(encoding="utf-8"))
        for fn in ast.walk(tree):
            if isinstance(fn, ast.FunctionDef | ast.AsyncFunctionDef) and (
                names is None or fn.name in names
            ):
                for node in ast.walk(fn):
                    if isinstance(node, ast.Attribute) and node.attr.endswith("Request"):
                        if node.attr in WRAPPERS:
                            continue
                        if isinstance(node.value, ast.Attribute):
                            found.add(f"{node.value.attr}.{node.attr}")
                        else:
                            found.add(node.attr)
    return found


def test_the_telethon_version_is_the_reviewed_one():
    assert telethon.__version__ == "1.45.0"


def test_telethon_self_sent_requests_are_exactly_the_reviewed_set():
    assert _requests() == UPDATE_RPCS


def test_no_self_sent_request_writes():
    writes = {r for rpcs in (*WRITE_RPCS.values(), *ADMIN_RPCS.values()) for r in rpcs}
    assert not UPDATE_RPCS & writes
    reads = {r for rpcs in READ_RPCS.values() for r in rpcs}
    assert all(r.split(".")[0] in {"help", "users", "updates"} for r in UPDATE_RPCS - reads)
