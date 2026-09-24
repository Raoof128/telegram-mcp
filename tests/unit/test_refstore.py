import pytest

from comms.transports.telegram.storage.db import open_db
from comms.transports.telegram.storage.refstore import RefStore
from tests.authority_fixtures import seed_authority_rows


@pytest.fixture
def refs(tmp_path):
    conn = open_db(tmp_path / "m.db")
    seed_authority_rows(conn)
    return RefStore(conn, account_id=1)


def test_a_peer_is_minted_once_and_its_caches_refresh(refs):
    first = refs.ensure_peer("user", 42, display_name="Ali", username="ali")
    again = refs.ensure_peer("user", 42, display_name="Ali R.", username=None)
    assert first.peer_ref == again.peer_ref and first.peer_ref.startswith("tgp_")
    assert refs.peer_by_ref(first.peer_ref).display_name == "Ali R."
    assert refs.peer_by_identity("user:42").identity == "user:42"
    assert refs.peer_by_ref("tgp_" + "z" * 26) is None


def test_message_refs_are_stable_per_peer_and_id(refs):
    peer = refs.ensure_peer("channel", 7, display_name="News", username=None)
    ref = refs.message_ref(peer.row_id, 1001)
    assert refs.message_ref(peer.row_id, 1001) == ref and ref.startswith("tgm_")
    assert refs.message_by_ref(ref) == (peer.row_id, 1001)
    assert refs.message_ref(peer.row_id, 1002) != ref


def test_unknown_peer_types_are_refused(refs):
    with pytest.raises(ValueError):
        refs.ensure_peer("secret", 1, display_name=None, username=None)
