from whatsvault.ingest import normalise as NM

# PROVISIONAL fixture (real shapes confirmed at Phase 0 V4/V7).
WEBHOOK = {
    "entry": [
        {
            "changes": [
                {
                    "value": {
                        "metadata": {"phone_number_id": "PN1"},
                        "contacts": [{"wa_id": "61999", "profile": {"name": "Mona"}}],
                        "messages": [
                            {
                                "from": "61999",
                                "id": "wamid.M1",
                                "timestamp": "1700000000",
                                "type": "text",
                                "text": {"body": "hi"},
                            }
                        ],
                        "statuses": [
                            {
                                "id": "wamid.M0",
                                "status": "delivered",
                                "timestamp": "1700000001",
                                "recipient_id": "61999",
                            },
                            {
                                "id": "wamid.M0",
                                "status": "read",
                                "timestamp": "1700000002",
                                "recipient_id": "61999",
                            },
                        ],
                    }
                }
            ]
        }
    ]
}


def test_split_produces_three_atomics():
    atoms = NM.split_webhook(WEBHOOK)
    assert sorted(a["kind"] for a in atoms) == ["message", "status", "status"]


def test_inbound_window_eligible_and_seconds_to_ms():
    m = next(a for a in NM.split_webhook(WEBHOOK) if a["kind"] == "message")
    assert NM.classify(m) == "MESSAGE_INBOUND"
    r = NM.to_rows(m)
    assert r["message"]["window_eligible"] == 1 and r["message"]["origin"] == "cloud_api"
    assert r["message"]["ts_lower_ms"] == 1700000000 * 1000 and r["message"]["direction"] == "in"
    assert r["contact"]["name"] == "Mona"


def test_echo_not_window_eligible():
    m = {
        "from": "61999",
        "id": "wamid.E",
        "timestamp": "1700000000",
        "type": "text",
        "text": {"body": "yo"},
        "_wv_echo": True,
    }
    atom = NM.split_webhook(
        {"entry": [{"changes": [{"value": {"metadata": {"phone_number_id": "PN1"}, "messages": [m]}}]}]}
    )[0]
    assert NM.classify(atom) == "MESSAGE_ECHO"
    r = NM.to_rows(atom)
    assert r["message"]["window_eligible"] == 0 and r["message"]["origin"] == "business_app_echo"
    assert r["message"]["direction"] == "out"


def test_status_row_no_message_fk():
    st = next(a for a in NM.split_webhook(WEBHOOK) if a["kind"] == "status")
    r = NM.to_rows(st)
    assert r["family"] == "MESSAGE_STATUS" and "message" not in r
    assert r["status"]["wamid"] == "wamid.M0" and r["status"]["provider_ts_ms"] == 1700000001 * 1000


def test_unknown_value_no_domain_row():
    atoms = NM.split_webhook(
        {
            "entry": [
                {"changes": [{"value": {"metadata": {"phone_number_id": "PN1"}, "weird_field": {"x": 1}}}]}
            ]
        }
    )
    assert len(atoms) == 1 and NM.classify(atoms[0]) == "UNKNOWN_SUPPORTED"
    r = NM.to_rows(atoms[0])
    assert "message" not in r and "status" not in r


def test_semantic_keys_family_tagged_and_distinct():
    atoms = NM.split_webhook(WEBHOOK)
    m = next(a for a in atoms if a["kind"] == "message")
    fam, key = NM.semantic_key(m)
    assert fam == "MESSAGE_INBOUND" and len(key) == 64
    sts = [a for a in atoms if a["kind"] == "status"]
    assert NM.semantic_key(sts[0])[1] != NM.semantic_key(sts[1])[1]  # delivered vs read
