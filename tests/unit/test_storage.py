"""Task 7 Step 5: the Task-6 protocols bound to SQLite, and the settings registry."""

import sqlite3

import pytest

from comms.transports.telegram.authority.cursors import (
    CursorError,
    CursorPresenter,
    ProjectScopeEntry,
    check_cursor,
    mint_cursor,
)
from comms.transports.telegram.authority.epochs import (
    bump_policy_epoch,
    bump_project_epoch,
    set_locked,
)
from comms.transports.telegram.storage.db import (
    StorageError,
    bind_cursor_store,
    bind_epoch_state,
    open_db,
    save_epoch_state,
)
from comms.transports.telegram.storage.settings import (
    SETTINGS_REGISTRY,
    all_settings,
    get_setting,
    set_setting,
    validate_setting,
)

TS = "2026-09-22T00:00:00Z"
CURSOR_KEY = b"\x11" * 32
PRIVACY_KEY = b"\x22" * 32
RUNTIME_ID = b"\x33" * 16
PRINCIPAL = "prn_" + "a" * 26
CLIENT = "tcl_" + "b" * 26
ACCOUNT = "tga_" + "c" * 26
PROJECT = "tpr_" + "d" * 26


@pytest.fixture
def conn(tmp_path):
    connection = open_db(tmp_path / "db" / "meta.db")
    connection.execute(
        "INSERT INTO accounts(id, account_ref, telegram_user_id, created_at, updated_at)"
        " VALUES (1, ?, 1001, ?, ?)",
        (ACCOUNT, TS, TS),
    )
    connection.execute(
        "INSERT INTO principals(id, principal_ref, principal_key, auth_mode, created_at)"
        " VALUES (1, ?, 'k1', 'local', ?)",
        (PRINCIPAL, TS),
    )
    connection.execute(
        "INSERT INTO mcp_clients(id, principal_id, client_ref, auth_kind, auth_binding,"
        " client_kind, enabled, created_at)"
        " VALUES (1, 1, ?, 'bearer', 'b1', 'codex_local', 1, ?)",
        (CLIENT, TS),
    )
    connection.execute(
        "INSERT INTO policy_state(principal_id, account_id, mode, policy_epoch,"
        " include_archived, include_private, include_groups, include_channels, updated_at)"
        " VALUES (1, 1, 'allowlist', 4, 0, 1, 1, 1, ?)",
        (TS,),
    )
    connection.execute(
        "INSERT INTO projects(id, account_id, project_ref, slug, display_name, enabled,"
        " project_epoch, created_at, updated_at) VALUES (1, 1, ?, 's1', 'S1', 1, 1, ?, ?)",
        (PROJECT, TS, TS),
    )
    connection.commit()
    return connection


def _presenter(**over):
    kw = {
        "principal": PRINCIPAL,
        "client": CLIENT,
        "account": ACCOUNT,
        "tool": "telegram_get_messages",
        "request": {"peer_ref": "tgp_" + "f" * 26, "limit": 20},
        "policy_epoch": 4,
        "security_epoch": 1,
        "scope": (
            ProjectScopeEntry(
                project_ref=PROJECT,
                project_epoch=1,
                can_read=True,
                can_cross_search=False,
                egress_level="full_text",
                excerpt_limit=None,
            ),
        ),
    }
    kw.update(over)
    return CursorPresenter(**kw)


def test_cursor_round_trips_through_sqlite(conn):
    store = bind_cursor_store(conn)
    presenter = _presenter()
    ref = mint_cursor(
        store,
        cursor_key=CURSOR_KEY,
        privacy_key=PRIVACY_KEY,
        presenter=presenter,
        state={"offset_id": 42, "seen_ids": [1, 2, 3]},
        now=1_000_000.5,
        runtime_id=RUNTIME_ID,
    )
    record = check_cursor(
        store,
        cursor_key=CURSOR_KEY,
        privacy_key=PRIVACY_KEY,
        ref=ref,
        presenter=presenter,
        now=1_000_001.0,
        runtime_id=RUNTIME_ID,
    )
    assert record.state == {"offset_id": 42, "seen_ids": [1, 2, 3]}
    assert record.created_at == 1_000_000.5
    assert record.runtime_id == RUNTIME_ID.hex()
    # the row carries no plaintext query and no runtime column of its own
    stored = conn.execute("SELECT state_json, query_digest FROM cursors").fetchone()
    assert "invoice" not in stored[0]
    assert RUNTIME_ID.hex() in stored[0]
    assert len(stored[1]) == 64


def test_expired_cursor_row_is_deleted_by_the_check(conn):
    store = bind_cursor_store(conn)
    presenter = _presenter()
    ref = mint_cursor(
        store,
        cursor_key=CURSOR_KEY,
        privacy_key=PRIVACY_KEY,
        presenter=presenter,
        state={},
        now=1_000_000.0,
        runtime_id=RUNTIME_ID,
    )
    with pytest.raises(CursorError) as exc:
        check_cursor(
            store,
            cursor_key=CURSOR_KEY,
            privacy_key=PRIVACY_KEY,
            ref=ref,
            presenter=presenter,
            now=1_000_000.0 + 901,
            runtime_id=RUNTIME_ID,
        )
    assert exc.value.code == "CURSOR_EXPIRED"
    assert conn.execute("SELECT count(*) FROM cursors").fetchone()[0] == 0


def test_cursor_store_refuses_an_unknown_client_ref(conn):
    store = bind_cursor_store(conn)
    with pytest.raises(StorageError):
        mint_cursor(
            store,
            cursor_key=CURSOR_KEY,
            privacy_key=PRIVACY_KEY,
            presenter=_presenter(client="tcl_" + "z" * 26),
            state={},
            now=1_000_000.0,
            runtime_id=RUNTIME_ID,
        )


def test_epoch_state_round_trips_through_sqlite(conn):
    state = bind_epoch_state(conn)
    assert state["security_state"]["security_epoch"] == 1
    assert state["policy_state"][(PRINCIPAL, ACCOUNT)]["policy_epoch"] == 4
    assert state["projects"][PROJECT]["project_epoch"] == 1

    assert bump_policy_epoch(state) == 5
    assert bump_project_epoch(state, PROJECT) == 2
    assert set_locked(state, True) == 2
    save_epoch_state(conn, state)

    reloaded = bind_epoch_state(conn)
    assert reloaded["policy_state"][(PRINCIPAL, ACCOUNT)]["policy_epoch"] == 5
    assert reloaded["projects"][PROJECT]["project_epoch"] == 2
    assert reloaded["security_state"]["security_epoch"] == 2
    assert reloaded["security_state"]["locked"] == 1
    assert reloaded["security_state"]["locked_at"] is not None

    assert set_locked(reloaded, False, presence=True) == 3
    save_epoch_state(conn, reloaded)
    final = bind_epoch_state(conn)
    assert final["security_state"]["locked"] == 0
    assert final["security_state"]["locked_at"] is None


def test_bindings_refuse_a_connection_without_foreign_keys(tmp_path):
    raw = sqlite3.connect(tmp_path / "off.db")
    from comms.transports.telegram.storage.migrations import migrate

    migrate(raw)
    with pytest.raises(StorageError):
        bind_cursor_store(raw)
    with pytest.raises(StorageError):
        bind_epoch_state(raw)


# --- settings ---------------------------------------------------------------


def test_settings_defaults_match_the_spec_baseline(conn):
    assert get_setting(conn, "exposure_budget.rolling_window_minutes") == 30
    assert get_setting(conn, "exposure_budget.hard_records_per_client_project") == 500
    assert get_setting(conn, "exposure_budget.hard_bytes_per_client_global") == 15_000_000
    assert set(all_settings(conn)) == set(SETTINGS_REGISTRY)


def test_settings_round_trip_and_reject_unknown_keys(conn):
    assert set_setting(conn, "audit.checkpoint_cadence_events", 250) == 250
    assert get_setting(conn, "audit.checkpoint_cadence_events") == 250
    assert set_setting(conn, "audit.checkpoint_cadence_events", 300) == 300
    assert conn.execute("SELECT count(*) FROM settings").fetchone()[0] == 1
    with pytest.raises(ValueError):
        set_setting(conn, "telegram.last_query", "invoice")
    with pytest.raises(ValueError):
        get_setting(conn, "telegram.last_query")


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("exposure_budget.rolling_window_minutes", 0),
        ("exposure_budget.rolling_window_minutes", 1441),
        ("exposure_budget.rolling_window_minutes", True),
        ("exposure_budget.rolling_window_minutes", "30"),
        ("audit.checkpoint_cadence_seconds", 59),
        ("release.profile", "direct_https"),
        ("release.version", "0.1"),
        ("release.version", 1),
    ],
)
def test_typed_validators_reject_bad_values(key, value):
    with pytest.raises(ValueError):
        validate_setting(key, value)


def test_no_registry_key_can_carry_telegram_content():
    """Tie the guarantee to the type, not the spelling.

    An ``int`` row cannot hold prose whatever it is called, which is why
    ``retention.message_ref_days`` is safe. Every ``str`` row must be closed
    by ``choices`` or a ``pattern``, so no free-text value can be written
    through this module at all — a stronger claim than the name heuristic,
    which only ever caught careless naming.
    """
    forbidden = ("query", "message", "body", "caption", "username", "phone", "url", "text")
    for key, spec in SETTINGS_REGISTRY.items():
        if spec.kind == "str":
            assert not any(word in key.lower() for word in forbidden), key
            closed = spec.choices is not None or spec.pattern is not None
            assert closed or key == "release.version", key


def test_no_free_text_string_can_be_written(conn):
    prose = "the invoice thread with Dana"
    for key, spec in SETTINGS_REGISTRY.items():
        if spec.kind != "str":
            continue
        with pytest.raises(ValueError):
            set_setting(conn, key, prose)


def test_checkpoint_cadence_cannot_exceed_the_spec_26_5_bound():
    """Spec §26.5: a checkpoint at least every 500 events or 60 minutes.

    The registry bounds must make a non-compliant cadence unwritable; an
    operator with user presence must not be able to configure a cadence
    that violates the MUST.
    """
    assert validate_setting("audit.checkpoint_cadence_events", 500) == 500
    assert validate_setting("audit.checkpoint_cadence_seconds", 3_600) == 3_600
    with pytest.raises(ValueError):
        validate_setting("audit.checkpoint_cadence_events", 501)
    with pytest.raises(ValueError):
        validate_setting("audit.checkpoint_cadence_seconds", 3_601)


def test_phase_three_settings_rows_exist_with_the_spec_defaults(conn):
    assert get_setting(conn, "audit.integrity_degraded") == 0
    assert get_setting(conn, "audit.external_anchor_provider") == "file_0600"
    assert get_setting(conn, "retention.disclosure_receipt_days") == 180
    assert get_setting(conn, "retention.exposure_ledger_days") == 30
    assert get_setting(conn, "retention.audit_events_days") == 30
    assert get_setting(conn, "retention.audit_checkpoint_days") == 180
    assert get_setting(conn, "retention.verification_key_grace_days") == 30
    assert get_setting(conn, "retention.message_ref_days") == 180


def test_exposure_ledger_retention_cannot_be_shorter_than_the_window():
    # Spec §23C.1: retention MUST be at least the longest rolling window.
    # The window is capped at 1440 minutes, which is one day.
    with pytest.raises(ValueError):
        validate_setting("retention.exposure_ledger_days", 0)


def test_degraded_reason_is_a_closed_set():
    assert validate_setting("audit.degraded_reason", "anchor_refresh_failure")
    with pytest.raises(ValueError):
        validate_setting("audit.degraded_reason", "something happened")


def test_degraded_disclosure_ref_accepts_only_a_tdr_ref():
    assert validate_setting("audit.degraded_disclosure_ref", "tdr_" + "a" * 26)
    with pytest.raises(ValueError):
        validate_setting("audit.degraded_disclosure_ref", "the invoice thread")


def test_anchor_provider_is_a_closed_set():
    with pytest.raises(ValueError):
        validate_setting("audit.external_anchor_provider", "dropbox")
