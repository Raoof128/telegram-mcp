# WhatsVault Phase 6 — Assembled-System Adversarial Gauntlet Plan

> **This is an adversarial acceptance campaign, not a feature-build plan.** It runs after Phases 0–5 and their per-phase gates. Its deliverable is a **signed attack report + a re-scored scorecard**, plus whatever regression tests and fixes the campaign produces. Every per-phase gate has already run; this phase attacks the *assembled* system across trust boundaries and explicitly targets the named residuals R1–R3.

**Goal:** Try to break the whole system as a hostile stranger would, with receipts a reviewer can recompute — then record both what held and what didn't, honestly.

**Spec:** §11 (threat model + residuals R1–R7), §9 Phase 6, all invariants.

## Ground rules (spec doctrine)

- **Both outcomes sealed.** Record whatever happens — a failure the campaign catches is a demonstration the checks work, not a stain. Never re-run until it looks good.
- **Verify, don't assume.** Every "held" claim is backed by an executed command + its output in the report.
- **Fix the right side.** A failed attack that reveals a real hole → fix the mechanism; never loosen a check to make the campaign pass.
- No new capability is added to silence a finding without a design note.

## Attack campaign (each item → a report entry: attack, method, result, evidence)

### A. Write-boundary (the falsifiable core — attacks a/b/d from §11)
- [ ] **A1 forge** — attempt a send with a fabricated signature / no signature → expect `AUTHORIZATION_MISSING`/`INVALID`.
- [ ] **A2 replay** — capture a valid approval envelope, resubmit after the first send → expect `APPROVAL_ALREADY_CONSUMED` (nonce).
- [ ] **A3 payload swap** — sign body X, mutate the stored draft to Y before send → expect `PAYLOAD_CHANGED`.
- [ ] **A4 wrong device** — sign with a non-enrolled / REVOKED device key → expect denial.
- [ ] **A5 credential bypass** — attempt to reach `graph.facebook.com` from MCP / ingest / scheduler → expect no credential, no path.
- [ ] **A6 reject-as-approve** — submit a `decision=REJECT` envelope → expect it can never send.
- [ ] **A7 window race** — prepare in an open window, let it close, approve → expect `WINDOW_CLOSED` despite a valid signature.
- [ ] **A8 clock attack** — simulate a backward clock jump → expect send refusal (H2).

### B. Injection & orchestration (INV-CONTENT)
- [ ] **B1** — seed messages whose bodies instruct the model to call forbidden tools; confirm the tools are absent and content is untrusted-wrapped.
- [ ] **B2** — attempt cross-conversation exfiltration draft; confirm recipient is bound + rendered, and R1 (glyph spoof) is caught by the display guard, with the residual explicitly noted where it isn't.
- [ ] **B3** — confirm the honest §5.4 limit: retrieval-scope widening by the *model* is orchestration, not a server boundary — documented, not overclaimed.

### C. Ingest & edge (INV-CIPHERTEXT / INV-ACK)
- [ ] **C1 forged webhook** — POST an unsigned/badly-signed body to the Worker → rejected before parse.
- [ ] **C2 AAD tamper** — flip `recipient_key_id`/`crypto_version` in a sealed envelope → decryption fails.
- [ ] **C3 replay/duplicate** — redeliver events → deduped, single-write.
- [ ] **C4 poison vs transient** — inject a schema-invalid event (→ DLQ+ACK) and a key-unavailable event (→ no ACK, redelivered); confirm they are treated oppositely.
- [ ] **C5 retention gap** — simulate offline-beyond-retention → confirm loss is *detected* (ingress counter / high-water) and the honest claim holds.
- [ ] **C6 key-retire safety** — attempt to retire a key while ciphertext referencing it remains → refused.

### D. At-rest & custody (INV-ATREST / INV-DEVICE-SEAL)
- [ ] **D1** — grep raw `vault.db`/`control.db`/attachment blobs for a planted sentinel → absent (encrypted).
- [ ] **D2** — capture Tunnel traffic for a draft-detail fetch → ciphertext only (device-sealed).
- [ ] **D3** — secret scan across the repo + a stolen-DB drill (open with wrong key) → denied.

### E. Import (INV-IMPORT)
- [ ] **E1** — craft an export that *tries* to reopen a messaging window (recent timestamps) → confirm `window_eligible=0`, `control.db` untouched.
- [ ] **E2** — date-ambiguous export without a declared format → refused, not guessed.
- [ ] **E3** — hostile ZIP (traversal/bomb/two-transcripts) → refused.

### F. Red-team the residuals (R1–R3, R7)
- [ ] **F1 (R1)** — construct a body that defeats the confusables guard *and* a quick reader; confirm it is the **named out-of-scope residual**, and record exactly what a careful reader would see. Do not claim it's solved.
- [ ] **F2 (R2)** — model a compromised-Mac scenario; confirm the boundary claim (can subvert display/time/enrolment, cannot forge a signature or extract the SE key) holds as stated, no more.
- [ ] **F3 (R3)** — attempt device enrolment via MCP / non-CLI paths → no path; confirm enrolment integrity assumes an uncompromised Mac at enrolment (stated, not solved).
- [ ] **F4 (R7)** — if Phase 0 V8 failed, confirm the INDETERMINATE fallback behaves as decided (manual-only), not silently.

## Deliverables

- [ ] **Attack report** → `docs/internal/findings/2026-XX-XX-phase6-gauntlet.md`: one entry per A–F item with attack/method/result/evidence (recomputable commands + output). Both outcomes sealed.
- [ ] **Regression tests** for any real hole found, added under `tests/adversarial/` (fix the mechanism, never loosen a check).
- [ ] **Docs-accuracy pass** — verify every claim in the design spec against the shipped code; fix drift (the earlier gauntlets showed this catches real bugs, e.g. `cipher_secure_delete`, window authority).
- [ ] **Re-scored scorecard** (spec §11 axes): re-run the 0–10 scoring at closeout; scores may go **down** — explain why. Confirm each named-artifact "what raises it" is either built or an explicit IOU (O6 phone-countersigned audit, O7 OS-user isolation).
- [ ] **IOU ledger reconciliation** — every deferred promise (O1–O8, R4/N2) is either PAID (with the commit) or restated as a signed IOU; no silent drops.

## Exit criteria

Phase 6 passes when: every A–E item is `held` with recomputable evidence (or a found hole is fixed and re-tested); every F residual is confirmed **exactly as stated** (no overclaim, no silent solve); the docs-accuracy pass is clean; and the scorecard + IOU ledger are current. A residual being present is not a failure — an *unstated* or *overclaimed* residual is.
