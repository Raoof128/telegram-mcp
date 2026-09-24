from whatsvault.ingest import dedupe as D


def test_message_key_is_stable_and_hex():
    k = D.message_key("meta", "pn1", "wamid.A")
    assert k == D.message_key("meta", "pn1", "wamid.A")
    assert len(k) == 64 and all(c in "0123456789abcdef" for c in k)


def test_status_ranks_of_same_wamid_do_not_collide():
    base = ("meta", "pn1", "wamid.A")
    keys = {D.status_key(*base, s, ts, "r") for s, ts in [("sent", 1), ("delivered", 2), ("read", 3)]}
    assert len(keys) == 3


def test_status_and_message_families_do_not_collide():
    assert D.message_key("meta", "pn1", "wamid.A") != D.status_key("meta", "pn1", "wamid.A", "sent", 1, "r")


def test_missing_recipient_is_handled():
    k = D.status_key("meta", "pn1", "wamid.A", "sent", 1, None)
    assert len(k) == 64
    assert k != D.status_key("meta", "pn1", "wamid.A", "sent", 1, "r")
