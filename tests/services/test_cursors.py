"""comms v0.3 Task D9: MACed, key-versioned cur_ cursors (A30, G13; B7's promise)."""

import pytest

from comms.core.errors import CommsError
from tests.services.handle_fixtures import CLIENT, OTHER, open_handle, rotate_cursor_key, world


@pytest.fixture
def env(tmp_path):
    return world(tmp_path)


def test_a_cursor_round_trips_to_its_position(env):
    ref = open_handle(env)
    token = env["handles"].cursor(CLIENT, ref, {"offset": 50, "anchor": "cmg_" + "a" * 26})
    ref_part, mac = token.split(".")
    assert ref_part.startswith("cur_") and len(mac) == 32 and set(mac) <= set("0123456789abcdef")
    handle, position = env["handles"].position(CLIENT, token)
    assert handle.ref == ref and position == {"offset": 50, "anchor": "cmg_" + "a" * 26}


def test_cursor_key_rotation_invalidates_every_cursor(env):
    tokens = [env["handles"].cursor(CLIENT, open_handle(env), {"offset": n}) for n in range(3)]
    rotate_cursor_key(env)
    for token in tokens:
        with pytest.raises(CommsError) as stale:
            env["handles"].position(CLIENT, token)
        assert stale.value.code == "STALE_HANDLE"
    assert env["conn"].execute("SELECT count(*) FROM cursors").fetchone()[0] == 0
    fresh = env["handles"].cursor(CLIENT, open_handle(env), {"offset": 9})
    assert env["handles"].position(CLIENT, fresh)[1] == {"offset": 9}  # the new key works


@pytest.mark.parametrize("tamper", ["mac", "ref", "strip", "case"])
def test_tampered_cursor_mac_refused(env, tamper):
    token = env["handles"].cursor(CLIENT, open_handle(env), {"offset": 1})
    ref, mac = token.split(".")
    bad = {
        "mac": f"{ref}.{'0' * 32}",
        "ref": f"cur_{'z' * 26}.{mac}",
        "strip": ref,
        "case": f"{ref}.{mac.upper()}",
    }[tamper]
    with pytest.raises(CommsError) as refused:
        env["handles"].position(CLIENT, bad)
    assert refused.value.code in ("STALE_HANDLE", "INVALID_ARGUMENT")


def test_a_cursor_is_bound_to_its_client_and_expires(env):
    token = env["handles"].cursor(CLIENT, open_handle(env), {"offset": 1})
    with pytest.raises(CommsError) as stale:
        env["handles"].position(OTHER, token)
    assert stale.value.code == "STALE_HANDLE"
    env["clock"].advance(hours=1, seconds=1)
    with pytest.raises(CommsError) as stale:
        env["handles"].position(CLIENT, token)
    assert stale.value.code == "STALE_HANDLE"


def test_a_cursor_is_only_minted_for_its_handles_client(env):
    ref = open_handle(env)
    with pytest.raises(CommsError) as stale:
        env["handles"].cursor(OTHER, ref, {"offset": 1})
    assert stale.value.code == "STALE_HANDLE"
