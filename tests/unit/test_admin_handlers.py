"""Project, grant and scope handlers (design §2.5): retired in comms v0.3, kept for history.

They are driven through the historical surface; production answers RETIRED_IN_V0_3.
"""

import pytest

from comms.transports.telegram.ipc.admin import LEGACY_ADMIN_SURFACE, AdminRouter
from comms.transports.telegram.ipc.handlers.leases import auth_headers_handler
from comms.transports.telegram.ipc.handlers.projects import project_handlers
from comms.transports.telegram.ipc.leases import verify_lease
from comms.transports.telegram.storage.db import open_db
from tests.authority_fixtures import seed_authority_rows

CLIENT = "tcl_" + "a" * 26


@pytest.fixture
def router(tmp_path):
    conn = open_db(tmp_path / "meta.db")
    seed_authority_rows(conn)
    conn.execute("DELETE FROM projects")
    conn.commit()
    return conn, AdminRouter(project_handlers(conn), surface=LEGACY_ADMIN_SURFACE)


def _call(router, cmd, **args):
    return router.dispatch({"cmd": cmd, "args": args})


def test_create_grant_and_list(router):
    _conn, admin = router
    created = _call(admin, "project create", slug="ops", display_name="Ops")
    ref = created["data"]["project_ref"]
    granted = _call(
        admin,
        "project grant-client",
        project_ref=ref,
        client_ref=CLIENT,
        egress_level="excerpt",
        excerpt_max_codepoints=200,
    )
    assert granted["ok"], granted
    listed = admin.dispatch({"cmd": "project list", "args": {}})
    assert listed["data"]["projects"][0]["project_ref"] == ref


def test_enable_and_disable_bump_the_project_epoch(router):
    conn, admin = router
    ref = _call(admin, "project create", slug="ops", display_name="Ops")["data"]["project_ref"]
    _call(admin, "project disable", project_ref=ref)
    _call(admin, "project enable", project_ref=ref)
    epoch = conn.execute(
        "SELECT project_epoch FROM projects WHERE project_ref = ?", (ref,)
    ).fetchone()[0]
    assert epoch == 3


def test_invalid_input_is_refused_without_writing(router):
    conn, admin = router
    ref = _call(admin, "project create", slug="ops", display_name="Ops")["data"]["project_ref"]
    before = conn.total_changes
    for cmd, args in (
        ("project create", {"slug": "Bad Slug", "display_name": "x"}),
        ("project create", {"slug": "ok", "display_name": "line\nbreak"}),
        (
            "project grant-client",
            {"project_ref": ref, "client_ref": CLIENT, "egress_level": "excerpt"},
        ),
        (
            "project set-egress",
            {"project_ref": ref, "client_ref": CLIENT, "egress_level": "full_text"},
        ),
        ("scope mode", {"mode": "everything"}),
    ):
        assert _call(admin, cmd, **args)["code"] == "MALFORMED_REQUEST", (cmd, args)
    assert conn.total_changes == before


def test_scope_mode_bumps_the_policy_epoch(router):
    conn, admin = router
    assert _call(admin, "scope mode", mode="all_cloud_chats")["ok"]
    assert conn.execute("SELECT mode, policy_epoch FROM policy_state").fetchone() == (
        "all_cloud_chats",
        2,
    )


def test_auth_headers_mints_a_verifiable_lease(router):
    conn, _admin = router
    seed = b"\x09" * 32
    handler = auth_headers_handler(
        conn,
        seed_for=lambda ref: seed if ref == CLIENT else None,
        runtime_id=b"\x05" * 16,
        clock=lambda: 1_000_000,
    )
    header = handler({"client_ref": CLIENT})["authorization"]
    assert header.startswith("Bearer tgml1.")
    claims = verify_lease(
        header.removeprefix("Bearer "),
        seeds={CLIENT: seed},
        epoch=1,
        now=1_000_000,
        runtime_id=b"\x05" * 16,
    )
    assert claims.client == CLIENT
    with pytest.raises(PermissionError):
        handler({"client_ref": "tcl_" + "z" * 26})


@pytest.mark.parametrize(
    "name",
    [
        "Ops\u202e",
        "\u2067Ops\u2069",
        "Ops\u200e",
        "Ops\u200f",
        "Ops\u061c",
        "Ops\u2028x",
        "Ops\u2029x",
    ],
)
def test_prompt_unsafe_names_are_refused(router, name):
    _conn, admin = router
    assert (
        _call(admin, "project create", slug="ops", display_name=name)["code"] == "MALFORMED_REQUEST"
    )


def test_persian_names_with_zwnj_are_accepted(router):
    _conn, admin = router
    assert _call(admin, "project create", slug="fa", display_name="انجمن\u200cها")["ok"]
