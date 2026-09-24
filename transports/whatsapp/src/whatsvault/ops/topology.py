"""Process/credential topology (5x-A, ledger #43-adjacent). The invariant the whole
architecture rests on: exactly ONE process holds the Meta token, and NO process can
approve (approval authority is the phone's Secure Enclave signature only)."""

PROCESSES = [
    {"name": "whatsvault-meta", "holds_meta_token": True, "holds_vault_key": True, "can_approve": False},
    {"name": "mcp", "holds_meta_token": False, "holds_vault_key": True, "can_approve": False},
    {"name": "ingest", "holds_meta_token": False, "holds_vault_key": True, "can_approve": False},
    {"name": "scheduler", "holds_meta_token": False, "holds_vault_key": True, "can_approve": False},
    {"name": "dispatcher", "holds_meta_token": False, "holds_vault_key": True, "can_approve": False},
    {"name": "cli", "holds_meta_token": False, "holds_vault_key": True, "can_approve": False},
]


def check_invariants(processes=None) -> list:
    procs = processes if processes is not None else PROCESSES
    token_holders = [p["name"] for p in procs if p.get("holds_meta_token")]
    approvers = [p["name"] for p in procs if p.get("can_approve")]
    return [
        {
            "check": "single_meta_token_holder",
            "ok": len(token_holders) == 1,
            "detail": f"holders={token_holders}",
        },
        {
            "check": "meta_token_holder_is_daemon",
            "ok": token_holders == ["whatsvault-meta"],
            "detail": f"holders={token_holders}",
        },
        {"check": "no_process_can_approve", "ok": len(approvers) == 0, "detail": f"approvers={approvers}"},
    ]
