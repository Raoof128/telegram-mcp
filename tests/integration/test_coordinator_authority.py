"""CoordinatorAuthority against live rows (design §2.3)."""

import pytest

from comms.transports.telegram.disclosure.coordinator import AuthorityRefusal
from comms.transports.telegram.disclosure.measure import bytes_disclosed
from comms.transports.telegram.disclosure.seams import CoordinatorAuthority
from comms.transports.telegram.keys.store import load_key, provision_missing
from comms.transports.telegram.runtime.identity import resolve_principal
from comms.transports.telegram.storage.db import bind_cursor_store, open_db
from tests.authority_fixtures import PROJECT_REF, seed_authority_rows

CLIENT = "tcl_" + "a" * 26


@pytest.fixture
def world(tmp_path):
    provision_missing(tmp_path / "keys", phases=(2, 3))
    conn = open_db(tmp_path / "meta.db")
    seed_authority_rows(conn)
    conn.execute(
        "INSERT INTO client_projects (client_id, project_id, can_read, can_cross_search,"
        " egress_level, excerpt_max_codepoints, created_at, updated_at)"
        " VALUES (1, 1, 1, 0, 'full_text', NULL, 'now', 'now')"
    )
    conn.commit()
    authority = CoordinatorAuthority(
        conn,
        privacy_key=load_key("privacy-key"),
        cursor_key=load_key("cursor-key"),
        cursor_store=bind_cursor_store(conn),
        runtime_id=b"\x05" * 16,
    )
    return conn, authority, resolve_principal(conn, CLIENT)


def _snapshot(authority, principal, tool="telegram_list_projects", args=None):
    request = authority.freeze_arguments(tool, args or {"limit": 20}, principal=principal)
    return request, authority.snapshot(tool, request)


def test_freeze_is_keyed_and_deterministic(world):
    _conn, authority, principal = world
    one = authority.freeze_arguments("telegram_list_projects", {"limit": 20}, principal=principal)
    two = authority.freeze_arguments("telegram_list_projects", {"limit": 20}, principal=principal)
    other = authority.freeze_arguments("telegram_list_projects", {"limit": 21}, principal=principal)
    assert one.canonical_request_hmac == two.canonical_request_hmac
    assert one.canonical_request_hmac != other.canonical_request_hmac
    with pytest.raises(TypeError):
        one.validated_args["limit"] = 50  # frozen


def test_snapshot_sees_only_readable_enabled_projects(world):
    conn, authority, principal = world
    _request, snap = _snapshot(authority, principal)
    assert [v.project_ref for v in snap.visible] == [PROJECT_REF]
    assert snap.project_count == 0 and snap.egress_level == "metadata_only"
    assert snap.project_scope_digest == "hmac-sha256:" + snap.scope_hex
    conn.execute("UPDATE client_projects SET can_read = 0")
    conn.commit()
    _request, snap = _snapshot(authority, principal)
    assert snap.visible == ()


def test_locked_unconfigured_and_revoked_refuse_before_consent(world):
    conn, authority, principal = world
    conn.execute("UPDATE security_state SET locked = 1")
    conn.commit()
    with pytest.raises(AuthorityRefusal) as locked:
        _snapshot(authority, principal)
    assert locked.value.code == "SECURITY_LOCKED"
    conn.execute("UPDATE security_state SET locked = 0")
    conn.execute("UPDATE mcp_clients SET enabled = 0")
    conn.commit()
    with pytest.raises(AuthorityRefusal) as revoked:
        _snapshot(authority, principal)
    assert revoked.value.code == "CLIENT_REVOKED"
    from dataclasses import replace

    unconfigured = replace(principal, account_id=None, account_ref=None)
    request = authority.freeze_arguments(
        "telegram_list_projects", {"limit": 20}, principal=unconfigured
    )
    with pytest.raises(AuthorityRefusal) as none:
        authority.snapshot("telegram_list_projects", request)
    assert none.value.code == "POLICY_UNCONFIGURED"


def test_the_estimate_bounds_both_catalogue_payloads(world):
    _conn, authority, principal = world
    _request, snap = _snapshot(authority, principal)
    (usage,) = authority.worst_case_buckets("telegram_list_projects", snap).values()
    listing = {
        "projects": [
            {
                "project_ref": PROJECT_REF,
                "display_name": "Alpha",
                "egress_level": "full_text",
                "can_cross_search": False,
                "excerpt_max_codepoints": None,
            }
        ]
    }
    resolving = {
        "matches": [dict(listing["projects"][0], match_kind="substring_display_name")],
        "ambiguous": False,
    }
    assert usage.records >= 1
    assert usage.bytes >= bytes_disclosed(listing)
    assert usage.bytes >= bytes_disclosed(resolving)


def test_revalidation_reports_each_moved_dimension(world):
    conn, authority, principal = world
    _request, snap = _snapshot(authority, principal)
    assert authority.revalidate(snap) is None
    conn.execute("UPDATE client_projects SET egress_level = 'metadata_only'")
    conn.commit()
    assert authority.revalidate(snap) == "POLICY_CHANGED"
    conn.execute("UPDATE mcp_clients SET enabled = 0")
    conn.commit()
    assert authority.revalidate(snap) == "CLIENT_REVOKED"
    conn.execute("UPDATE security_state SET security_epoch = 2")
    conn.commit()
    assert authority.revalidate(snap) == "SECURITY_LOCKED"


def test_a_list_cursor_is_checked_before_consent(world):
    conn, authority, principal = world
    _request, snap = _snapshot(authority, principal)
    cursor = authority.mint_catalogue_cursor(snap, {"limit": 20}, 1)
    _request, resumed = _snapshot(authority, principal, args={"limit": 20, "cursor": cursor})
    assert resumed.page == 1
    conn.execute("UPDATE client_projects SET egress_level = 'metadata_only'")
    conn.commit()
    with pytest.raises(AuthorityRefusal) as moved:
        _snapshot(authority, principal, args={"limit": 20, "cursor": cursor})
    assert moved.value.code == "CURSOR_PROJECT_CHANGED"
