# WhatsVault Phase 0 — Coexistence & Cloud API Verification Plan

> **This is a verification plan, not a TDD implementation plan.** Its deliverable is a **findings document**, not code. It gates *production activation* of Phases 3–5; it does **not** gate development of them (their local cores are fixture-testable without it). Do not write or run production code here. Do not press onboarding buttons until the eligibility checks below pass.

**Goal:** Establish, with receipts, whether and how this specific WhatsApp number can run under Meta Coexistence + direct Cloud API, and confirm the provider behaviours the write path depends on — before any component that talks to Meta is activated.

**Spec:** `docs/internal/specs/2026-08-27-whatsvault-design.md` (§0.1 C3, §9 Phase 0, §6.6 O2, INV-PROVIDER).

**Output artifact:** `docs/internal/findings/2026-XX-XX-phase0-coexistence.md` — each question answered YES/NO/UNKNOWN with the evidence (screenshot, API response, doc link with the exact quote). No prose-only answers.

## Ground rules

- **Do not run ordinary Cloud API `/register` on the personal number.** That can change how the number is registered and jeopardise the Business App. (Spec §1.)
- A third party (BSP) may be used **only** for onboarding, and only if ownership, webhook control, data exposure, retention, and subsequent direct-API operation are all verified first (INV-PROVIDER).
- Every figure from a secondary source is marked "reported" until the primary Meta/Cloudflare doc is pinned with a verbatim quote.

## Verification checklist (each becomes a findings entry)

- [ ] **V1 — Number eligibility.** Is the existing WhatsApp Business App number eligible for Coexistence onboarding? Record: account age, messaging-quality state, any provider-stated prerequisite (e.g. the reported 7-day active-use requirement). Evidence: the provider's eligibility screen / API response.
- [ ] **V2 — Onboarding path.** Document the exact, current, self-managed Business-App → Coexistence onboarding steps end to end **before** performing any of them. If the official path is not documented clearly enough to execute safely, STOP and record that as the finding — do not improvise.
- [ ] **V3 — Business App preserved.** Confirm that Coexistence keeps the WhatsApp Business App usable on the phone simultaneously with API access (not a one-way migration). Evidence: provider doc + a post-onboarding test on a throwaway/secondary number if available.
- [ ] **V4 — Inbound webhook.** Confirm a live inbound 1:1 message is delivered to the configured webhook, and capture the exact payload shape for the `MESSAGE_INBOUND` normaliser (message id, sender, timestamp *unit* — confirm **seconds**, type, content). Evidence: captured payload (redacted).
- [ ] **V5 — `smb_message_echoes`.** Confirm whether messages typed in the Business App on the phone are mirrored to the webhook as echoes (`MESSAGE_ECHO`), and capture the payload shape. If not delivered, record it — the "read your own sent messages" feature depends on this.
- [ ] **V6 — History sync.** Confirm whether any historical-message sync is delivered on onboarding, its volume, and its payload shape (`HISTORY_EVENT`). Record what is and isn't covered. The design must not *depend* on this (export backfill covers the gap), but knowing the actual behaviour sizes the gap.
- [ ] **V7 — Status webhooks.** Confirm `sent`/`delivered`/`read`/`failed`/`deleted` status notifications are delivered, their ordering behaviour, and payload shape (`MESSAGE_STATUS`). Evidence: captured status events for a test send.
- [ ] **V8 — `biz_opaque_callback_data` (O2, BLOCKING for Phase 4 UX).** Confirm the direct Cloud API accepts `biz_opaque_callback_data` on a send **and echoes it back into the status webhook**. If YES, deterministic INDETERMINATE reconciliation works. If NO, record the fallback decision required before Phase 4 (self-sent correlation marker, or accept manual-only reconciliation). Evidence: send response + the correlated status webhook showing the field.
- [ ] **V9 — Media URL refresh.** Confirm `GET /{media-id}` returns a fresh temporary URL after the first URL expires (so a sleeping Mac does not permanently lose media within the retention window). Evidence: two `GET`s of the same media id minutes apart.
- [ ] **V10 — 24h window + templates.** Confirm free-form send fails outside the 24h customer-service window and requires an APPROVED template; capture the exact error. Confirm the `message_templates` endpoint lists template status. Evidence: a rejected free-form send + a template list response.
- [ ] **V11 — Groups exclusion.** Confirm (or refute) that Coexistence numbers cannot send to / are not delivered group messages via the API. Evidence: provider doc quote. (Spec C2.)
- [ ] **V12 — Credentials & scopes.** Confirm the runtime messaging scope (`whatsapp_business_messaging`) is separable from the management scope (`whatsapp_business_management`), so `whatsvault-meta` can hold only the former. Evidence: the token/scope configuration screen.
- [ ] **V13 — Retention.** Pin Meta's current Cloud-API message-content retention (reported ~30 days) with the primary doc quote — sizes how promptly media must be materialised.
- [ ] **V14 — Cloudflare Queues.** Pin current Queues limits: max retention (target 14 days on paid; Free tier 24h), pull-consumer + DLQ semantics, `max_retries` ceiling, and the realtime metric field name (`oldest_message_timestamp_ms`). Evidence: current Cloudflare docs quotes. (Spec §7.)

## Exit criteria

Phase 0 is complete when every V-item has a YES/NO/UNKNOWN finding with evidence. **Production activation** of Phases 3–5 is permitted only when V1–V3 (eligibility/onboarding/app-preserved), V4/V7 (inbound + status delivery), V12 (scope separation), and V14 (14-day retention) are YES. V8's answer selects the Phase 4 reconciliation design. A NO on V1/V2/V3 pauses the entire write path (the read/vault half proceeds regardless).
