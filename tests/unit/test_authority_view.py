# tests/unit/test_authority_view.py
"""SQLite rows -> AuthorityView (the one binding)."""

import pytest

from comms.transports.telegram.authority.policy import AuthorityRequest, Denial, evaluate
from comms.transports.telegram.storage.authority_view import (
    grant_digest,
    load_security,
    load_view,
    owner_account,
    project_labels,
)
from comms.transports.telegram.storage.db import open_db
from tests.authority_fixtures import PROJECT_REF, seed_authority_rows

CLIENT = "tcl_" + "a" * 26


@pytest.fixture
def conn(tmp_path):
    connection = open_db(tmp_path / "meta.db")
    seed_authority_rows(connection)
    connection.execute(
        "INSERT INTO client_projects (client_id, project_id, can_read, can_cross_search,"
        " egress_level, excerpt_max_codepoints, created_at, updated_at)"
        " VALUES (1, 1, 1, 0, 'excerpt', 200, 'now', 'now')"
    )
    connection.commit()
    yield connection
    connection.close()


def test_the_view_mirrors_the_rows(conn):
    view = load_view(conn, principal_id=1, account_id=1)
    assert view.clients[CLIENT].enabled is True
    assert view.projects[PROJECT_REF].project_epoch == 1
    grant = view.grants[(CLIENT, PROJECT_REF)]
    assert (grant.egress_level, grant.excerpt_limit, grant.can_cross_search) == (
        "excerpt",
        200,
        False,
    )
    assert grant.grant_digest == grant_digest(True, False, "excerpt", 200)
    assert (view.policy_epoch, view.security_epoch) == (1, 1)


def test_a_disabled_client_is_denied_by_the_engine(conn):
    conn.execute("UPDATE mcp_clients SET enabled = 0")
    conn.commit()
    view = load_view(conn, principal_id=1, account_id=1)
    verdict = evaluate(view, AuthorityRequest("discover", CLIENT))
    assert isinstance(verdict, Denial) and verdict.code == "CLIENT_REVOKED"


def test_grant_digest_moves_with_every_grant_bit():
    base = grant_digest(True, False, "excerpt", 200)
    assert grant_digest(True, True, "excerpt", 200) != base
    assert grant_digest(True, False, "excerpt", 201) != base
    assert grant_digest(True, False, "full_text", None) != base


def test_security_labels_and_owner_account(conn):
    assert load_security(conn) == (1, False)
    assert project_labels(conn, account_id=1) == {PROJECT_REF: ("alpha", "Alpha")}
    assert owner_account(conn, principal_id=1) == 1
    assert owner_account(conn, principal_id=99) is None
