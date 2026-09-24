# Comms 5b-3 Design: Owner-Direct Authority (the constitutional amendment)

**Status:** Revision 1. The owner approved the design on 2026-09-24 with eight amendments and two gate additions (§0A).
**Date:** 2026-09-24 (Australia/Sydney)
**Parent:** [`comms-consolidation-design.md`](2026-09-24-comms-consolidation-design.md) rev 2, §4.1.
**Governs:** this is the one semantic phase. Its normative output is `docs/comms-spec-v0.2.md`, a clause-by-clause delta against spec v0.1.10 and WhatsVault's frozen design.

## Controlling statement

There is **no Touch ID and no consent ceremony anywhere.**

- **An owner command is the authorization.**
- **The human confirmation lives in the conversation.** Claude drafts, shows the exact message and recipients, and asks "look, is it good?". On the owner's yes, Claude runs the operator command, and Claude Code's always-ask rule makes that single Allow the interlock.
- **The consent subsystem is deleted, not emulated** (owner decision B).
- **Historical evidence stays verifiable:** nothing about v1 receipts, v1 audit history or retained key material becomes unreadable.

## 0. Owner decisions

| # | Decision |
|---|---|
| O1 | Option B: the consent step is deleted from the disclosure pipeline. |
| O2 | The consent subsystem is removed entirely: the Swift agent, RV-1, broker, prompter, display, gate, pairing, admin approver and summaries. |
| O3 | The send flow is: draft → preview → "look, is it good?" → owner yes → Claude Code Allow → `comms campaign send`. Remote AI clients have no send primitive. |

## 0A. Owner amendments (all adopted; each was checked against the code)

| # | Amendment | Where |
|---|---|---|
| A1 | An explicit signed version, not inference from nulls | §2.1 |
| A2 | The migration never rewrites v1 semantics; an upgrade fixture proves verify-before == verify-after with v1 bytes unchanged | §2.2 |
| A3 | "Retire" means inactive, not erased | §2.5 |
| A4 | JCS byte-level fixtures, as well as AST equivalence | §2.3 |
| A5 | `soft_threshold_exceeded` is pinned to the exact pre-existing decision point | §2.4 |
| A6 | Replacement formal invariants, not just subtraction | §2.6 |
| A7 | The "AI cannot transmit" claim is scoped precisely | §3 |
| A8 | The dormant WhatsVault sender is proven unreachable | §4 |
| G1 | The table rebuild preserves indexes, foreign keys, triggers, row counts and IDs | §2.2 |
| G2 | A pre/post test-collection manifest: removed, replaced, unexpectedly missing = 0 | §5 |

## 1. The pipeline without consent

The order becomes: freeze → authority snapshot → estimate → **consult and reserve** → retrieve → revalidate → egress → commit (receipt, ledger, audit in one transaction) → anchor → handoff.

These are unchanged: every barrier; reservation/commit atomicity; the append guard; anchor-before-handoff; the degraded latch; revalidation at step 8; and hard-threshold refusal before retrieval.

## 2. Evidence

### 2.1 Receipt v2, explicitly versioned (A1)

- `disclosure_receipts` gains a `proof_version INTEGER NOT NULL` column. Existing rows are `1` through the migration default, and new rows are `2`.
- The signed canonical payload's `schema` is `tg-mcp-disclosure/v2` for v2. The receipt domain string is unchanged.
- **The v2 payload has no `consent_verified`, `consent_key_id` or `consent_challenge_digest`.** It adds:
  - `authorization_mode: "owner_direct"`;
  - `soft_threshold_exceeded: bool` (§2.4).
- **Verification dispatches on `proof_version`.** Nothing is inferred from nulls.
  - A v1 row must have both consent fields non-null and is rebuilt as v1.
  - A v2 row must have both null and `authorization_mode == "owner_direct"`, and is rebuilt as v2.
  - Any other combination is a failure.

### 2.2 Migration (A2, G1)

The migration is append-only. It rebuilds `disclosure_receipts` so that the two consent columns are nullable and `proof_version` is added. Every existing row gets `proof_version = 1`, and **its consent values are kept byte-identical**. No `authorization_mode` is retrofitted.

The proof, on an upgrade fixture holding real v1 receipts plus their audit history and exposure rows:

- `verify_persisted_receipt` passes on every v1 receipt before the migration.
- After the migration:
  - the canonical v1 proof bytes are identical to before;
  - verification passes;
  - row counts, IDs and foreign keys are equal (`PRAGMA foreign_key_check` is empty);
  - every index and trigger from before exists after (compared by `sqlite_master` name and normalised SQL);
  - `quick_check` returns ok;
  - the audit chain still verifies.

### 2.3 JCS extraction (A4)

`jcs_dumps` moves, as the single copy, from `consent/challenge.py` to `comms/transports/telegram/canonical.py`, and all 14 importers are repointed. There are two proofs:

1. AST equivalence of the moved function, reusing the 5b-1 checker's approach.
2. **Byte identity:** for every vector in `tests/fixtures/consent/jcs_vectors.json` (the Phase-2 corpus covering ordering, Unicode, escaping, booleans, null, integers and fatal inputs), the old encoder's output equals the new one's, and fatal inputs fail the same way.

The corpus moves to `tests/fixtures/canonical/` because it belongs to JCS, not consent.

### 2.4 `soft_threshold_exceeded` (A5)

It is set from the tier returned by the ledger consult **immediately preceding reservation**: `elevated` → `true`, `normal` → `false`, and `refuse` refuses as before.

The equivalence rule: the same ledger state plus the same frozen worst-case estimate give the same tier as before. It was interactive and is now informational. It is carried in the signed v2 payload **and** the audit event for that disclosure.

### 2.5 Retired keys (A3)

- `challenge-key`, `agent-approval-key` and `agent-transport-key` leave the **active** key registry, marked `retired`, never deleted.
- v1 receipts are signed by the disclosure key, not by these keys, so no verification depends on them. v1 receipts store consent key IDs as strings, and those strings stay.
- Any public material in `verification_keys` is kept through its retention horizon.
- Deleting the private Secure Enclave key and the pairing pins is an external runbook step (§6).

### 2.6 Formal model (A6)

`formal/model.py` drops its consent states, its `consent_*` transitions and `receipt_without_consent`, and **adds**:

- `OwnerDirectReceiptNeverClaimsConsent`;
- `ReceiptVersionsDistinguishable` (v1 has consent fields and no mode; v2 has none and `owner_direct`);
- `V2RequiresOwnerDirect`;
- `HardRefusalPrecedesRetrieval`;
- `ReservationCommitsAtMostOnce`;
- `NoHandoffBeforeCommitAndAnchor`.

The explored-state and assertion counts are recorded exactly. `SECURITY-MANIFEST.json` is updated to match.

## 3. The AI boundary, scoped precisely (A7)

The spec says exactly this, and makes no stronger claim:

- **MCP capability boundary:** no MCP tool exposes a transmission primitive. A structural test on every tool registry proves it: Telegram's ten tools today, WhatsVault's read tools, and any comms tools later.
- **Operator CLI:** may transmit directly. From 5d/5e, `comms campaign send` is operator-only.
- **Claude development environment:** the repo's `.claude/settings.json` marks send commands **always-ask**. This is a **UX interlock, not the system's authorization boundary**. It does not govern other agents.
- **An agent granted unrestricted operator-shell authority is, by definition, inside the operator trust boundary.**

## 4. The dormant WhatsVault sender (A8)

`INV-APPROVAL` is retired **normatively** for comms operator sends. The subtree stays byte-identical, so the tree-hash test keeps holding.

A guard proves that no send exists and none is reachable:

- nothing under `src/comms` imports `whatsvault`, statically or dynamically;
- `apps` (which holds WhatsVault's dispatcher) is not importable from the root environment;
- the root project installs no WhatsVault console script;
- `whatsvault.providers` contains only the provider protocol and `fake_meta`. There is no real Meta provider implementation (`apps/meta/` is a README).

These facts support the claim: *"The subtree remains exactly imported, the old sender stays dormant, v0.2 governs the new authority semantics, and no real WhatsApp send exists yet."*

## 5. Admin plane, deletions and test accounting

- **Admin authority is socket peer credentials only.** `PRESENCE_GATED`, the approver and the summaries are deleted, and a `presence` argument is rejected as unknown.
- **Deleted:**
  - modules: `consent/{broker,prompter,display,gate,admin_approval,admin_summaries}.py` and `consent/challenge.py`'s challenge wire (JCS has left it by then); `ipc/rendezvous.py`; `keys/pairing.py`;
  - the agent and its packaging: `agent/`, `scripts/package_agent.sh`, `scripts/consent-agent.plist`;
  - tests: `tests/agent/`, the Phase-2J join gate, the Touch-ID tests, and the consent smoke sections;
  - startup: the `bind_consent_socket` and `handshake_consent` steps;
  - doctor: the pairing and consent self-test checks.
- **Test accounting (G2):** the test-collection manifest is captured before and after. Every removed test ID is classified as *removed with the consent subsystem* or *replaced by an owner-direct assertion*, and **unexpectedly missing = 0** is a gate.

## 6. Retired identifiers are tombstoned

The challenge wire, `ApprovalEnvelope`, display digest (`telegram-mcp-display-v1`), RV-1 and the `PROMPT` / `APPROVAL` / `DENIAL` frames are retired from production. **Their names and domain strings must never be reassigned to new structures or semantics.** A tombstone list in `docs/comms-spec-v0.2.md` and a test pin this: none of those domain strings may appear in `src` with a new meaning (only in the tombstone list).

**Runbook step, outside every gate:** delete the paired Secure Enclave approval key and the pairing pins, and uninstall the old signed consent-agent bundle.

## 7. Sequencing (one plan)

1. **JCS extraction** (AST plus byte vectors).
2. **Receipt v2 plus migration** (upgrade fixture, rebuild preservation).
3. **Consent removed from the coordinator and seams**; soft threshold as a flag.
4. **Formal model rewrite** (replacement invariants).
5. **Admin presence removal.**
6. **Deletion** of the dead machinery; key rows retired; startup steps removed.
7. **Doctor cleanup.**
8. **Boundary guards:** no MCP send primitive, WhatsVault dormancy, and the `.claude/settings.json` ask rule.
9. **`docs/comms-spec-v0.2.md`** (delta plus tombstones) and CLAUDE.md's frozen-wire list.
10. **Evidence:** the collection manifest classification and the full gate.

Rehearsal first, in a throwaway worktree: the three riskiest points are JCS byte identity, v1 receipt verification after migration, and dormant-sender reachability.
