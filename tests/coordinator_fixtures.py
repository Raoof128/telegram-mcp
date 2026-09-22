"""Seams the coordinator needs, faked exactly as Phase 2 faked its own.

``FakeAuthority`` and ``FakeConsent`` are the two boundaries Phase 4 replaces.
They decide nothing interesting: the point of the crash suite is the
coordinator's *sequencing*, so the seams answer predictably and the failures
under test come from the transaction, not from policy.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from tests.authority_fixtures import PROJECT_REF, seed_authority_rows

ACCOUNT_REF = "tga_" + "a" * 26
PRINCIPAL_REF = "prn_" + "a" * 26
CLIENT_REF = "tcl_" + "a" * 26


@dataclass(frozen=True)
class FrozenRequest:
    nonce: str


@dataclass(frozen=True)
class Snapshot:
    principal_id: int = 1
    client_id: int = 1
    account_id: int = 1
    principal_ref: str = PRINCIPAL_REF
    client_ref: str = CLIENT_REF
    account_ref: str = ACCOUNT_REF
    security_epoch: int = 1
    policy_epoch: int = 1
    project_scope_digest: str = "hmac-sha256:" + "0" * 64
    project_count: int = 1
    partial: bool = False


@dataclass(frozen=True)
class Approval:
    key_id: str = "p256:sha256:" + "1" * 64
    challenge_sha256: str = "2" * 64


class FakeAuthority:
    """Fixed snapshot; reports movement only when a test asks for it."""

    def __init__(self, *, moved: str | None = None, worst_case: Any = None) -> None:
        self._moved = moved
        self._worst_case = worst_case

    def freeze_arguments(self, tool_name: str, arguments: Any) -> FrozenRequest:
        return FrozenRequest(nonce="n" * 32)

    def snapshot(self, tool_name: str, request: FrozenRequest) -> Snapshot:
        return Snapshot()

    def worst_case_buckets(self, tool_name: str, snapshot: Snapshot) -> Any:
        return self._worst_case

    def revalidate(self, snapshot: Snapshot) -> str | None:
        return self._moved

    def apply_egress(self, raw: dict[str, Any], snapshot: Snapshot) -> dict[str, Any]:
        return raw


class FakeConsent:
    """Issues a challenge and approves it. Divergence is opt-in."""

    def __init__(self, *, approve: bool = True, diverge_times: int = 0) -> None:
        self._approve = approve
        self._diverge_times = diverge_times

    def issue(self, *, tool_name: str, snapshot: Snapshot) -> str:
        return "tgu_" + "a" * 26

    async def consume(self, challenge: str) -> Approval | None:
        return Approval() if self._approve else None

    def snapshot_matches(self, approval: Approval, worst_case: Any) -> bool:
        if self._diverge_times > 0:
            self._diverge_times -= 1
            return False
        return True


def build_coordinator(
    tmp_path,
    *,
    crash_at: str | None = None,
    transport: Any = None,
    records: list[dict[str, Any]] | None = None,
    moved: str | None = None,
    approve: bool = True,
    diverge_times: int = 0,
    anchor_dir_mode: int = 0o700,
):
    """Build a coordinator over a real database, ledger, chain and anchor."""
    from telegram_mcp.disclosure.budget import BudgetLedger, buckets_for
    from telegram_mcp.disclosure.coordinator import DisclosureCoordinator
    from telegram_mcp.keys.store import key_id, load_key, provision_missing
    from telegram_mcp.storage.db import open_db
    from telegram_mcp.storage.migrations import migrate

    provision_missing(tmp_path / "keys", phases=(2, 3))
    conn = open_db(tmp_path / "meta.db")
    migrate(conn)
    seed_authority_rows(conn)

    anchor_dir = tmp_path / "anchor"
    anchor_dir.mkdir(mode=anchor_dir_mode, exist_ok=True)

    payload_records = records or [
        {
            "message_ref": "tgm_" + c * 26,
            "origin_project_refs": [PROJECT_REF],
            "text": "hello",
            "text_truncated": False,
        }
        for c in "ab"
    ]
    data = {"project": {"project_ref": PROJECT_REF}, "messages": payload_records}

    class FakeAdapter:
        async def retrieve(self, *, tool_name: str, arguments: Any) -> dict[str, Any]:
            return {k: list(v) if isinstance(v, list) else v for k, v in data.items()}

    worst_case = {
        key: usage._replace() if hasattr(usage, "_replace") else usage
        for key, usage in buckets_for("telegram_get_messages", data, client_id=1).items()
    }
    # Reserve generously: the worst case must never be tighter than the actual.
    from telegram_mcp.disclosure.budget import Usage

    worst_case = {k: Usage(v.records + 5, v.bytes + 500) for k, v in worst_case.items()}

    coordinator = DisclosureCoordinator(
        conn,
        chain_key=load_key("audit-chain-key"),
        disclosure_seed=load_key("disclosure-key"),
        disclosure_key_id=key_id("disclosure-key"),
        anchor_path=anchor_dir / "anchor.json",
        ledger=BudgetLedger(conn),
        authority=FakeAuthority(moved=moved, worst_case=worst_case),
        consent=FakeConsent(approve=approve, diverge_times=diverge_times),
        transport=transport,
        crash_at=crash_at,
    )
    return coordinator, conn, FakeAdapter()
