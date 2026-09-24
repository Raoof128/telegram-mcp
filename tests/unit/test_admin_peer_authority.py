"""Admin authority is the socket's peer credentials alone (comms spec v0.2; 5b-3 design §3)."""

import inspect

from comms.transports.telegram.ipc import admin
from comms.transports.telegram.ipc.admin import AdminRouter, serve_admin


def _router(seen):
    def lock(args):
        seen.append(args)
        return {"locked": True}

    return AdminRouter({"lock": lock})


def test_a_mutating_command_runs_with_no_proof():
    seen: list = []
    assert _router(seen).dispatch({"cmd": "lock", "args": {}}) == {
        "ok": True,
        "data": {"locked": True},
    }
    assert seen == [{}]


def test_presence_argument_is_rejected():
    seen: list = []
    response = _router(seen).dispatch({"cmd": "lock", "args": {"presence": {}}})
    assert (response["ok"], response["code"]) == (False, "MALFORMED_REQUEST")
    assert seen == [], "the handler never sees a presence argument"


def test_no_presence_machinery_remains():
    assert not hasattr(admin, "PRESENCE_GATED")
    assert set(inspect.signature(AdminRouter).parameters) == {"handlers", "control_handlers"}
    assert "approver" not in inspect.signature(serve_admin).parameters
