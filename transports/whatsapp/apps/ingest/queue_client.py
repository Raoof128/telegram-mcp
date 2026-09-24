"""Queue client Protocol + FakeQueue for tests. The real CloudflarePullConsumer is
Phase-0-gated (Task 6). A message not ACKed (and not DLQ'd) is redelivered."""

from dataclasses import dataclass


@dataclass
class LeasedMsg:
    lease_id: str
    body: bytes


class FakeQueue:
    def __init__(self, messages=None):
        self._pending = list(messages or [])
        self._leased = {}
        self._acked = []
        self._counter = 0

    def add(self, body):
        self._pending.append(body)

    def lease(self, max_messages=32):
        out = []
        while self._pending and len(out) < max_messages:
            self._counter += 1
            lid = f"L{self._counter}"
            body = self._pending.pop(0)
            self._leased[lid] = body
            out.append(LeasedMsg(lid, body))
        return out

    def ack(self, lease_ids):
        for lid in lease_ids:
            body = self._leased.pop(lid, None)
            if body is not None:
                self._acked.append(body)

    def nack(self, lease_ids):  # redeliver: back to pending
        for lid in lease_ids:
            body = self._leased.pop(lid, None)
            if body is not None:
                self._pending.append(body)
