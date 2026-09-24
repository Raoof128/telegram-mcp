from whatsvault.approval import policy as P


def _ctx(**kw):
    base = {
        "recipient_wa_id": "61999",
        "kind": "text",
        "account_ok": True,
        "now_ms": 100,
        "expires_at_ms": 1000,
        "device_active": True,
        "rate_ok": True,
        "recipient_is_group": False,
        "window_open": True,
    }
    base.update(kw)
    return base


def test_clean_context_passes():
    assert P.evaluate(_ctx(), phase="prepare").ok


def test_free_form_needs_open_window_at_send():
    r = P.evaluate(_ctx(window_open=False), phase="send")
    assert not r.ok and "P2_WINDOW_CLOSED" in r.failed


def test_template_sends_outside_window():
    assert P.evaluate(_ctx(kind="template", window_open=False), phase="send").ok


def test_expired_fails_p4():
    assert "P4_EXPIRED" in P.evaluate(_ctx(now_ms=2000), phase="send").failed


def test_revoked_device_fails_p5():
    assert "P5_DEVICE_INACTIVE" in P.evaluate(_ctx(device_active=False), phase="send").failed


def test_group_recipient_fails_p7():
    assert "P7_GROUP_RECIPIENT" in P.evaluate(_ctx(recipient_is_group=True), phase="prepare").failed


def test_missing_recipient_fails_p1():
    assert "P1_RECIPIENT_UNBOUND" in P.evaluate(_ctx(recipient_wa_id=None), phase="prepare").failed
