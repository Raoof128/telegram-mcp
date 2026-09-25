"""comms v0.3 Task D9: client-bound ctx_ handles (A30, G13)."""

import pytest
from comms.core.security import bump_security_epoch

from comms.core.errors import CommsError
from comms.core.groups import GroupError, destination_of
from comms.core.objects import resolve_object
from tests.services.handle_fixtures import CLIENT, OTHER, open_handle, world


@pytest.fixture
def env(tmp_path):
    return world(tmp_path)


def test_a_handle_resumes_for_its_client(env):
    ref = open_handle(env)
    assert ref.startswith("ctx_")
    handle = env["handles"].resume(CLIENT, ref)
    assert (handle.target_ref, handle.actor, handle.snapshot) == (
        "grp_" + "g" * 26,
        "telegram_user",
        {"kind": "recent"},
    )


def test_handle_bound_to_client_other_client_refused(env):
    ref = open_handle(env)
    with pytest.raises(CommsError) as stale:
        env["handles"].resume(OTHER, ref)
    assert stale.value.code == "STALE_HANDLE"


def test_expired_handle_stale_handle_error(env):
    ref = open_handle(env)
    env["clock"].advance(hours=1, seconds=1)
    with pytest.raises(CommsError) as stale:
        env["handles"].resume(CLIENT, ref)
    assert stale.value.code == "STALE_HANDLE"


def test_epoch_bump_invalidates_handles_and_cursors(env):
    ref = open_handle(env)
    token = env["handles"].cursor(CLIENT, ref, {"offset": 50})
    bump_security_epoch(env["conn"])
    for call in (
        lambda: env["handles"].resume(CLIENT, ref),
        lambda: env["handles"].position(CLIENT, token),
    ):
        with pytest.raises(CommsError) as stale:
            call()
        assert stale.value.code == "STALE_HANDLE"


@pytest.mark.parametrize("value", ["ctx_" + "z" * 26, "not-a-ref", "", "grp_" + "g" * 26])
def test_unknown_or_foreign_refs_are_stale_or_invalid(env, value):
    with pytest.raises(CommsError) as refused:
        env["handles"].resume(CLIENT, value)
    assert refused.value.code in ("STALE_HANDLE", "INVALID_ARGUMENT")


def test_handle_or_cursor_is_not_a_write_capability(env):
    ref = open_handle(env)
    token = env["handles"].cursor(CLIENT, ref, {"offset": 1})
    for value in (ref, token, token.split(".")[0]):
        with pytest.raises(CommsError) as refused:
            resolve_object(env["conn"], value, "message")
        assert refused.value.code == "INVALID_ARGUMENT"
        with pytest.raises(GroupError):  # never a group either
            destination_of(env["conn"], value)
