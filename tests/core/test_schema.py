"""comms 5b-4 Task 3: schema v1 constraints, each violated once (design §2, G14–G16, G25).

sqlcipher3 raises its own IntegrityError, not sqlite3's (M6).
"""

import pytest
import sqlcipher3

from tests.core import schema_fixtures as fx

Integrity = sqlcipher3.dbapi2.IntegrityError


@pytest.fixture
def conn(tmp_path):
    return fx.migrated(tmp_path)


@pytest.fixture
def w(conn):
    return fx.world(conn)


def _refused(conn, sql, params=()):
    with pytest.raises(Integrity):
        conn.execute(sql, params)


def test_foreign_key_check_is_empty_after_migrate(conn, w):
    assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1


@pytest.mark.parametrize(
    ("sql", "params"),
    [
        ("UPDATE delivery_jobs SET state = 'SENT' WHERE id = 1", ()),
        ("UPDATE campaigns SET lifecycle = 'DONE' WHERE id = 1", ()),
        ("UPDATE campaigns SET summary = 'GOOD' WHERE id = 1", ()),
        ("UPDATE locations SET enabled = 2 WHERE id = 1", ()),
        ("UPDATE recipients SET enabled = -1 WHERE id = 1", ()),
        ("UPDATE contact_points SET enabled = 3 WHERE id = 1", ()),
        ("INSERT INTO delivery_identities (transport, identity) VALUES ('sms', 'x')", ()),
        ("UPDATE delivery_attempts SET outcome = 'MAYBE' WHERE id = 1", ()),
        ("UPDATE delivery_jobs SET attempt_count = -1 WHERE id = 1", ()),
    ],
)
def test_every_state_check_refuses(conn, w, sql, params):
    fx.attempt(conn, w["job"])
    _refused(conn, sql, params)


def test_generation_status_and_provider_status_checks(conn, w):
    _refused(
        conn,
        "INSERT INTO generations (ref, campaign_id, created_at, send_at, content,"
        " snapshot_digest, status) VALUES ('gen_x', ?, ?, ?, '{}', ?, 'paused')",
        (w["campaign"], fx.T0, fx.T0, "0" * 64),
    )
    _refused(
        conn,
        "INSERT INTO provider_events (transport, provider_event_ref, provider_message_ref,"
        " reported_status, disposition, received_at) VALUES ('whatsapp','e','m','READ','pending_match',?)",
        (fx.T0,),
    )


def test_audience_member_exactly_one_and_no_self_membership(conn):
    conn.execute("INSERT INTO audiences (ref, name, created_at) VALUES ('cau_a', 'A', ?)", (fx.T0,))
    loc, _ = fx.location(conn)
    rid, _ = fx.recipient(conn)
    _refused(conn, "INSERT INTO audience_members (audience_id) VALUES (1)")
    _refused(
        conn,
        "INSERT INTO audience_members (audience_id, member_location_id, member_recipient_id)"
        " VALUES (1, ?, ?)",
        (loc, rid),
    )
    _refused(conn, "INSERT INTO audience_members (audience_id, member_audience_id) VALUES (1, 1)")
    conn.execute(
        "INSERT INTO audience_members (audience_id, member_location_id) VALUES (1, ?)", (loc,)
    )
    _refused(
        conn, "INSERT INTO audience_members (audience_id, member_location_id) VALUES (1, ?)", (loc,)
    )


def test_delivery_identity_is_unique_per_transport(conn):
    fx.identity(conn, "telegram", "12345")
    fx.identity(conn, "whatsapp", "12345")
    _refused(
        conn, "INSERT INTO delivery_identities (transport, identity) VALUES ('telegram', '12345')"
    )


def test_one_enabled_endpoint_of_each_kind_per_identity(conn):
    loc, _ = fx.location(conn)
    r1, _ = fx.recipient(conn)
    r2, _ = fx.recipient(conn)
    ident = fx.identity(conn, "telegram", "12345")
    fx.destination(conn, loc, ident)
    fx.contact_point(conn, r1, ident, "telegram")  # one of each kind may share it (G2)
    with pytest.raises(Integrity):
        fx.destination(conn, loc, ident)
    with pytest.raises(Integrity):
        fx.contact_point(conn, r2, ident, "telegram")
    fx.destination(conn, loc, ident, enabled=0)  # disabled duplicates are allowed (F2)
    fx.contact_point(conn, r2, ident, "telegram", enabled=0)


def test_disable_old_create_new_moves_an_identity_to_another_recipient(conn):
    r1, _ = fx.recipient(conn)
    r2, _ = fx.recipient(conn)
    ident = fx.identity(conn, "whatsapp", "+61400000001")
    old, _ = fx.contact_point(conn, r1, ident, "whatsapp")
    conn.execute("UPDATE contact_points SET enabled = 0 WHERE id = ?", (old,))
    fx.contact_point(conn, r2, ident, "whatsapp")


def test_one_enabled_contact_point_per_recipient_and_transport(conn):
    rid, _ = fx.recipient(conn)
    first = fx.identity(conn, "whatsapp", "+61400000001")
    second = fx.identity(conn, "whatsapp", "+61400000002")
    old, _ = fx.contact_point(conn, rid, first, "whatsapp")
    with pytest.raises(Integrity):
        fx.contact_point(conn, rid, second, "whatsapp")
    conn.execute("UPDATE contact_points SET enabled = 0 WHERE id = ?", (old,))
    fx.contact_point(conn, rid, second, "whatsapp")


def test_endpoint_and_job_transport_must_match_the_identity(conn, w):
    loc, _ = fx.location(conn)
    wa = fx.identity(conn, "whatsapp", "+61400000009")
    rid, _ = fx.recipient(conn)
    with pytest.raises(Integrity, match="endpoint transport mismatch"):
        fx.destination(conn, loc, wa)
    with pytest.raises(Integrity, match="endpoint transport mismatch"):
        fx.contact_point(conn, rid, wa, "telegram")
    with pytest.raises(Integrity, match="job transport mismatch"):
        fx.job(conn, w["generation"], wa, "telegram")


def test_an_origin_must_resolve_to_its_jobs_identity(conn, w):
    rid, _ = fx.recipient(conn)
    other = fx.identity(conn, "whatsapp", "+61400000002")
    _, stranger = fx.contact_point(conn, rid, other, "whatsapp")
    with pytest.raises(Integrity, match="origin endpoint does not match its job"):
        fx.origin(conn, w["job"], stranger)
    with pytest.raises(Integrity, match="origin endpoint does not match its job"):
        fx.origin(conn, w["job"], "rct_" + "a" * 26)


def test_an_origin_path_must_end_at_its_endpoint(conn, w):
    with pytest.raises(Integrity):
        fx.origin(conn, w["job"], w["cp_ref"], ("cau_" + "a" * 26, "rcp_" + "b" * 26))
    with pytest.raises(Integrity):
        conn.execute(
            "INSERT INTO job_origins (job_id, endpoint_ref, path) VALUES (?, ?, 'not json')",
            (w["job"], w["cp_ref"]),
        )
    with pytest.raises(Integrity):
        conn.execute(
            "INSERT INTO job_origins (job_id, endpoint_ref, path) VALUES (?, ?, '[]')",
            (w["job"], w["cp_ref"]),
        )
    fx.origin(conn, w["job"], w["cp_ref"], ("cau_" + "a" * 26, w["cp_ref"]))


@pytest.mark.parametrize(
    ("payload", "digest", "skip", "state"),
    [
        (b"p", None, None, "PENDING"),  # payload without digest
        (
            None,
            "0" * 64,
            "platform_ineligible",
            "SKIPPED_PLATFORM_POLICY",
        ),  # digest without payload
        (b"p", "0" * 64, "platform_ineligible", "PENDING"),  # payload and a skip reason
        (None, None, None, "SKIPPED_PLATFORM_POLICY"),  # skipped without a reason
        (None, None, "platform_ineligible", "PENDING"),  # no payload but not skipped
        (b"p", "0" * 63 + "G", None, "PENDING"),  # malformed digest
        (b"p", "0" * 10, None, "PENDING"),  # short digest
    ],
)
def test_payload_digest_and_skip_reason_agree(conn, w, payload, digest, skip, state):
    _refused(
        conn,
        "INSERT INTO delivery_jobs (ref, generation_id, transport, identity_id,"
        " idempotency_key, payload, payload_digest, skip_reason, state) VALUES (?,?,?,?,?,?,?,?,?)",
        (
            "djb_" + "c" * 26,
            w["generation"],
            "whatsapp",
            fx.identity(conn, "whatsapp", "+619"),
            "e" * 64,
            payload,
            digest,
            skip,
            state,
        ),
    )


def test_current_generation_belongs_to_its_campaign_and_is_active(conn, w):
    other, _ = fx.campaign(conn)
    foreign, _ = fx.generation(conn, other, current=False)
    with pytest.raises(Integrity, match="another campaign"):
        conn.execute(
            "UPDATE campaigns SET current_generation_id = ? WHERE id = ?", (foreign, w["campaign"])
        )
    discarded, _ = fx.generation(conn, w["campaign"], status="discarded", current=False)
    with pytest.raises(Integrity, match="another campaign"):
        conn.execute(
            "UPDATE campaigns SET current_generation_id = ? WHERE id = ?",
            (discarded, w["campaign"]),
        )
    _refused(
        conn,
        "INSERT INTO campaigns (ref, title, lifecycle, content, targets, options,"
        " current_generation_id, summary, created_at, updated_at) VALUES"
        " ('cmp_x','T','DRAFT','{}','{}','{}',?, 'IN_PROGRESS', ?, ?)",
        (w["generation"], fx.T0, fx.T0),
    )


def test_summary_and_current_generation_are_null_together(conn, w):
    _refused(conn, "UPDATE campaigns SET summary = NULL WHERE id = ?", (w["campaign"],))
    _refused(
        conn, "UPDATE campaigns SET current_generation_id = NULL WHERE id = ?", (w["campaign"],)
    )
    conn.execute(
        "UPDATE campaigns SET current_generation_id = NULL, summary = NULL WHERE id = ?",
        (w["campaign"],),
    )


def test_unique_job_attempt_and_provider_event(conn, w):
    with pytest.raises(Integrity):
        fx.job(conn, w["generation"], w["identity"], "whatsapp")
    fx.attempt(conn, w["job"], 1)
    with pytest.raises(Integrity):
        fx.attempt(conn, w["job"], 1)
    insert = (
        "INSERT INTO provider_events (transport, provider_event_ref, provider_message_ref,"
        " reported_status, disposition, received_at) VALUES (?, 'evt-1', 'm', 'DELIVERED', 'pending_match', ?)"
    )
    conn.execute(insert, ("whatsapp", fx.T0))
    conn.execute(insert, ("telegram", fx.T0))
    _refused(conn, insert, ("whatsapp", fx.T0))


def test_provider_event_disposition_and_attempt_agree(conn, w):
    aid, _ = fx.attempt(conn, w["job"])
    base = (
        "INSERT INTO provider_events (transport, provider_event_ref, provider_message_ref, attempt_id,"
        " reported_status, disposition, received_at) VALUES ('whatsapp', ?, 'm', ?, 'DELIVERED', ?, ?)"
    )
    _refused(conn, base, ("a", aid, "pending_match", fx.T0))
    _refused(conn, base, ("b", None, "applied", fx.T0))
    _refused(conn, base, ("c", None, "recorded", fx.T0))
    conn.execute(base, ("d", None, "refused", fx.T0))


def test_on_delete_restrict(conn, w):
    with pytest.raises(Integrity, match="never deleted"):
        conn.execute("DELETE FROM contact_points WHERE id = ?", (w["contact_point"],))
    loc, _ = fx.location(conn)
    fx.destination(conn, loc, fx.identity(conn, "telegram", "7"))
    with pytest.raises(Integrity, match="never deleted"):
        conn.execute("DELETE FROM destinations")
    _refused(conn, "DELETE FROM delivery_identities WHERE id = ?", (w["identity"],))
    _refused(conn, "DELETE FROM generations WHERE id = ?", (w["generation"],))
    _refused(conn, "DELETE FROM recipients WHERE id = ?", (w["recipient"],))


@pytest.mark.parametrize(
    ("sql", "message"),
    [
        (
            "UPDATE contact_points SET platform_identity = 'new'",
            "contact point identity is immutable",
        ),
        (
            "UPDATE contact_points SET recipient_id = recipient_id",
            "contact point identity is immutable",
        ),
        ("UPDATE contact_points SET ref = 'rct_x'", "contact point identity is immutable"),
        ("UPDATE delivery_identities SET identity = 'x'", "delivery identity is immutable"),
        ("UPDATE generations SET send_at = 'x'", "generation is frozen"),
        ("UPDATE generations SET snapshot_digest = 'x'", "generation is frozen"),
        ("UPDATE delivery_jobs SET payload = x'00'", "job binding is frozen"),
        ("UPDATE delivery_jobs SET idempotency_key = 'x'", "job binding is frozen"),
        ("UPDATE delivery_jobs SET identity_id = identity_id", "job binding is frozen"),
        ("UPDATE job_origins SET path = path", "origins are frozen"),
        ("DELETE FROM job_origins", "origins are frozen"),
        ("UPDATE delivery_attempts SET attempt_no = 2", "attempt binding is frozen"),
    ],
)
def test_immutability_triggers(conn, w, sql, message):
    fx.attempt(conn, w["job"])
    with pytest.raises(Integrity, match=message):
        conn.execute(sql)


def test_destination_identity_is_immutable(conn):
    loc, _ = fx.location(conn)
    other, _ = fx.location(conn)
    fx.destination(conn, loc, fx.identity(conn, "telegram", "1"))
    for sql in (
        "UPDATE destinations SET platform_identity = 'x'",
        f"UPDATE destinations SET location_id = {other}",
    ):
        with pytest.raises(Integrity, match="destination identity is immutable"):
            conn.execute(sql)
    conn.execute("UPDATE destinations SET enabled = 0, display_name = 'renamed'")


def test_generation_status_is_one_way(conn, w):
    conn.execute("UPDATE campaigns SET current_generation_id = NULL, summary = NULL")
    conn.execute("UPDATE generations SET status = 'discarded'")
    with pytest.raises(Integrity, match="generation is frozen"):
        conn.execute("UPDATE generations SET status = 'active'")


def test_provider_reference_is_set_once(conn, w):
    aid, _ = fx.attempt(conn, w["job"])
    conn.execute("UPDATE delivery_attempts SET provider_message_ref = 'm1' WHERE id = ?", (aid,))
    with pytest.raises(Integrity, match="provider reference is frozen"):
        conn.execute(
            "UPDATE delivery_attempts SET provider_message_ref = 'm2' WHERE id = ?", (aid,)
        )
    _refused(conn, "UPDATE delivery_attempts SET finished_at = ? WHERE id = ?", (fx.T0, aid))


def test_only_a_pending_provider_event_resolves_and_nothing_is_deleted(conn, w):
    aid, _ = fx.attempt(conn, w["job"])
    conn.execute(
        "INSERT INTO provider_events (transport, provider_event_ref, provider_message_ref,"
        " reported_status, disposition, received_at) VALUES ('whatsapp','e','m','DELIVERED','pending_match',?)",
        (fx.T0,),
    )
    with pytest.raises(Integrity, match="provider event is frozen"):
        conn.execute("UPDATE provider_events SET reported_status = 'ACCEPTED'")
    conn.execute(
        "UPDATE provider_events SET disposition = 'applied', job_id = ?, attempt_id = ?",
        (w["job"], aid),
    )
    with pytest.raises(Integrity, match="provider event is frozen"):
        conn.execute("UPDATE provider_events SET disposition = 'refused'")
    with pytest.raises(Integrity, match="provider event is frozen"):
        conn.execute("DELETE FROM provider_events")


def test_event_log_is_append_only(conn):
    conn.execute(
        "INSERT INTO campaign_events (event_ref, event_type, ts, payload) VALUES ('cev_a','x',?,'{}')",
        (fx.T0,),
    )
    with pytest.raises(Integrity, match="append-only"):
        conn.execute("UPDATE campaign_events SET payload = '[]'")
    with pytest.raises(Integrity, match="append-only"):
        conn.execute("DELETE FROM campaign_events")


@pytest.mark.parametrize(
    ("sql", "index"),
    [
        ("SELECT path FROM job_origins WHERE job_id = 1", "job_origins_job"),
        ("SELECT id FROM destinations WHERE location_id = 1", "destinations_location"),
        ("SELECT id FROM generations WHERE campaign_id = 1", "generations_campaign"),
        ("SELECT id FROM contact_points WHERE recipient_id = 1", "contact_points_recipient"),
        (
            "SELECT location_id FROM location_members WHERE recipient_id = 1",
            "location_members_recipient",
        ),
        (
            (
                "SELECT id FROM provider_events WHERE transport = 'whatsapp'"
                " AND provider_message_ref = 'm' AND disposition = 'pending_match'"
            ),
            "provider_events_pending",
        ),
        (
            "SELECT id FROM delivery_attempts WHERE provider_message_ref = 'm'",
            "delivery_attempts_provider_ref",
        ),
    ],
)
def test_hot_path_queries_use_indexes(conn, sql, index):
    plan = " ".join(row[-1] for row in conn.execute("EXPLAIN QUERY PLAN " + sql))
    assert f"INDEX {index}" in plan, plan
