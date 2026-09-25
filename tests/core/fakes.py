"""Scriptable fake transports and test seams for the campaign core (comms 5b-4 Task 6).

Tests only. ``script`` maps a delivery identity to behaviours: ``deliver`` pops
outcome behaviours in order (default ``accept``); ``ineligible`` and
``("window_closes_at", t)`` are standing properties read by ``prepare`` and
``still_valid``.
"""

from __future__ import annotations

import hashlib
import itertools
import json
from collections.abc import Callable
from datetime import datetime
from typing import Any

from comms.core import timeutil
from comms.core.canonical import jcs_dumps
from comms.core.delivery.transport import (
    DeliveryIntent,
    DeliveryResult,
    FrozenDelivery,
    PreparedPayload,
    ResultKind,
    Skip,
    SkipReason,
)
from tests.core import schema_fixtures as fx

OUTCOMES = {
    "accept",
    "deliver",
    "transient_unsent",
    "permanent",
    "raise",
    "unknown",
    "crash_after_accept",
}
_planted = itertools.count()


class SimulatedCrash(BaseException):
    """The process dies here. BaseException, so no ``except Exception`` can swallow it."""


class _Fake:
    name = ""
    credential = "FAKE-TRANSPORT-CREDENTIAL-7f3a"

    def __init__(
        self,
        conn: Any = None,
        script: dict[str, list[Any]] | None = None,
        on_deliver: Callable[[FrozenDelivery], None] | None = None,
    ) -> None:
        self.conn = conn
        self.script = {k: list(v) for k, v in (script or {}).items()}
        self.on_deliver = on_deliver
        self.calls: list[tuple[str, str, bool]] = []
        self.sent: list[str] = []
        self.io_calls: list[tuple[str, bool]] = []
        self._messages = itertools.count(1)

    def _inside(self) -> bool:
        return bool(self.conn is not None and self.conn.in_transaction)

    def _standing(self, identity: str) -> list[Any]:
        return [b for b in self.script.get(identity, []) if b not in OUTCOMES]

    def _normalize(self, raw: str) -> str:
        raise NotImplementedError

    def normalize(self, platform_identity: str) -> str:
        return self._normalize(platform_identity)

    def prepare(self, intent: DeliveryIntent, send_at: datetime) -> PreparedPayload | Skip:
        self.calls.append(("prepare", intent.identity, self._inside()))
        if "ineligible" in self._standing(intent.identity):
            return Skip(SkipReason.PLATFORM_INELIGIBLE)
        data = jcs_dumps(
            {
                "content": dict(intent.content),
                "send_at": timeutil.iso(send_at),
                "to": intent.identity,
                "transport": self.name,
            }
        )
        return PreparedPayload(data=data, digest=hashlib.sha256(data).hexdigest())

    def still_valid(self, payload: PreparedPayload, now: datetime) -> bool | str:
        identity = json.loads(payload.data)["to"]
        self.calls.append(("still_valid", identity, self._inside()))
        for behaviour in self._standing(identity):
            closes = isinstance(behaviour, tuple) and behaviour[0] == "window_closes_at"
            if closes and timeutil.utc(now) >= timeutil.utc(behaviour[1]):
                return False
        return True

    def deliver(self, delivery: FrozenDelivery) -> DeliveryResult:
        identity = delivery.identity
        self.calls.append(("deliver", identity, self._inside()))
        queue = self.script.get(identity, [])
        index = next((i for i, b in enumerate(queue) if b in OUTCOMES), None)
        behaviour = queue.pop(index) if index is not None else "accept"
        message = f"{self.name}-msg-{next(self._messages)}"
        if behaviour == "raise":
            raise RuntimeError("fake adapter failure")
        if behaviour in ("transient_unsent", "permanent"):
            kind = (
                ResultKind.FAILED_TRANSIENT
                if behaviour == "transient_unsent"
                else ResultKind.FAILED_PERMANENT
            )
            return DeliveryResult(kind)
        self.sent.append(identity)
        if behaviour == "crash_after_accept":
            raise SimulatedCrash
        if self.on_deliver is not None:
            self.on_deliver(delivery)
        if behaviour == "unknown":
            return DeliveryResult(ResultKind.OUTCOME_UNKNOWN)
        kind = ResultKind.DELIVERED if behaviour == "deliver" else ResultKind.ACCEPTED
        return DeliveryResult(kind, provider_message_ref=message)


class FakeTelegram(_Fake):
    name = "telegram"

    def _normalize(self, raw: str) -> str:
        return fx.tg(raw)


class KeyedTelegram(FakeTelegram):
    """A telegram fake that names a provider request key, as the MTProto transport does."""

    actor = "telegram_user"

    def __init__(self, conn, key_of=lambda idem: "k-" + idem[:16], **kw):
        super().__init__(conn=conn, **kw)
        self.key_of = key_of
        self.seen_keys = []

    def provider_request_key(self, idempotency_key):
        return self.key_of(idempotency_key)

    def deliver(self, delivery):
        row = self.conn.execute(
            "SELECT a.provider_request_key, a.transport_actor FROM delivery_attempts a"
            " JOIN delivery_jobs j ON j.id = a.job_id WHERE j.ref = ? ORDER BY a.attempt_no DESC",
            (delivery.job_ref,),
        ).fetchone()
        self.seen_keys.append(tuple(row))  # what was durable when the call was made
        return super().deliver(delivery)


class FakeWhatsApp(_Fake):
    name = "whatsapp"

    def _normalize(self, raw: str) -> str:
        return fx.wa(raw)


class ImpureFakeWhatsApp(FakeWhatsApp):
    """Violates S3 on purpose: its still_valid touches an I/O hook, which is recorded."""

    def still_valid(self, payload: PreparedPayload, now: datetime) -> bool | str:
        self.io_calls.append(("still_valid", self._inside()))
        return super().still_valid(payload, now)


class FakeLock:
    def __init__(self) -> None:
        self._held = True

    def held(self) -> bool:
        return self._held

    def release(self) -> None:
        self._held = False


def plant_failure(conn: Any, table: str, op: str, *, when: str = "BEFORE") -> str:
    """A TEMP trigger that aborts ``op`` on ``main.table`` (M9): a mid-transaction failure
    through a real dependency. ``op`` is INSERT, DELETE, UPDATE or ``UPDATE OF column``."""
    name = f"planted_{next(_planted)}"
    conn.execute(
        f"CREATE TEMP TRIGGER {name} {when} {op} ON main.{table} "
        "BEGIN SELECT RAISE(ABORT, 'planted'); END"
    )
    return name


def clear_planted(conn: Any) -> None:
    for (name,) in conn.execute(
        "SELECT name FROM sqlite_temp_master WHERE type = 'trigger'"
    ).fetchall():
        conn.execute(f"DROP TRIGGER temp.{name}")
