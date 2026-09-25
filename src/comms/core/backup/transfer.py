"""Backup transfer frames (comms v0.3 Task B26): bounded, peer-bound, in order, SHA-checked.

A backup crosses the admin socket as 32 KiB chunks, well inside the unchanged 64 KiB frame
codec. A transfer lives only in daemon memory, is bound to the admin peer credential that
began it, expires after five minutes, is capped at 4 MiB, and must arrive complete, in
order, and matching its announced SHA-256. Nothing is written to disk here.
"""

from __future__ import annotations

import hashlib
import secrets
import time
from collections.abc import Callable, Hashable
from dataclasses import dataclass, field

__all__ = ["CAP", "CHUNK", "TTL_S", "PullTransfer", "TransferError", "TransferRegistry"]

CHUNK = 32 * 1024
CAP = 4 * 1024 * 1024
TTL_S = 300.0


class TransferError(Exception):
    """A transfer step was refused. Fixed messages."""


@dataclass
class _Transfer:
    peer: Hashable
    direction: str
    size: int
    sha256: str
    expires: float
    data: bytearray = field(default_factory=bytearray)
    next_index: int = 0


@dataclass(frozen=True)
class PullTransfer:
    transfer_id: str
    size: int
    sha256: str
    chunks: int


class TransferRegistry:
    def __init__(self, *, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._transfers: dict[str, _Transfer] = {}

    def _new(self, transfer: _Transfer) -> str:
        transfer_id = secrets.token_urlsafe(18)
        self._transfers[transfer_id] = transfer
        return transfer_id

    def _get(self, transfer_id: str, peer: Hashable, direction: str) -> _Transfer:
        transfer = self._transfers.get(transfer_id)
        if transfer is None or transfer.direction != direction:
            raise TransferError("unknown transfer")
        if self._clock() >= transfer.expires:
            del self._transfers[transfer_id]
            raise TransferError("transfer expired")
        if transfer.peer != peer:
            raise TransferError("transfer belongs to another peer")
        return transfer

    def begin_push(self, peer: Hashable, *, size: int, sha256: str) -> str:
        if not isinstance(size, int) or not 0 <= size <= CAP:
            raise TransferError("transfer is over the cap")
        return self._new(_Transfer(peer, "push", size, sha256, self._clock() + TTL_S))

    def push(self, transfer_id: str, peer: Hashable, index: int, chunk: bytes) -> None:
        transfer = self._get(transfer_id, peer, "push")
        if index != transfer.next_index:
            raise TransferError("chunks must arrive in order")
        if len(chunk) > CHUNK:
            raise TransferError("chunk is too large")
        if len(transfer.data) + len(chunk) > transfer.size:
            raise TransferError("transfer exceeds its announced size")
        transfer.data += chunk
        transfer.next_index += 1

    def complete(self, transfer_id: str, peer: Hashable) -> bytes:
        transfer = self._get(transfer_id, peer, "push")
        if len(transfer.data) != transfer.size:
            raise TransferError("transfer is incomplete")
        del self._transfers[transfer_id]
        data = bytes(transfer.data)
        if hashlib.sha256(data).hexdigest() != transfer.sha256:
            raise TransferError("transfer sha256 does not match")
        return data

    def begin_pull(self, peer: Hashable, data: bytes) -> PullTransfer:
        if len(data) > CAP:
            raise TransferError("transfer is over the cap")
        digest = hashlib.sha256(data).hexdigest()
        transfer_id = self._new(
            _Transfer(peer, "pull", len(data), digest, self._clock() + TTL_S, bytearray(data))
        )
        return PullTransfer(transfer_id, len(data), digest, max(1, -(-len(data) // CHUNK)))

    def pull(self, transfer_id: str, peer: Hashable, index: int) -> bytes:
        transfer = self._get(transfer_id, peer, "pull")
        if index != transfer.next_index:
            raise TransferError("chunks must be pulled in order")
        transfer.next_index += 1
        chunk = bytes(transfer.data[index * CHUNK : (index + 1) * CHUNK])
        if (index + 1) * CHUNK >= transfer.size:
            del self._transfers[transfer_id]  # the last chunk ends the transfer
        return chunk
