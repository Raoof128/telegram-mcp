# 5x-B Backup / Disaster Recovery — FROZEN DEFERRED DECISION (DD3, ledger #58)

**Status:** UNRESOLVED. No backup/DR code will be written until this fork is decided.

## The fork
If the Mac **and** its Keychain are lost, should the vault be recoverable?

### Option A — No recovery (permanent loss)
- **Statement to ship:** "Loss of the Mac and its Keychain permanently destroys the vault."
- Simple, very strong custody: the SQLCipher keys never leave the Keychain; there is no second copy to compromise.
- Cost: total data loss on device failure. Acceptable only if the vault is treated as a convenience cache, not a system of record.

### Option B — Recoverable
Requires a real design fork, NOT a backup script:
- a recovery-key design (key wrapping of the vault/control keys under a recovery secret);
- offline storage of the recovery secret (paper/hardware);
- restore tests against SQLCipher + WAL consistency;
- a compromise analysis (the recovery secret is now a second key-exfiltration target);
- probably a second trusted factor.

## Decision needed from Raouf
Pick A or B. Until then, 5x-B is not started, and the operational system (5x-A) runs under
the **implicit Option A** posture — no backups exist, so a device loss is currently total.
This document is the standing reminder that the current posture is a *default*, not a *decision*.

## Cross-refs
Master roadmap Deferred-Decision Register DD3; Corrections Ledger #58 (INV-ATREST).
