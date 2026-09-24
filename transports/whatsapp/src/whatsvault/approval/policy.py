"""Shared P1-P7 send policy (spec §5.6, ledger #11).

THE single source of send policy. Both drafts.prepare and sender.execute_write import
and call evaluate(); the sender re-evaluates inside the permission-to-transmit
transaction and is authoritative. Reason codes are stable so callers and tests can
assert on specific failures."""

from dataclasses import dataclass, field


@dataclass
class PolicyResult:
    ok: bool
    failed: list = field(default_factory=list)


def evaluate(ctx: dict, *, phase: str) -> PolicyResult:
    failed = []
    if not ctx.get("recipient_wa_id"):
        failed.append("P1_RECIPIENT_UNBOUND")
    if ctx.get("kind", "text") == "text" and not ctx.get("window_open"):
        failed.append("P2_WINDOW_CLOSED")  # free-form text needs an open 24h window
    if not ctx.get("account_ok", True):
        failed.append("P3_ACCOUNT_BINDING")
    now, exp = ctx.get("now_ms"), ctx.get("expires_at_ms")
    if now is not None and exp is not None and now >= exp:
        failed.append("P4_EXPIRED")
    if not ctx.get("device_active", True):
        failed.append("P5_DEVICE_INACTIVE")
    if not ctx.get("rate_ok", True):
        failed.append("P6_RATE_LIMIT")
    if ctx.get("recipient_is_group"):
        failed.append("P7_GROUP_RECIPIENT")
    return PolicyResult(ok=(len(failed) == 0), failed=failed)
