"""Approval-triggered dispatcher (spec §6/§11, Phase 4 Task 7) — NOT YET OPERABLE.

Design: a valid approval envelope wakes this dispatcher, which drives
`whatsvault-meta` to transmit. The model is never in the dispatch path, and there
is no dispatch verb anywhere in the MCP or CLI surface.

It cannot run yet: `whatsvault-meta` (the one process permitted to hold the Meta
token) is not built, and live Meta is blocked behind Phase-0 Gates 1 and 2. This
module therefore starts, reports the count of approval envelopes waiting to be
re-driven, names its blocker, and exits cleanly. It deliberately contains NO
transmit path — acquiring one here by accident would put send authority in a
daemon instead of behind the phone's Secure Enclave signature.
"""

import sys

from whatsvault.ops import recovery, structlog

BLOCKED_ON = "whatsvault_meta_daemon"
DETAIL = (
    "whatsvault-meta is not built and live Meta is Phase-0-gated (Gates 1-2); "
    "approval envelopes are surfaced for an operator, never auto-dispatched"
)


def run(vault_conn, control_conn, now_ms) -> dict:
    startup = recovery.run_startup(vault_conn, control_conn, now_ms)
    return structlog.event(
        {
            "service": "dispatcher",
            "status": "not_started",
            "blocked_on": BLOCKED_ON,
            "detail": DETAIL,
            "pending_approvals": startup["pending_approvals"],
            "submitting_recovered": startup["submitting_recovered"],
        }
    )


def main():  # pragma: no cover - process entrypoint
    import json
    import time

    from whatsvault.ops import daemon

    vault_conn, control_conn, blocked = daemon.open_databases("dispatcher")
    if blocked is not None:
        print(json.dumps(blocked))
        return 0
    print(json.dumps(run(vault_conn, control_conn, int(time.time() * 1000))))
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
