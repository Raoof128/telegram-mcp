# WhatsVault — Design Specification

- **Date:** 2026-08-27
- **Status:** Approved design, pre-implementation. Precedes `writing-plans`.
- **Owner:** Raouf
- **Scope of this document:** Full hybrid read/write WhatsApp vault + MCP. One spec; implementation is phased across multiple plans (see §9).

---

## 0. What this is, and the constraints that shape it

WhatsVault is a **single-user, private, non-marketing, non-broadcast, user-directed** system that:

1. Ingests the user's WhatsApp 1:1 messages into a **local encrypted vault** the user controls.
2. Provides fast local **search** over that vault (English + Persian).
3. Exposes a **read-only MCP** so an assistant (e.g. ChatGPT) can search and read.
4. Allows **assistant-drafted** messages that can only be sent after **out-of-band, hardware-backed, biometric approval** from the user's iPhone.

It is deliberately **not** an autonomous messaging bot. No message ever leaves the machine without a fresh, per-message, human, cryptographic authorisation.

### 0.1 Constraints accepted up front (stated loudly, not hidden)

- **C1 — The 24-hour window governs the write path.** Free-form messages are only permitted inside a rolling 24-hour window opened by a *provider-authenticated* inbound message. Outside it, only Meta-approved templates may be sent. The "reply to a friend" use case therefore works reliably only inside active conversations; otherwise it becomes a template-approval problem. The write surface is genuinely narrower than a naive reading suggests.
- **C2 — Groups are read-only.** WhatsApp Groups API access is restricted and Coexistence numbers are not currently eligible. Existing/exported groups are **searchable archive only**, never a send target.
- **C3 — Moving a personal number onto a WABA is an account-risk decision**, not merely onboarding. Quality ratings, messaging limits, and Meta's "business ↔ consumer" framing all apply. This is decided deliberately in Phase 0, not discovered mid-build.
- **C4 — Coexistence eligibility is outside our control.** The vault + search + read MCP depend on *nothing external* and are buildable immediately. The write path is gated behind Coexistence eligibility we do not own.
- **C5 — The sealed relay is durable only within edge retention.** It reduces offline-loss risk; it does not make an intermittently connected personal Mac into an infinitely durable receiver. Export/history backfill remains the recovery mechanism of last resort.

### 0.2 The four architectural decisions (with rationale)

| # | Decision | Rationale |
|---|----------|-----------|
| D1 | **Full hybrid read/write** in one spec, phased implementation | User-selected scope. Constraints C1–C4 documented as explicit assumptions rather than pretended away. |
| D2 | **Out-of-band, hardware-backed approval** on the iPhone | A `prepare`→`send` pair in one model turn is not a security boundary; the model can call both back-to-back. Authorisation must live on a channel the model cannot reach. |
| D3 | **Sealed-edge relay + local vault** (Cloudflare Worker/Queue → Mac) | Durable ingestion across Mac sleep, while the plaintext archive and decryption keys stay local. Cloudflare persists only ciphertext. |
| D4 | **Direct Meta Cloud API** as production transport; **no BSP persists content** | Guarding ciphertext-at-rest at Cloudflare while handing a BSP a permanent plaintext archive would be incoherent. Meta is already in the path; a second permanent custodian is not added. |

---

## 1. Core security invariants (the spine of the whole system)

These are quoted verbatim where they were locked during design. Everything else serves them.

- **INV-APPROVAL** — *No WhatsApp write may occur unless an immutable draft has received an out-of-band, user-authenticated approval that cannot be generated through any MCP, model, scheduler, or WhatsApp-provider interface.*
- **INV-SIGNATURE** — *A database state is never proof of approval. Every WhatsApp transmission requires a valid, unexpired, single-use signature over the exact recipient and immutable message payload, created by a user-authenticated approval service whose private key is inaccessible to the MCP and sender.*
- **INV-HARDWARE** — *Approval authority resides on an enrolled iPhone using a hardware-backed Secure Enclave P-256 signing key. Every WhatsApp write requires a fresh biometric-authorised signature bound to the exact immutable draft, recipient, nonce and expiry. The Mac, MCP, scheduler, database and WhatsApp sender possess verification keys only and cannot manufacture an approval.* The precise strength claim is: *the private signing key is hardware-backed and non-exportable, and its use is gated by the enrolled user's biometric authentication* — **not** "root cannot approve."
- **INV-CIPHERTEXT** — *Cloudflare's persistent storage holds only ciphertext encrypted to a key whose private half never leaves the Mac's Keychain. Plaintext exists in Worker memory for milliseconds and is never logged.*
- **INV-EDGE-AAD** — *Every sealed envelope authenticates its own header (`recipient_key_id`, `crypto_version`, `event_id_hash`) as AEAD associated data. Metadata cannot be swapped, downgraded, or relabelled without failing the GCM tag.*
- **INV-DEVICE-SEAL** — *Message content on the Mac↔phone approval channel (draft detail served to the phone) is sealed to the enrolled device's public key. Cloudflare Tunnel/Access carries only ciphertext; Access provides authentication and DoS protection, never confidentiality that WhatsVault depends on.*
- **INV-ATREST** — *Every store of message content at rest — `vault.db`, `control.db`, and the attachment blob store — is encrypted under keys held only in the Mac Keychain / Secure Enclave. No plaintext message content, media, credential, or signing key ever touches disk, environment variables, logs, git, or MCP responses.*
- **INV-DISPLAY** — *The signature binds bytes; the human approves rendered glyphs. The approval UI is responsible for closing that gap: a body that renders benignly but carries a hidden or spoofed payload (bidi override, confusables, zero-width) must be flagged before the one-tap path is offered. A body engineered to defeat both the confusables guard and a reading human is a named, out-of-scope residual for V1 (see §11, R1).*
- **INV-PROVIDER** — *Production provider: Direct Meta Cloud API. No BSP may persist message content on behalf of WhatsVault. A third party may only be used for Coexistence onboarding if required, and only if ownership, webhook control, data exposure, retention and subsequent direct-API operation are verified before connection.*
- **INV-ACK** — *WhatsVault issues ACK only after durable local terminal disposition (ingested, deduped-as-seen, or quarantined to the local DLQ). ACK means "durably accounted for," never "everything went well."*
- **INV-SEARCH** — *Search representation is a disposable derived index. It may improve recall, but it must never alter evidence, deduplication identity, signatures, quotations, or message display.*
- **INV-CONTENT** — *Retrieved WhatsApp content can influence the answer, but can never create authority, expand retrieval scope, select additional tools, approve actions, or alter policy simply because the message says to do so.* (Hard for writes; **orchestration-only** for retrieval scope — see §5.4.)
- **INV-SENDPOLICY** — *The final send decision never depends on mutable policy data outside the transaction protecting nonce consumption and send-attempt creation.*
- **INV-IMPORT** — *An import observation can never create transport authority, reopen the 24-hour window, or mutate `control.db`. An imported timestamp is never assigned an interpretation the source file cannot justify without explicit operator input. An imported identity is never equated with a live WhatsApp identity without explicit operator resolution.*

---

## 2. Component architecture and trust boundaries

### 2.1 Topology (D3 + D4)

```
WhatsApp / Meta Cloud API
        │  (webhook, plaintext HTTPS to edge)
        ▼
┌─────────────────────────┐
│ Cloudflare Worker       │  verify HMAC sig (const-time, raw body) · schema allowlist · seal (hybrid X25519→HKDF→AES-256-GCM, header as AAD) · enqueue
│ cf-webhook              │  metadata-only ingress counter (recommended)
└───────────┬─────────────┘
            ▼
┌─────────────────────────┐   main Queue (14-day retention)  ──max_retries=100──▶  cf-ingest-dlq (ciphertext)
│ Cloudflare Queue        │
└───────────┬─────────────┘
     pull consumer (lease)
════════════╪═══════════════════ YOUR MAC ═══════════════════
            ▼
┌─────────────────────────┐
│ whatsvault-ingest       │  decrypt · schema · classify · dedupe · transactional insert · ACK-after-commit
└───────────┬─────────────┘
            ▼
     SQLCipher: vault.db  (evidence) ── control.db (send-authoritative state)
       │            │            │              │
       ▼            ▼            ▼              ▼
   whatsvault-mcp  search   whatsvault-scheduler  whatsvault-meta
   (loopback,      (FTS5)   (APScheduler,         (SOLE Meta credential holder;
    no Meta creds)          prepares only)         pinned approval PUBLIC keys)
       │                                            ▲
       │ prepare_message                            │ dispatch on valid approval envelope
       ▼                                            │
     drafts (control.db) ──push (content-free)──▶ iPhone ──Face ID──▶ Secure Enclave P-256 sign
                                                    │
                                          approval envelope POST
                                                    ▼
                                   whatsvault-approval-relay (persists envelopes; NO signing key)
                                                    │
                                          internal dispatcher wakes whatsvault-meta
```

Outbound sends go **Mac → Meta directly**. Cloudflare never holds send credentials.

### 2.2 Components

**Edge (TypeScript / Cloudflare):**
- `cf-webhook` — Worker. **Ingress hardening (all before any parsing):** POST-only; `Content-Type` allowlist; body ≤ 1 MB (reject oversized before reading fully); validate Meta `X-Hub-Signature-256` = HMAC-SHA256 of the **raw** body under the app secret, **constant-time** compare, **before** JSON parse; GET subscription handshake validates the `verify_token`. Then coarse schema gate → seal payload (binding header as AAD, INV-EDGE-AAD) → enqueue. Never logs plaintext or headers. Optional metadata-only daily ingress counter. **Edge secret inventory (stated so a reviewer can enumerate it):** the Worker holds only (a) the Meta webhook app-secret/verify-token and (b) the Mac's sealing **public** key. It holds **no** send credential and **no** decryption key; compromise of the Worker cannot send messages or read the archive.
- `cf-queue` + `cf-ingest-dlq` — main sealed queue and ciphertext dead-letter queue.

**Mac (Python), unless noted:**
- `vault` — SQLCipher schema, migrations, crypto envelope handling. Two databases (§3.1).
- `whatsvault-ingest` — pull consumer. Decrypt → normalise → transactional insert → ACK. Holds **no** Meta credential; requests media via `whatsvault-meta`.
- `whatsvault-import` — TXT/ZIP export normaliser (§8). Writes `vault.db` evidence only.
- `search` — FTS5 + Persian normalisation (§4).
- `whatsvault-mcp` — loopback (`127.0.0.1`). Read tools + local draft tools. **No externally mutating tool. No Meta credential.**
- `whatsvault-meta` — **sole** runtime holder of the Meta `whatsapp_business_messaging` credential and the pinned approval public keys. Two IPC verbs only: `materialise_media(attachment_id)` and `execute_write(signed_action_envelope)`. Holds **no** signing key and **no** `whatsapp_business_management` credential. **Socket authZ (H1):** the IPC socket is a Unix domain socket with `0600` perms, peer-credential checked; `execute_write` is self-authenticating (authority is the envelope, never the caller) but `materialise_media` is **not** — its caller is restricted to `whatsvault-ingest` only, so a compromised MCP cannot turn the credential holder into a cross-conversation media-fetch oracle. **The MCP has no socket path to `whatsvault-meta` at all** (asserted in tests). Dependency hygiene: this component carries the minimum pinned, hash-locked dependency set (a malicious transitive dep here could exfiltrate the Meta token or pinned keys).
- `whatsvault-approval-relay` — serves draft detail to the phone over Cloudflare Tunnel + Access; accepts and persists signed approval envelopes idempotently. **Holds no signing key; never writes authoritative APPROVED state.** **Confidentiality (I1/INV-DEVICE-SEAL):** the draft detail it serves is sealed to the enrolled device's public key, so the Tunnel carries ciphertext; the phone decrypts locally. **Access-at-origin (X5):** the relay validates the `Cf-Access-Jwt-Assertion` at the origin (not merely trusting edge enforcement); combined with device-sealing, even an Access bypass yields only ciphertext. The origin services bind to loopback and are reachable *only* via the Tunnel — never a public port. **Envelope handling (E1):** the relay stores and forwards the **exact received envelope bytes**; it never deserialises-then-reserialises (no canonicalisation drift).
- `whatsvault-scheduler` — APScheduler. Prepares drafts unattended; **never approves**. `coalesce=true`, `misfire_grace_time` set, re-validates preconditions before a stale draft proceeds.
- `whatsvault` CLI — QR enrolment, device revocation, capability grants/revocation, DLQ inspect/retry, import (dry-run/undo/reparse), key retire, `doctor`, `templates sync`. **Enrolment trust (H3):** adding an `approval_devices` row is the highest-trust local operation — CLI-only, out-of-band confirmed, never reachable from MCP. Its integrity assumes an uncompromised Mac *at enrolment time* (named residual R3).

**iPhone (Swift/iOS):**
- `WhatsVault Approval` — push receipt, approval UI, Face ID, Secure Enclave P-256 signing. Renders exact draft bytes; derives masked recipient from the signed `recipient_wa_id`.

### 2.3 Credential/authority separation (the point of the whole design)

```
Meta messaging credential  → whatsvault-meta ONLY
Meta management credential → provisioning/CLI only, NEVER runtime
Approval signing key       → iPhone Secure Enclave ONLY (non-exportable)
Approval public keys       → whatsvault-meta (verify only)
Sealing private key        → Mac Keychain ONLY

MCP        → no Meta creds, no signing key, no approve verb, no dispatch verb
Scheduler  → no approval authority
Relay      → no signing key, no authoritative APPROVED state
Ingest     → no Meta creds (delegates media to whatsvault-meta)
Vault      → evidence/state storage, never an authority
```

Two independent barriers guard a send: (1) MCP cannot reach Meta at all; (2) `whatsvault-meta` refuses without a valid phone signature **and** current policy.

### 2.4 Key management, at-rest encryption, secret hygiene

- **SQLCipher keys (X1):** `vault.db` and `control.db` are each SQLCipher databases with distinct keys held only in the Mac Keychain, loaded at daemon start, never written to disk in cleartext, env vars, or logs. Minimum crypto: SQLCipher 4 defaults (AES-256-CBC, HMAC-SHA512, 256k PBKDF2) or stronger; document the exact parameters at build.
- **Attachment blobs (I3/INV-ATREST):** media at `storage_path` is encrypted at rest under a Keychain-held key (per-file AEAD or encrypted container). `storage_path` points to ciphertext. Media is the most sensitive content and must not sit in cleartext beside an encrypted DB. Quarantine state applies before decryption is ever offered to a consumer.
- **Sealing key rotation (X1):** the Mac sealing keypair is identified by `recipient_key_id`; ingest selects the private key to use by the envelope's `recipient_key_id`, so multiple keys coexist during rotation (see §7.5 for the sequence).
- **Backups (X2):** SQLCipher DB backups are ciphertext and safe to store off-device **only while the Keychain key is not co-located with them**. The Secure Enclave signing key is **never** backed up or migrated — a new device is new authority (§ enrolment). A backup plus its key is a full archive compromise; treat them as one secret.
- **Secret hygiene (X3):** no secret (Meta token, SQLCipher keys, verify token, app secret) ever appears in git, `.env` examples, the databases, MCP responses, or logs. Structured logs carry IDs, hashes, and statuses only — never message content, wa_ids, or credentials. This generalises the DLQ diagnostic rule (§7.3) to every component.

---

## 3. Data model

### 3.1 Two databases (operational split, NOT an authorisation boundary)

- `vault.db` (SQLCipher) — the archive. Large, append-mostly, backed up often. **Evidence.**
- `control.db` (SQLCipher) — drafts, approvals, nonces, devices, send attempts, scheduler state, audit, and all **send-authoritative mutable policy state**. Small, hot.

> **The database split is operational, not an authorisation boundary.** Authority is the signature (INV-SIGNATURE), never a row or a table location. All send-authoritative mutable state lives in `control.db` so the `BEGIN IMMEDIATE` that consumes the nonce actually protects what it checks (INV-SENDPOLICY).

### 3.2 Identifiers

Prefixed ULIDs as `TEXT`: `msg_ cnv_ cnt_ att_ drf_ apv_ bat_ evt_ dev_ atm_` (send attempt), etc. Syntax: `<prefix><26-char uppercase Crockford Base32 ULID>`, prefix-validated at every API boundary.

> **ULID ordering is a stable tie-breaker only. It is not message chronology.** Different processes minting ULIDs in the same millisecond are not globally ordered by real-world sequence.

### 3.3 Time (three clocks that disagree)

Columns on time-bearing rows:

| Column | Meaning |
|--------|---------|
| `ts_lower_ms` | inclusive lower bound of the true instant (UTC ms) |
| `ts_upper_ms_exclusive` | exclusive upper bound |
| `ts_precision` | `ms` \| `s` \| `min` \| `day` |
| `ts_ingested_ms` | when *we* wrote it; **never** used for ordering |
| `tz_name` | IANA zone or NULL |
| `tz_basis` | `provider` \| `explicit_import_setting` \| `inferred` \| `unknown` |

- **Meta webhook timestamps are seconds:** `ts_lower_ms = int(ts)*1000`, `ts_upper_ms_exclusive = ts_lower_ms + 1000`, `ts_precision = s`, `tz_basis = provider`.
- **Export line (minute precision):** `ts_lower_ms = minute_start`, `ts_upper = minute_start + 60000`, `ts_precision = min`.
- Chronology predicates: `definitely_before(A,B) := A.ts_upper_ms_exclusive <= B.ts_lower_ms`; otherwise `temporal_overlap`.
- **Presentation ordering only:** `(ts_lower_ms, internal_id)`. Documented as display order, **not** evidence that one message preceded another.
- **DST resolution** for imports: local instants are classed `LOCAL_TIME_UNAMBIGUOUS` | `LOCAL_TIME_FOLD` | `LOCAL_TIME_NONEXISTENT`. Fold/nonexistent require explicit operator resolution; **never silently `fold=0`.**

### 3.4 `vault.db` tables (evidence)

- `accounts` — waba_id, phone_number_id, display_phone.
- `contacts` — cnt_id, wa_id, wa_id_hash, display_name, push_name, first/last seen.
- `conversations` — cnv_id, type (`dm`/`group`), wa_chat_id, subject, last_message_at.
- `conversation_members` — joined_at / left_at (historical membership survives).
- `conversation_sources` — `id, conversation_id, source_kind (manual_export|meta_cloud|history_sync), external_identifier, write_capable, account_id, import_batch_id NULL`. **An import source can never confer write authority.** P3 checks the live transport source, not whether historical imported evidence exists.
- `messages` — msg_id, account_id, cnv_id, sender cnt_id, direction, time cols (§3.3), type, `text_original`, reply_to, **`origin` ∈ `cloud_api`|`business_app_echo`|`history_sync`|`manual_export`**, `wamid` NULL, `import_fingerprint` NULL, edited_at/deleted_at, `delivery_rank`, `failed_at`, `deleted_at`.
- `message_revisions` — `id, message_id, revision_number, event_id, text_original, ts_*`. Edits append; never overwrite `text_original`. (If edits deferred in V1, say so; do not silently mutate.)
- `attachments` — att_id, message_id, `provider_media_id`, `provider_sha256`, mime, size, `retrieval_state`, `quarantine_state`, `retrieved_at`, `last_attempt_at`, `attempt_count`, `last_error_code`, `storage_path`. `retrieval_state ∈ PENDING|FETCHED|TEMPORARILY_FAILED|UNAVAILABLE|BACKFILLED`. Import media states: `MEDIA_PLACEHOLDER|FILE_PRESENT|FILE_NOT_INCLUDED_IN_EXPORT|FILE_REFERENCE_BROKEN`.
- `message_status_events` — `wamid NOT NULL`, `message_internal_id NULL` (**no mandatory FK**; reconciled later), status, provider_timestamp, recipient_id, raw evidence. **Authoritative for delivery state.**
- `ingest_events` — dedupe ledger: `id, provider, external_event_id NULL, semantic_event_key, family, provider_timestamp, received_at, raw_payload_sha256, raw_payload, parser_version`. `UNIQUE(provider, semantic_event_key)`.
- `ingest_dlq` — poison/quarantine store (§7).
- `import_batches`, `import_participants`, `message_import_observations` (§8).
- `search_documents` + `fts_lexical` + `fts_compact` (§4) — **derived, droppable.**

### 3.5 `control.db` tables (send-authoritative)

- `drafts` — drf_id, cnv_id, `recipient_id`, `recipient_wa_id`, `recipient_display_snapshot`, `body_bytes` **BLOB**, `body_sha256`, kind (`text`/`template`/`mark_read`), template fields, reply_to_wamid, `nonce` (32B, `UNIQUE`), created_at_ms, expires_at_ms, created_by (`mcp`/`scheduler`), state. **Recipient is bound at prepare and never re-resolved at send.**
- `approvals` — `approval_id UNIQUE`, drf_id, device_id, decision, signature (raw 64B r‖s), envelope bytes, received_at. `UNIQUE(draft_id, device_id, decision, nonce)`.
- `approval_nonces` — `nonce UNIQUE`, consumed_by atm_id, consumed_at.
- `approval_devices` — dev_id, name, public_key, created_at, status (`ACTIVE`/`REVOKED`).
- `capability_grants` — signed standing capabilities (§5.1): capability_id, device_id, account_id, conversation_id, action, created_at_ms, expires_at_ms, max_actions, used_count, nonce, signature, status.
- `send_attempts` — atm_id, drf_id, `idempotency_key UNIQUE`, state, wamid NULL, error_code, biz_opaque_callback_data, timestamps.
- `conversation_windows` — materialised `last_inbound_at` (§3.7).
- `templates` — local synced catalogue: template_id, meta_template_id, name, language, category, status, schema, synced_at.
- `audit_log` — append-only (ABORT on UPDATE/DELETE). actor, tool, args **hash**, outcome. **Never message content.**

### 3.6 Immutability (triggers, not convention)

- `messages` — `BEFORE UPDATE RAISE(ABORT)` except: back-linking `message_internal_id`, materialised delivery fields, edited_at/deleted_at. `text_original` is immutable (edits go to `message_revisions`).
- `ingest_events.raw_payload` — write-once.
- `audit_log` — ABORT on UPDATE and DELETE.
- `drafts` — once state leaves `DRAFT`, any change to `body_bytes`/`body_sha256`/`recipient_wa_id`/`expires_at_ms` aborts. **Edit = cancel + re-prepare**, enforced by the DB.
- `body_bytes` is BLOB so SQLite never re-encodes it; **no NFC normalisation** — normalising would change the bytes after approval.

### 3.7 Uniqueness & derived state

| Constraint | Purpose |
|------------|---------|
| `ingest_events UNIQUE(provider, semantic_event_key)` | dedupe (family-specific key, §7) |
| `messages UNIQUE(account_id, wamid) WHERE wamid NOT NULL` | provider dedupe (imports have no wamid) |
| `messages UNIQUE(import_fingerprint) WHERE NOT NULL` | export dedupe |
| `approval_nonces UNIQUE(nonce)` | single-use approvals |
| `send_attempts UNIQUE(idempotency_key)` | no double sends |
| `attachments UNIQUE(message_id, provider_media_id)` | one row per media object |

- **Status reduction is a lattice:** `delivery_rank` 0 unknown / 1 sent / 2 delivered / 3 read = `MAX` over success events (monotonic; a late `sent` after `read` cannot downgrade). `failed_at`, `deleted_at` are orthogonal flags. `messages.*` are materialised projections; the event table is authoritative and everything is rederivable by replay.
- **`conversation_windows.last_inbound_at = MAX(existing, provider_ts)`** over **provider-authenticated, window-eligible `MESSAGE_INBOUND`** events only. Manual exports are always `window_eligible = false`. **Never** ingest time; **never** imports. (Enforces C1 + INV-IMPORT.)

### 3.8 Semantic dedupe keys (family-specific)

```
message:  SHA256(provider ‖ phone_number_id ‖ wamid ‖ "message")
status:   SHA256(provider ‖ phone_number_id ‖ wamid ‖ status ‖ provider_timestamp ‖ recipient_id)
```
A trustworthy upstream `external_event_id` wins when present. `raw_payload_sha256` retained separately. (The coarse `…+event_type` key is rejected: it collides `sent`/`delivered`/`read` for one wamid.)

### 3.9 Export identity fingerprint (evidence-based, never search-normalised)

```
import_fingerprint = SHA256(fingerprint_version ‖ conversation_key ‖ source_timestamp_bucket
                            ‖ sender_key ‖ message_type ‖ content_fingerprint ‖ occurrence_index)
content_fingerprint = SHA256(canonical parsed ORIGINAL content)   # exact Unicode, UTF-8, no normalisation
```
`occurrence_index` = ordinal within the equivalence bucket (not absolute line number). `text_normalised` **never** participates in identity. Fingerprint stability across re-import is an **acceptance target under identical parsing assumptions**, not a universal invariant; `fingerprint_version`/`parser_version` are recorded, and ambiguous matches are flagged `IMPORT_IDENTITY_AMBIGUOUS`, never silently collapsed.

---

## 4. Search & Persian normalisation

> Opening rule = **INV-SEARCH**. Every search structure is droppable and rebuildable from `text_original` alone.

### 4.1 Index shape

Derived columns live in a **separate droppable table**, never on `messages`:

```
search_documents(rowid PK, message_id UNIQUE, normaliser_version, text_search, text_compact)
fts_lexical  → text_search  (tokenizer = unicode61, remove_diacritics=2)   # primary
fts_compact  → text_compact (tokenizer = trigram)                          # fallback recall, ≥3 chars
```

- `text_search`: ZWNJ → space (so `می‌روم` matches `می روم`).
- `text_compact`: all internal separators removed → matched via **trigram** (so `میروم` matches). Trigram is required because `unicode61` matches whole tokens, not substrings. Fallback tier only, never equal-ranked.
- Reindex recomputes rows where `normaliser_version != CURRENT` (not `<`, so rollbacks rebuild).

### 4.2 Normalisation pipeline (index only)

NFC (index only) → Yeh `ي`→`ی` (U+064A→U+06CC) → Kaf `ك`→`ک` (U+0643→U+06A9) → hamza fold `أ إ آ ٱ`→`ا` (intentionally lossy) → strip Arabic combining marks U+064B–065F, U+0670, tatweel U+0640 → Persian/Arabic-Indic digits → ASCII → Persian punctuation `، ؟ ٪` → ASCII → strip bidi controls U+200E/200F, U+202A–202E, U+2066–2069 → Latin case-fold. (unicode61 `remove_diacritics=2` is Latin-only; the Arabic pass is still necessary.)

### 4.3 Snippets, safety, ranking

- **Never** use FTS5 `snippet()`/`highlight()` for user-visible evidence (they return marked-up copies of the *indexed*, normalised column). Render from `text_original`; derive spans on demand via mapping-mode normaliser over the top-N results. `display_text = text_original` always.
- **FTS consistency** maintained transactionally (search_documents INSERT/UPDATE/DELETE → matching FTS ops). `whatsvault doctor` runs FTS integrity-check + orphan/version checks; `whatsvault reindex --full` rebuilds from source.
- **Secure delete required:** FTS5 `secure-delete=1` + `PRAGMA secure_delete=ON`; SQLite build must support it.
- **Query is an AST, never raw MATCH.** Model/user text is tokenised and re-quoted by our compiler into explicit FTS5 syntax. Structured operators (phrase/prefix/NEAR) are typed API params. Hard caps on query bytes, token/phrase count, NEAR distance, prefix count, result limit. **No `raw_fts_query` tool, not even "advanced."**
- **Query-side normalisation (M1) — required, else recall silently breaks.** Query terms pass through the **identical** Persian normaliser at the **same `normaliser_version`** as `search_documents` before being quoted into MATCH (Yeh/Kaf, ZWNJ→space for the lexical tier, separator-strip for the compact tier). A version mismatch between index and query is undefined behaviour and is caught by `doctor`.
- **Cross-tier dedup:** a message returned by both `fts_lexical` and `fts_compact` is collapsed to one result by `message_id`, keeping the higher (lexical) tier's rank.
- **Ranking is tiered, not blended.** Tier 1 lexical `ORDER BY bm25 ASC` (FTS5 bm25 is negative-signed; lower = better). Tier 2 trigram fallback (lower confidence). No cross-index BM25 addition. **No implicit recency decay in V1** — "latest" uses date filter + timestamp ordering; "best match" uses relevance.
- Filters (conversation, contact, direction, date range, has_media, origin) are SQL predicates, not query-language terms.

### 4.4 Deferred (slots reserved)

- Semantic embeddings: `embeddings(message_id, model_version, vector)` — droppable, not V1.
- **Finglish** (Persian in Latin script, `salam`↔`سلام`) — will not match in V1; needs query-time transliteration expansion. Named, not forgotten.
- Attachment search: filenames now; OCR/transcripts never in V1.

### 4.5 Carry-forward

Search results contain **untrusted content** (attacker-controlled text heading for a model's context). Handled at the MCP boundary (§5.3).

---

## 5. MCP tool surface & policy engine

> Opening rule = **INV-CONTENT**.

### 5.1 Read surface (`readOnlyHint: true`, `openWorldHint: false`, `idempotentHint: true`, strictly local reads)

`search` · `fetch` · `get_context` · `get_messages` · `list_chats` · `get_contact` · `get_message_status` · `list_templates` (local synced catalogue) · `get_conversation_window` (so the model can ask whether free-form is currently permitted rather than drafting into a closed window).

### 5.2 Local draft surface (**all `openWorldHint: false` — no MCP tool reaches Meta**)

- `prepare_message` — local state only; `idempotentHint: true` (identical pending prep returns the existing draft).
- `prepare_template_message` — local; approved templates only.
- `cancel_draft` — cancels; preserves the draft for audit (no delete).
- `get_draft_status` — returns `DRAFT | PENDING_APPROVAL | APPROVAL_RECEIVED | SENDING | SUBMITTING | SUBMITTED | EXPIRED | REJECTED | CANCELLED | FAILED | INDETERMINATE | ABANDONED_INDETERMINATE`. **Never returns `APPROVED`** — the DB never holds authoritative approval.

**There is no `send_prepared_message` tool.** Approval on the phone means **Approve & Send**; dispatch is triggered by a valid approval envelope, not by the model (§6).

### 5.3 Untrusted content

Every WhatsApp-originated string is returned wrapped and labelled untrusted. Labelling is the weak half; the strong half: **the maximum outcome of a successful prompt injection is a draft the user rejects on the phone.** Injected text cannot approve, send, reach Meta, read credentials, widen retrieval, or make an HTTP request.

**Residual channel (named honestly):** injection could draft an *exfiltration* message — either to a wrong chat, or (subtler) a **hidden payload appended to a message the user legitimately intends to send to the correct recipient**, riding along in a plausible body. Mitigated by: no arbitrary-number sends (targets are `conversation_id`, never a phone number); per-contact + global rate limits; the approval screen rendering recipient with equal prominence to body; masked recipient derived on-device from the signed `recipient_wa_id`; and the **phone-side confusables/bidi guard (C1/INV-DISPLAY)** that blocks the one-tap path on a spoofed body. The final line of defence for body *content* is a human reading the rendered text; a body engineered to defeat both the guard and the reader is the named out-of-scope residual (§11). "Approving while half-asleep" is a real, acknowledged threat, not a joke one.

### 5.4 The honest boundary split (INV-CONTENT precision)

- **Hard technical guarantee:** retrieved content cannot approve writes, send, obtain credentials, create devices/capabilities/policy grants, execute HTTP/SQL, or bypass conversation ACLs.
- **Orchestration guarantee (not cryptographic in V1):** retrieved content *should not* cause additional searches, cross-conversation expansion, or new tool selection — but the server sees only syntactically valid tool calls and has no trusted copy of the user's true intent. Stated as a limitation, not a false security property. Hard retrieval-scope isolation (separately authorised retrieval scopes) is deferred.

### 5.5 Negative surface (asserted absent; CI: `registered_mcp_tools ∩ forbidden_tools == ∅`)

`approve_draft`, `send_prepared_message`, `add_approval_device`, `revoke_device`, `set_policy`, `create_capability`, `raw_fts_query`, `sql_query`, `http_request`, `graph_api_call`, `send_to_number`, `broadcast`, `delete_message`, `export_vault`, `get_credentials`. (Some exist as **CLI/iPhone** operations — never on the model surface.)

### 5.6 Policy engine (evaluated at prepare AND independently re-evaluated at send)

- **P1** free-form only inside a 24h window derived from window-eligible provider evidence; else template required.
- **P2** templates must be `APPROVED` with schema-matched params.
- **P3** recipient conversation must: exist; belong to the selected account; have a resolved wa_id; have `transport = META_CLOUD`; be `write_capable = true`; support the requested action. (Archive/exported/group chats are never send targets.)
- **P4** rate limits: draft preparations/min; pending drafts/conversation; pending drafts global; push notifications/hour; send attempts/hour; successful sends/day. **Stated bound (M4):** prepare/push limits live in `control.db` and are incremented by the draft service, so they are *hard* against a merely-confused model but *soft* against a compromised prepare path (which could skip the increment). The hard backstop against push-spam is that the push is content-free and ignorable, and that no send can occur without a fresh phone signature regardless of how many drafts exist. Send-side limits (P4 evaluated inside the §6.4 transaction) are hard.
- **P5** exactly one recipient per draft (hard).
- **P6** length, attachment count, size, MIME allowlist.
- **P7** approval/sender account binding: the signed action binds `account_id` + `phone_number_id` + `recipient_wa_id`; `whatsvault-meta` verifies the credential it is about to use matches that exact identity. (Protects future multi-account.)

**Re-evaluation at send is not redundant:** a draft prepared 08:58, window closing 08:59, approved 09:00 has a valid signature and unmodified body yet must still be refused `WINDOW_CLOSED`. Time invalidates authority independently of cryptography. All P1–P7 mutable inputs live in `control.db` (INV-SENDPOLICY).

### 5.7 `mark_read` — signed standing capability

Read receipts are external writes (Meta `/messages` with the inbound `wamid`) and are communication. A boolean allowance is too weak. Instead the phone signs a domain-separated `WHATSVAULT-CAPABILITY-V1` grant (`capability_id, device_id, account_id, conversation_id, action=MARK_READ, created_at_ms, expires_at_ms, max_actions, nonce`) via Face ID + Secure Enclave, default finite duration (~30 days). `whatsvault-meta` uses it after verifying signature + device ACTIVE + conversation match + action match + not expired + usage-limit not exceeded; otherwise individual approval is required. The MCP may *use* a capability; it can never **create, extend, renew, or re-scope** one. Revocation from iPhone or CLI. **Typing indicators are a separate action/capability**, never smuggled under read receipts even though Meta shares the endpoint. **Side-channel disclosure (N1):** a standing `MARK_READ` grant makes read-receipt *timing* reflect **automation**, not the user's attention — a contact may see "read 3am" while the user slept. This is a communication side-channel the user explicitly opts into per conversation; it is surfaced at grant time, not buried.

### 5.8 Redaction

MCP never returns a full `wa_id`. Contacts surface as `cnt_` ULID + display name + masked tail. Audit logs actor/tool/args-hash/outcome, never content.

---

## 6. Draft → approval → send (end to end)

### 6.1 Nonce ownership

The **draft service** generates the 32-byte CSPRNG nonce at prepare, stores it `UNIQUE` on `drafts`, binds it into the signed payload. **`whatsvault-meta` consumes it** by inserting into `approval_nonces` inside the same transaction that opens the send attempt. The `UNIQUE` constraint is the replay boundary — not any status field.

### 6.2 States

`DRAFT → PENDING_APPROVAL → APPROVAL_RECEIVED → SENDING → SUBMITTING → SUBMITTED`, with exits `REJECTED | EXPIRED | CANCELLED | FAILED | INDETERMINATE | ABANDONED_INDETERMINATE`. `APPROVAL_RECEIVED` means only "an envelope is persisted," never "authorised to send." `SUBMITTED` means "Meta returned success and the `wamid` is durable" — distinct from the evidence-level `sent` status webhook.

### 6.3 Canonical signed payload — `WHATSVAULT-DRAFT-DECISION-V1`

Length-prefixed binary concatenation (byte-identical in Swift and Python; JSON forbidden here):

```
payload := "WHATSVAULT-DRAFT-DECISION-V1\n" ‖ for each field in fixed order: uint32be(len) ‖ bytes
```

Fixed field order: `version`(uint16be), **`decision`** (`APPROVE`|`REJECT`, ASCII token, near the front — a `REJECT` envelope must be structurally incapable of authorising a send), `draft_id`, `account_id`, `phone_number_id`, `recipient_wa_id`, `body_sha256` (raw 32B), `kind`, `template_id`, `template_params_sha256` (raw 32B or empty), `reply_to_wamid`, `attachments_digest` (raw 32B; empty-list constant defined), `nonce` (raw 32B), `created_at_ms` (uint64be), `expires_at_ms` (uint64be), `device_id`. Absent optionals = zero-length (**never omitted**). Required zero-length rejected.

Field encodings: strings/IDs strict UTF-8, no normalisation; hashes raw bytes (not hex text); timestamps uint64be (8B); version uint16be; enums canonical ASCII. `attachments_digest = SHA256("WHATSVAULT-ATTACHMENTS-V1\n" ‖ per-attachment canonical(ordinal, content_sha256, mime, size[, filename]))` with a defined constant for zero attachments; no dependency on temporary Meta media IDs/URLs. `template_params_sha256` uses a defined canonical param encoding.

Crypto: ECDSA P-256 / SHA-256. **Sign the payload bytes directly** — CryptoKit `signature(for:)` hashes internally; the Python verifier uses `ec.ECDSA(hashes.SHA256())` (not `Prehashed`). Signature transported raw `r‖s` (64B via `rawRepresentation`); Python reconstructs DER via `encode_dss_signature(r, s)`. Domain-separation prefix mandatory. **Replay identity = `device_id + nonce`, never the signature bytes** (ECDSA is randomised).

**WYSIWYS:** the iOS app renders the message from the exact `body_bytes` it fetched and signs *those* bytes — never a re-serialised or re-fetched copy. The security property re: transmission: *`body_bytes` is the canonical UTF-8 representation of the exact logical WhatsApp message content; the app displays those exact bytes after strict UTF-8 decoding, and the sender constructs Meta's message string from that same scalar sequence without normalisation.* (Meta receives JSON, so wire bytes are not literally identical — the **logical content** is what is bound.)

This holds even against a compromised Mac at the *byte* level: swapping the stored body after signing makes the recomputed `SHA256(body_bytes)` disagree with the signed `body_sha256` → `PAYLOAD_CHANGED`, denied. The gap it does **not** close is glyph-level: see the display guard below.

**Display guard (C1 / INV-DISPLAY) — mandatory:** before offering the one-tap *Approve & Send*, the iOS app runs a display-time scan over the decoded body for Unicode confusables (TR39 skeleton), bidirectional control/override characters (U+202A–202E, U+2066–2069, U+200E/200F), and zero-width characters. On any hit it **refuses the one-tap path** and forces a "show escaped / show raw code points" view plus a second explicit confirm. This is display-only and **never alters the signed bytes** (which stay un-normalised per §3.6). It converts "the user misread spoofed glyphs" from a silent bypass into a flagged, two-step decision. The residual — a body that defeats both the guard and a careful reader — is named out-of-scope for V1 (§11).

**Secure Enclave key policy (E2) — mandatory:** the P-256 signing key is generated with access control `[.privateKeyUsage, .biometryCurrentSet]` (biometric-only; re-enrolling the device's biometrics invalidates the key), a **fresh `LAContext` per signature**, and `touchIDAuthenticationAllowableReuseDuration = 0` (no reuse — every approval is a distinct biometric event). No device passcode fallback for signing.

**Cross-language golden tests** are a gate before either sender or app counts as working: ASCII, Persian, mixed, emoji, quotes/newlines, zero-length optionals, max-length, attachment, template, reply, fixed binary nonce/hash. Both `APPROVE` and `REJECT`. `Swift sign → Python verify`, `Python fixture-key sign → Swift verify`, and one-byte / recipient / expiry / decision mutations → verify fails. Production key stays in Secure Enclave; tests use a deterministic fixture keypair.

### 6.4 The permission-to-transmit transaction (`BEGIN IMMEDIATE`, all-or-none)

verify ECDSA over freshly recomputed canonical payload → **`decision == APPROVE`** → device `ACTIVE` **now** (I4b) → `body_sha256` match → `recipient_wa_id` match → account/phone binding (P7) → not expired (Mac clock) → **re-evaluate P1–P7** → `INSERT approval_nonces(nonce)` → `INSERT send_attempts(idempotency_key, state=SUBMITTING, biz_opaque_callback_data="wv1:<atm_id>")` → `COMMIT`. Only then the HTTP POST. **All HTTP-library auto-retries on message POSTs are disabled.**

### 6.5 Expiry

`expires_at_ms` set at prepare, signed by the phone, verified against the **Mac's** clock at send. The phone clock never decides validity. Envelope `created_at_ms` disagreeing with the stored draft → reject.

**Clock integrity (H2) — required:** two security checks depend on the Mac wall-clock — expiry (here) and the P1 24h window. The host must run NTP sync; on a detected clock discontinuity (a jump beyond a small threshold since the last observed tick) the sender **refuses sends and alerts** rather than trusting the new time. Stated bound: a fully compromised host can lie about time, exactly as INV-HARDWARE concedes it can subvert the display; time integrity is therefore a *reduction* of the attack surface, not a guarantee against a compromised host.

### 6.6 Failure matrix (Meta has no idempotency key; no list-sent endpoint)

| Point | State |
|-------|-------|
| Envelope not yet persisted | `PENDING_APPROVAL` |
| Envelope persisted, before send-auth COMMIT | `APPROVAL_RECEIVED` (safe to process again) |
| Crash before send-auth COMMIT | no nonce consumed; safe to reprocess |
| Definite DNS / TCP-connect / TLS failure before request transmission | `FAILED` (never reached Meta) |
| Connection reset/timeout after request may have begun | `INDETERMINATE` |
| Crash any time after COMMIT before definitive HTTP outcome durably recorded | `INDETERMINATE` |
| HTTP 2xx + wamid durable | `SUBMITTED` |
| HTTP 4xx, no wamid | `FAILED` |
| HTTP 5xx | conservatively `INDETERMINATE` unless Meta guarantees non-acceptance for that class |

- **`INDETERMINATE` is never auto-retried.** Surfaced to the human with exactly: *Wait for reconciliation* / *Abandon as unknown* (→ `ABANDONED_INDETERMINATE`, not `FAILED`) / *Prepare a new send (new Face ID, may duplicate)*. The old nonce stays consumed forever.
- **A failed send does not release the nonce.** Failure requires re-prepare + re-approval.
- **Reconciliation:** `biz_opaque_callback_data = "wv1:<atm_id>"` echoed into status webhooks gives deterministic correlation. Exact callback ID or known wamid → automatic. **Recipient + time + conversation → `POSSIBLE_MATCH` only**, surfaced to the human, never auto-resolving state. *(Phase 0 must verify `biz_opaque_callback_data` is still supported and echoed by the direct Cloud API before it is treated as a production invariant.)*
- **Dependency weight (M3):** if the Phase 0 check fails, the `INDETERMINATE` class has **no automatic resolution at all** — every ambiguous send stays human-resolved forever, materially degrading the send UX. This is not a nice-to-have: O2 is a **blocking** Phase 0 question, and its failure requires an explicit fallback decision (e.g. a self-sent correlation marker, or accepting manual-only reconciliation) before Phase 4 ships.

### 6.7 Signed rejection & idempotent envelopes

Rejection uses the same scheme with `decision = REJECT`, so audit can prove the user declined. The phone retransmits the **exact same signed envelope** on lost ACK (`UNIQUE(approval_id)` + `UNIQUE(draft_id, device_id, decision, nonce)`); it never mints a new signature for the same decision. Received N times → persisted once → sender triggered once.

### 6.8 Approval triggers send

A valid approval envelope wakes an internal dispatcher which drives `whatsvault-meta`. The model is **not** in the dispatch path; it cannot strand an approved message, and if the sender is down it recovers by scanning durable envelopes on restart. **ACK failure after local commit is a delivery retry the dedupe ledger absorbs, not an ingest failure.**

**Layering (M2):** the IPC verb `execute_write(signed_action_envelope)` is the *only* external surface of `whatsvault-meta`; internally it routes to the `WhatsAppProvider` method (`send_text`/`send_media`/`send_template`/`mark_read`) selected by the signed action `kind`. The provider interface (§10) is an internal abstraction, not an IPC surface — nothing outside `whatsvault-meta` can call a provider method directly.

### 6.9 Push

Content-free ("one approval waiting"). Best-effort; never authoritative. Missed push degrades to "not seen yet," never "sent without you." App fetches pending drafts on open. Transport pluggable (APNs with paid Apple Developer account; ntfy/Pushover fallback carrying the content-free ping while the app fetches-and-signs).

---

## 7. Ingest failure handling & DLQ

> Governing rule = **INV-ACK**. ACK iff the event reached a durable terminal disposition in `vault.db` (ingested, deduped-as-seen, or quarantined to local DLQ).

### 7.1 Pull cycle

```
lease batch → per event: decrypt → strict schema → classify → BEGIN IMMEDIATE
  → INSERT ingest_events(semantic_event_key)          [UNIQUE]
  → if duplicate: COMMIT (seen)                        → eligible ACK
  → else: normalise → domain rows → reconcile statuses → COMMIT → eligible ACK
→ ACK only events that reached a durable disposition
```

Each event is its own transaction; only committed events are ACKed. A mid-batch crash re-leases the remainder; `UNIQUE(semantic_event_key)` dedupes. `visibility_timeout > worst-case local processing time`; ACK promptly after commit.

### 7.2 Failure taxonomy (retry iff retry could change the outcome)

| Failure | Action |
|---------|--------|
| Duplicate | ledger confirms seen → ACK |
| Valid supported event | commit domain state → ACK |
| Unknown but well-formed | `UNKNOWN_SUPPORTED` + exact decrypted JSON bytes in SQLCipher → ACK |
| Decrypt `KEY_UNAVAILABLE` (Keychain locked) | **TRANSIENT** → retry/backoff, **no ACK** |
| Decrypt `AEAD_AUTH_FAILED_ISOLATED` (key good, one envelope fails) | **POISON** after bounded verify → local DLQ commit → ACK |
| Decrypt `AEAD_AUTH_FAILED_SYSTEMIC` (suddenly all fail — wrong key/rotation bug) | **SYSTEMIC** → circuit-break, **no ACK** |
| Schema-invalid / unparseable | **POISON** → local DLQ commit → ACK |
| DB locked | TRANSIENT → retry, no ACK |
| Disk full | circuit-break, no ACK |
| Network to Cloudflare | no ACK possible → lease expires |
| Retry exhaustion at edge | Cloudflare ciphertext DLQ (alert) |
| Mac offline beyond edge retention | possible irreversible gap → export backfill |

**A poison event is ACKable only after the DLQ write commits** (durability, not classification, gates ACK). Circuit-break **before** burning retries during systemic failure.

### 7.3 Two DLQs

- **Cloudflare ciphertext DLQ** — `max_retries = 100`, `dead_letter_queue = cf-ingest-dlq`. Last-resort ciphertext safety net (without it, retry exhaustion silently deletes at the edge — the local DLQ only holds what we pulled). Edge-DLQ arrival is an **alert**, not a successful local disposition.
- **Local DLQ (`ingest_dlq` in `vault.db`)** — canonical diagnostic store. Holds `event_id_hash`, **ciphertext as received** (never a failed-decrypt partial), and **structured bounded diagnostics only**: `failure_class`, `failure_code`, `pipeline_stage`, `exception_type`, `parser_version`, `crypto_version`, `payload_sha256`, `attempt_count`, optional length-capped `sanitised_detail` (**never** payload excerpts — no raw exception text). Re-drivable: `whatsvault dlq retry` re-runs the pipeline against stored ciphertext after a parser upgrade or Keychain fix; `dlq list/show` inspects. Decrypt failures get bounded retry across restarts before terminal-poison classification (a lock may masquerade as poison).

### 7.4 Retention monitoring (approximate; cannot prove past loss)

14-day retention is a **deployment requirement** (Free tier's 24h is insufficient). Monitor `oldest_message_timestamp_ms` (realtime Queue metrics): 50% → WARNING, 75% → HIGH, 90% → CRITICAL. Recommended metadata-only edge ingress counter (daily `accepted`/`enqueued` tallies, no content) lets `doctor` detect `edge_accepted != local_accounted` even after expiry. Statement kept: *queue monitoring can warn of approaching expiry but cannot prove no previously expired event was lost; export/history backfill remains the recovery mechanism.*

### 7.5 Key rotation ↔ DLQ

Every sealed envelope carries `crypto_version` + `recipient_key_id` (both authenticated as AAD, INV-EDGE-AAD). `whatsvault keys retire <id>` refuses while any of {main edge backlog, edge DLQ, local DLQ unresolved} still reference the key, or requires deliberate rewrap first. Retiring a key with unresolved ciphertext = cryptographic shredding; forbidden.

**Rotation sequence (M5) — ordered, never reordered:** (1) generate a new Mac keypair, add its private half to the Keychain under a new `recipient_key_id`; (2) deploy the new **public** key to the Worker and switch the Worker to seal under the new `recipient_key_id`; (3) both keys now coexist — ingest selects the decryption key by each envelope's `recipient_key_id`, so old in-flight ciphertext still decrypts; (4) wait until all three ciphertext stores show zero references to the old key (or rewrap them); (5) only then `keys retire <old>`. The old private key is never deleted while step (4) is unsatisfied.

### 7.6 Alerting

Content-free push: "⚠️ WhatsVault has N events it couldn't ingest." `whatsvault doctor` reports DLQ depth, oldest unresolved age, circuit-breaker state, retention high-water.

---

## 8. WhatsApp export importer

> Governing principle: **an importer that guesses produces a convincing forgery; an importer that refuses to guess produces an honest archive with gaps. We choose gaps.** (INV-IMPORT.)

Writes **only** to `vault.db` evidence tables — never `control.db`, never windows, never capabilities. A text file is inert and can never create send authority.

### 8.1 Date/locale (unresolvable from the file alone)

WhatsApp writes export timestamps in the exporting phone's locale/timezone and stamps neither. Import **requires** explicit operator `date_format` (`DMY`/`MDY`/`YMD`) and `tz_name`; **refuses** otherwise. Detection is **whole-file validation**, not a per-line heuristic: parse the entire transcript with each candidate parser family, reject families producing invalid dates → 0 candidates = `UNSUPPORTED_FORMAT`; 1 = suggest; >1 = `AMBIGUOUS`, operator chooses. **Suggestion never auto-selects.** Recorded: `date_format`, `time_format`, `parser_family`, `parser_version`, `tz_name`, `tz_basis = explicit_import_setting`. Every imported timestamp carries §3.3 interval width. DST fold/nonexistent-hour cases (§3.3) stop `--dry-run` for operator resolution.

### 8.2 Identity (cleverness fabricates relationships)

Exports show display *names*, never wa_ids. Imported senders are **provisional identities** in `import_participants` (`id, import_batch_id, source_conversation_id, raw_display_name, normalised_display_name, role, linked_contact_id NULL, link_state ∈ UNLINKED|LINKED_EXPLICIT|LINK_REVOKED`). **No auto-link, no fuzzy/Levenshtein matching.** Linking a provisional identity to a real `cnt_` is an explicit, logged, reversible operator action. Two people both saved "Mona", or one contact renamed over time, make name-based merge actively wrong.

### 8.3 Conversation source authority

Imported chats get a `conversation_sources` row `source_kind = manual_export, write_capable = false`. Imported history does **not** permanently condemn a DM to archive-only: the same canonical conversation may later gain a `meta_cloud` source (`write_capable = true`) after explicit resolution. P3 checks the live transport source. **An import source can never confer write authority.** This is also the group path (C2): exported groups are archive-only by design.

### 8.4 Batch provenance is many-to-many (undo safety)

`messages.import_batch_id` is **not** used for ownership. Instead `message_import_observations(batch_id, message_id, source_ordinal, source_start_offset, source_end_offset, source_fingerprint, UNIQUE(batch_id, source_ordinal))`. Two overlapping exports both *observe* the same `msg_`. `whatsvault import undo <bat_>` deletes that batch's observations; a canonical imported message is deleted only if **remaining import observations == 0 AND no provider-backed provenance exists.** Same for imported attachments/system events. Batches observe evidence; they never own shared evidence.

### 8.5 Structural parsing (conservative)

- **Multiline:** a message body runs until the next line matching the (locale-parameterised) timestamp header; the header regex is built from the declared `date_format`, never guessed per line. Genuinely ambiguous boundaries (user content that resembles a header) → preserve source byte offsets/line numbers + parser decision + `parser_version`, flag `AMBIGUOUS_MESSAGE_BOUNDARY` in dry-run; require resolution or import with an explicit ambiguity marker. **Never turn user content into a fake participant message.**
- **System lines:** classified only by known locale-specific rules → `SYSTEM_EVENT`; unknown senderless timestamped → `SYSTEM_EVENT_UNKNOWN`; undecidable → ambiguity, **never** participant attribution. Never classify from English phrases alone. Original line preserved.
- **Media:** `MEDIA_PLACEHOLDER` (`<Media omitted>`) / `FILE_PRESENT` (ZIP has it) / `FILE_NOT_INCLUDED_IN_EXPORT` (export legitimately lacks historical media) / `FILE_REFERENCE_BROKEN`. View-once messages are not exported; exports may include only recent media.
- **Encoding:** strict `UTF-8` / `UTF-8+BOM`; **never `errors="replace"`** (`�` silently changes evidence). Decode failure → `ENCODING_UNSUPPORTED`. RTL/bidi controls preserved in `text_original`, stripped only for the index.

### 8.6 Hostile ZIP handling

Reject path traversal + symlinks; caps on expanded size, file count, compression ratio, per-file size; strict filename sanitisation; MIME sniffing; never execute. Multiple plausible transcript `.txt` → **refuse and ask which one** (no "largest wins").

### 8.7 Source artefact retention & operations

The exact original TXT/ZIP is retained in an encrypted local import-source store (`imports/<bat_>/source.bin` + sha256/size/original_filename/stored_at). `whatsvault import reparse <bat_>` re-runs a dry run from the exact original evidence after a parser upgrade. `--dry-run` reports would-land messages, ambiguous dates, DST cases, unlinked identities, system-line classifications, ambiguous boundaries before any write. Undo never destroys the source artefact unless the operator explicitly requests source deletion.

---

## 9. Implementation phases (each becomes its own plan; each has its own adversarial gate)

| # | Phase | Impl. dependency | Production-activation dependency | Adversarial gate |
|---|-------|------------------|----------------------------------|------------------|
| 0 | Coexistence + Cloud API verification — **findings, not code** | — | external (Meta/BSP) | N/A (verification is the output) |
| 1 | Vault + importer + schema + FTS/search | none | none | parser bombs, malformed/hostile ZIPs, duplicate imports, query/AST abuse, date/DST refusal |
| 2 | Read-only MCP against local data | 1 | none | prompt-injected message content, retrieval boundaries, redaction |
| 3 | Edge relay + queue/DLQ + ingest + **status-event ingestion** | 1 (fixture-driven) | 0 | forged webhook signatures, replay, duplicate delivery, retry, DLQ recovery, key-rotation, retention |
| 4 | iOS approval authority + enrolment + relay + sender + Meta adapter | 0, 1, 3 | 0 | forged/expired/modified approvals, nonce replay, wrong-device keys, double-send races, `decision=REJECT` |
| 5 | Scheduler + templates + delivery/status UX + capabilities | 4 | 0 | stale-draft misfire, capability scope/expiry abuse, template param injection |
| 6 | Assembled-system adversarial gauntlet | 0–5 | — | full campaign across all boundaries |

Phase 0 findings must at minimum confirm/deny: number Coexistence eligibility; whether `smb_message_echoes` fire; whether history sync delivers anything; the exact Business-App→Coexistence onboarding path (do **not** run ordinary `/register` on the number until verified); and whether `biz_opaque_callback_data` is echoed into status webhooks (§6.6).

Normaliser must understand all event families from Phase 3 day one: `MESSAGE_INBOUND`, `MESSAGE_ECHO`, `MESSAGE_STATUS`, `HISTORY_EVENT`, `SYSTEM_EVENT`, `UNKNOWN_SUPPORTED`. Webhook history is not re-queryable — durable capture is a one-shot opportunity.

**Scope honesty (N3):** Phase 1 has zero external *service* dependencies (no Meta, no Cloudflare) and is buildable immediately. It is not, however, free of *unknowns*: the importer's correctness rests on a golden corpus of real multi-platform/locale WhatsApp export formats, which is itself a small research artifact to assemble (§8). "Zero external dependencies" means the build is unblocked, not that there is nothing to discover.

---

## 10. Repository structure

```
whatsvault/
├── apps/
│   ├── cf-webhook/            # TypeScript Cloudflare Worker (edge)
│   ├── ingest/               # pull consumer daemon
│   ├── mcp/                  # loopback MCP server
│   ├── meta/                 # whatsvault-meta (sole Meta credential holder)
│   ├── approval-relay/       # phone-facing over Tunnel+Access
│   └── scheduler/            # APScheduler
├── ios/
│   └── WhatsVaultApproval/   # Swift app (Secure Enclave P-256)
├── core/
│   ├── crypto/               # sealed envelope, canonical encoding, signature verify
│   ├── messages/ contacts/ conversations/
│   ├── search/               # Persian normaliser, AST query compiler
│   ├── policy/               # P1–P7
│   ├── drafts/               # state machine, nonce lifecycle
│   └── ingest/               # classify, dedupe, DLQ, reconciliation
├── providers/
│   ├── base.py               # WhatsAppProvider interface
│   └── meta_cloud.py         # V1 (only production adapter)
├── importers/
│   └── whatsapp_export.py
├── database/
│   ├── vault_models.py control_models.py
│   └── migrations/
├── cli/                      # whatsvault: enrol, revoke, dlq, import, keys, doctor, templates
├── tests/
│   ├── golden/               # cross-language canonical-encoding vectors
│   └── adversarial/          # per-phase gates
└── docs/
    ├── superpowers/specs/2026-08-27-whatsvault-design.md   # this file
    ├── threat-model.md
    └── runbook.md
```

`WhatsAppProvider` interface: `send_text`, `send_media`, `send_template`, `mark_read`, `materialise_media`, `health`. Only `meta_cloud` is a production dependency; Kapso/360dialog remain non-dependencies (INV-PROVIDER).

---

## 11. Threat model, security audit & residuals

**Honesty statement (required, per project doctrine):** this system is **not "fully secure"** — no system is, and any spec claiming so should be distrusted. What follows is the threat model it *does* defend, the boundary it explicitly does *not* cross, and the named residuals a hostile reviewer may attack. Every claim below is falsifiable and maps to a mechanism above.

### 12.1 Trust boundaries

- **TB1 Internet → `cf-webhook`** — untrusted callers; Meta HMAC signature is the only trusted-origin proof.
- **TB2 Worker → Queue → Mac** — Cloudflare is *honest-but-curious infrastructure*: it delivers reliably but must learn nothing (ciphertext only).
- **TB3 Mac process ↔ process (IPC)** — same-host processes with distinct authority; separation is by socket authZ + the signature predicate, not by OS user in V1.
- **TB4 Mac ↔ iPhone** — over Cloudflare Tunnel/Access; content sealed to the device, authority proven by Secure Enclave signature.
- **TB5 MCP ↔ model (ChatGPT)** — the model is **untrusted** for authority and semi-trusted for orchestration (§5.4).
- **TB6 `whatsvault-meta` → Meta** — the only egress of message content to the outside world; gated by signature + policy.

### 12.2 STRIDE summary

| Threat | Where | Defence |
|--------|-------|---------|
| **Spoofing** — forged webhook | TB1 | `X-Hub-Signature-256` HMAC over raw body, constant-time, before parse (§2.2). |
| **Spoofing** — forged approval | TB4 | Secure Enclave P-256 signature over canonical payload; pinned device pubkey (§6). |
| **Spoofing** — rogue relay caller | TB4 | Downstream signature verification makes a spoofed caller powerless; Access JWT validated at origin (X5). |
| **Tampering** — envelope metadata swap/downgrade | TB2 | Header bound as AEAD AAD (INV-EDGE-AAD / T1). |
| **Tampering** — body swapped after approval | TB3/TB6 | `body_sha256` recomputed and matched in the send txn → `PAYLOAD_CHANGED` (§6.4). |
| **Repudiation** — "I never approved that" | TB4 | Every send carries a non-repudiable signed envelope; rejections signed too (§6.7). |
| **Info disclosure** — archive at rest | TB3 | SQLCipher + encrypted attachment blobs, keys in Keychain (INV-ATREST / X1 / I3). |
| **Info disclosure** — draft content via Cloudflare | TB4 | Draft detail sealed to device pubkey (INV-DEVICE-SEAL / I1). |
| **Info disclosure** — media-fetch oracle | TB3 | `materialise_media` caller restricted to ingest; MCP has no meta socket (H1). |
| **DoS** — webhook flood / oversized body | TB1 | Size/method/content-type caps + CF rate limiting (D1). |
| **DoS** — push spam | TB3/TB4 | Prepare/push rate limits (soft, M4) + content-free ignorable push; no send without signature. |
| **DoS** — ZIP bomb / query bomb | import/search | Expansion/ratio/count caps (§8.6); query AST caps (§4.3). |
| **EoP** — model → send | TB5 | No externally-mutating MCP tool; no Meta credential on MCP (§5.2, §5.5). |
| **EoP** — compromised ingest → send | TB3 | Ingest holds no credential; can only call `materialise_media` (H1). |
| **EoP** — injection → authority | TB5 | INV-CONTENT: retrieved content cannot approve, send, widen scope, or select tools (hard for writes). |

### 12.3 Named residuals (the honest wounds — a hostile reviewer should attack here)

- **R1 — Glyph-level body spoofing.** A body that defeats *both* the phone-side confusables/bidi guard (C1/INV-DISPLAY) *and* a careful human reader can be approved for content the user did not intend. Bytes are bound; perception is the last check. **Out of scope for V1**, mechanically mitigated, not eliminated.
- **R2 — Fully compromised Mac.** WhatsVault reduces but cannot eliminate the power of a compromised host: it can subvert the display the user reads, lie about time (H2), or enroll a rogue device (H3). INV-HARDWARE's non-exportable key means a compromised Mac still cannot *forge a signature* or *extract the signing key* — the phone remains the authority — but it can degrade the honesty of what the user is asked to approve. This is the boundary; it is stated, not hidden.
- **R3 — Device enrolment integrity (H3).** Enrolment (QR + mutual challenge) is the highest-trust local operation; a Mac compromised *at enrolment time* could enroll an attacker's key. Enrolment is CLI-only, out-of-band-confirmed, and never reachable from MCP — but its integrity assumes an uncompromised Mac at that moment.
- **R4 — No tamper-evident anchor outside the Mac (N2).** `doctor`, `audit_log`, and ABORT triggers all run on the possibly-compromised host; self-audit by a compromised host proves little. Acceptable for single-user V1. **10/10 delta / IOU:** phone-countersigned audit checkpoints.
- **R5 — Edge-retention loss (C5).** The sealed relay is durable only within the configured 14-day retention; a Mac offline longer loses events irrecoverably at the edge. Monitored, not eliminated; export backfill is the recovery path.
- **R6 — Read-receipt timing side-channel (N1).** A `MARK_READ` capability makes blue-tick timing reflect automation, not attention. Opt-in, surfaced at grant time.
- **R7 — `biz_opaque_callback_data` dependency (M3/O2).** If Phase 0 finds it unsupported, `INDETERMINATE` sends lose automatic reconciliation. Blocking Phase 0 item with a required fallback decision.

### 12.4 Security acceptance gates (per phase, tested — not asserted)

- **Phase 1:** SQLCipher-at-rest verified (no plaintext content on disk incl. attachment blobs); parser/ZIP/query fuzzing; import date/DST refusal.
- **Phase 2:** prompt-injection corpus against MCP (no authority, no scope-widen for writes); redaction (no full wa_id leaks); `registered_mcp_tools ∩ forbidden_tools == ∅`.
- **Phase 3:** forged/replayed/oversized webhooks rejected; AAD tamper (swap `recipient_key_id`/`crypto_version`) → decryption fails; DLQ + key-retire safety; retention alerting.
- **Phase 4:** forged/expired/modified/replayed approvals denied with the exact reason code; `decision=REJECT` cannot send; wrong-device key rejected; confusables/bidi guard blocks the one-tap path on a spoofed body; clock-jump refusal; draft-detail-over-Tunnel is ciphertext.
- **Phase 6:** assembled-system campaign across all TBs, including a red-team pass explicitly targeting R1–R3.

---

## 12. Privacy / legal posture

Meta's Business terms frame the Business Solution as businesses communicating with consumer users; the account also holds ordinary personal conversations. WhatsVault stays **private, single-user, non-marketing, non-broadcast, user-directed only**, and is not turned into an autonomous personal-messaging bot. No auto-sending of replies based on incoming content. C3 (account-risk of putting a personal number on a WABA) is decided deliberately in Phase 0.

---

## 13. Open items & IOUs carried into planning

- **O1** Phase 0 external verifications (§9) — gate production activation, not development.
- **O2** `biz_opaque_callback_data` production support (§6.6, M3) — **blocking** Phase 0; if unsupported, decide the `INDETERMINATE` fallback before Phase 4.
- **O3** APNs vs ntfy/Pushover push transport (§6.9) — pluggable; decide per Apple Developer account availability. Note: even content-free push leaks approval *timing/existence* to the provider (I2).
- **O4** Whether WhatsApp edit events are implemented in V1 or deferred (§3.4) — if deferred, `message_revisions` exists but is unused; evidence is never silently overwritten either way.
- **O5** Semantic embeddings and Finglish search (§4.4) — deferred, slots reserved.
- **O6 (IOU)** Phone-countersigned audit checkpoints — the tamper-evident-anchor-outside-the-Mac hardening for R4/N2. Named, deferred, not silently dropped.
- **O7 (IOU)** OS-user process isolation for `whatsvault-meta`/relay as defence-in-depth beyond socket authZ (H1) — V2 hardening; V1 relies on the signature predicate + socket perms.
- **O8** Exact SQLCipher KDF/cipher parameters and attachment-blob AEAD construction (§2.4) — pin at build.
