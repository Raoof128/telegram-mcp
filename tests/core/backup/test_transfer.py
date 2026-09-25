"""comms v0.3 Task B26: bounded, peer-bound, in-order 32 KiB transfer frames for backups."""

import hashlib
import os

import pytest

from comms.core.backup.transfer import CAP, CHUNK, TTL_S, TransferError, TransferRegistry

PEER, OTHER = (501, 20), (502, 20)


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


@pytest.fixture
def reg():
    clock = Clock()
    return TransferRegistry(clock=clock), clock


def _chunks(data):
    return [data[i : i + CHUNK] for i in range(0, len(data), CHUNK)]


def _push_all(registry, data, peer=PEER):
    tid = registry.begin_push(peer, size=len(data), sha256=hashlib.sha256(data).hexdigest())
    for index, chunk in enumerate(_chunks(data)):
        registry.push(tid, peer, index, chunk)
    return tid


def test_round_trip_in_order_chunks(reg):
    registry, _ = reg
    data = os.urandom(3 * CHUNK + 17)
    tid = _push_all(registry, data)
    assert registry.complete(tid, PEER) == data
    pulled = registry.begin_pull(PEER, data)
    assert pulled.size == len(data) and pulled.sha256 == hashlib.sha256(data).hexdigest()
    assert (
        b"".join(registry.pull(pulled.transfer_id, PEER, i) for i in range(pulled.chunks)) == data
    )
    with pytest.raises(TransferError):
        registry.complete(tid, PEER)  # a completed transfer is gone


def test_out_of_order_refused(reg):
    registry, _ = reg
    data = os.urandom(2 * CHUNK)
    tid = registry.begin_push(PEER, size=len(data), sha256=hashlib.sha256(data).hexdigest())
    with pytest.raises(TransferError, match="order"):
        registry.push(tid, PEER, 1, data[CHUNK:])
    pulled = registry.begin_pull(PEER, data)
    with pytest.raises(TransferError, match="order"):
        registry.pull(pulled.transfer_id, PEER, 1)


def test_expired_transfer_refused(reg):
    registry, clock = reg
    data = os.urandom(10)
    tid = registry.begin_push(PEER, size=10, sha256=hashlib.sha256(data).hexdigest())
    clock.now += TTL_S + 1
    with pytest.raises(TransferError, match="expired"):
        registry.push(tid, PEER, 0, data)


def test_wrong_peer_refused(reg):
    registry, _ = reg
    data = os.urandom(10)
    tid = registry.begin_push(PEER, size=10, sha256=hashlib.sha256(data).hexdigest())
    with pytest.raises(TransferError, match="peer"):
        registry.push(tid, OTHER, 0, data)
    registry.push(tid, PEER, 0, data)
    with pytest.raises(TransferError, match="peer"):
        registry.complete(tid, OTHER)


def test_over_cap_refused(reg):
    registry, _ = reg
    with pytest.raises(TransferError, match="cap"):
        registry.begin_push(PEER, size=CAP + 1, sha256="0" * 64)
    with pytest.raises(TransferError, match="cap"):
        registry.begin_pull(PEER, b"x" * (CAP + 1))
    tid = registry.begin_push(PEER, size=10, sha256="0" * 64)
    with pytest.raises(TransferError, match="size"):
        registry.push(tid, PEER, 0, b"x" * 11)
    with pytest.raises(TransferError, match="chunk"):
        registry.push(
            registry.begin_push(PEER, size=CAP, sha256="0" * 64), PEER, 0, b"x" * (CHUNK + 1)
        )


def test_sha_mismatch_refused(reg):
    registry, _ = reg
    data = os.urandom(100)
    tid = registry.begin_push(PEER, size=100, sha256="0" * 64)
    registry.push(tid, PEER, 0, data)
    with pytest.raises(TransferError, match="sha"):
        registry.complete(tid, PEER)


def test_an_incomplete_push_cannot_complete(reg):
    registry, _ = reg
    data = os.urandom(2 * CHUNK)
    tid = registry.begin_push(PEER, size=len(data), sha256=hashlib.sha256(data).hexdigest())
    registry.push(tid, PEER, 0, data[:CHUNK])
    with pytest.raises(TransferError, match="incomplete"):
        registry.complete(tid, PEER)
