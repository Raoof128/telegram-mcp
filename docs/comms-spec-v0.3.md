# Comms spec v0.3: owner_full_admin, the universal administrative bridge

**Status:** Normative. Adopted by the owner on 2026-09-24 (Australia/Sydney).

**Source:** the owner's proposal, kept verbatim at [`docs/provenance/comms-v0.3-proposal.md`](provenance/comms-v0.3-proposal.md). This document calls it **P**; "P §n" means section *n* of that proposal.

**Design:** [`docs/superpowers/specs/2026-09-24-comms-v0.3-design.md`](superpowers/specs/2026-09-24-comms-v0.3-design.md). It holds the mechanisms, measurements and proof obligations.

## Controlling statement

Comms is an owner-controlled communications layer for LLMs over Telegram and WhatsApp.

- **P §0–§91 are normative**, except where amendment A1–A40 below changes them. An amendment always wins over the proposal's text.
- **The default and only v0.3 profile is `owner_full_admin`.** It grants the full typed read and write surface.
- **There is no Comms approval ceremony.** No Touch ID, consent challenge, second confirmation or daemon prompt exists anywhere.

## Precedence

| Document | Authority |
|---|---|
| **comms-spec-v0.3** (this document plus P) | Governs everything it speaks to. |
| **comms-spec-v0.2** | Normative only for retained semantics that v0.3 does not restate: owner-direct authority, receipt versioning (v1/v2 verified by `proof_version`), and the 5b-3 tombstones. |
| **Telegram MCP spec v0.1.10** | Historical verification authority only, for retained v1/v2 Telegram receipts and the legacy audit chain. |
| **WhatsVault spec** | Historical verification and provenance authority for imported WhatsVault records, until v0.3 semantics replace them. |

A migrated object is governed by the highest document in this table that speaks to it.

## Decisions

| # | Decision |
|---|---|
| D1 | One design, one plan, one branch (`comms-v0.3`), one merge. The work is split into Parts A–D, each ending on a green gate with an annotated local tag. A recorded checkpoint is never rebased. |
| D2 | Campaign snapshots reach the audit chain only as a keyed commitment (A12). |
| D3 | A Telegram bot token, the owner's Telegram user session and Meta WhatsApp Cloud API credentials are all available. Each adapter has an owner-run live acceptance step. |
| D4 | `comms mcp` is the single AI surface. The Telegram MCP server and WhatsVault's `apps/mcp` are retired. |
| D5 | `owner_full_admin` exactly as P defines it: the full typed read and write surface, with no budgets, receipts or project policy on reads. Host-side permission UX is the only prompt layer. |

## The trust boundary (D5)

`owner_full_admin` intentionally grants the connected MCP host authority to invoke typed write tools. Comms does not require an independent human-presence ceremony.

**Claim boundary.** Comms does **not** guarantee that a model will perform only the writes that match the owner's semantic intent. Content retrieved from Telegram or WhatsApp is untrusted, and a model may be misled by it. The defences against model or tool misuse are:

- typed tools, with no raw-RPC tool (P §37);
- explicit opaque targets, and ambiguity refusal (`AMBIGUOUS_TARGET`, P §41);
- capability checks, with the provider's response as final authority (A25);
- host permission UX and honest annotations (P §3);
- request-id idempotency and durable operation records (A27, A28);
- audit evidence on an anchored chain (A7, A8).

Two further limits are not claimed:

- **Cross-client duplicates.** Two independently issued, semantically identical instructions from different clients are not detected as duplicates. Only one operation identity, `(authenticated_client, request_id)`, is protected.
- **Tool selection.** A model's choice of tool is acceptance evidence, never a proven property.

## Amendments to P

### Part A: the constitutional cutover

- **A1. Retired, not deleted.** "Retired" means tombstoned and never reassigned. Historical data and verification code are kept.
  - The following are **retired from authorization and execution**: the 10 Telegram MCP tools and their contracts; WhatsVault's `apps/mcp` and its 6 tool names; disclosure-on-read (the coordinator, exposure budgets and ledger, receipt issuance); and project, grant and scope policy as authority.
  - Their schemas and rows are **not dropped at cutover**. They remain for legacy receipt verification, legacy audit interpretation, retention and diagnostics. 5c-level retention purges them only when they are independently eligible.
- **A2. What is tombstoned.** Tombstones cover protocol identifiers only:
  - the 16 tool names;
  - `tg-mcp-policy-bundle/v1` and `tg-mcp-policy-signature/v1`;
  - the `tgml1` lease format and its seeds (A33).
  
  The legacy audit domains are not tombstoned. They are **closed to new appends** once `SEALED` (A6), and they remain valid for verification. Implementation locations are covered by architecture guards instead: no module may register the retired MCP surfaces, and no production composition may reach them.
- **A3. Historical verification stands alone.** v1/v2 receipt verification and legacy chain verification need only retained canonical fields and historical keys, never the retired policy engine, coordinator or reservation path.
- **A4. Retired names cannot be called.** For each of the 16 retired names, `tools/list` does not advertise it, **and** a direct `tools/call` returns `TOOL_NOT_FOUND` with no database mutation and no provider access. Dispatch is a closed allowlist of `comms_*` names.
- **A5. The supersession manifest.** `docs/comms-v0.3-supersession.json`, parsed by the single strict decoder, lists every superseded identifier, retired authority, retained verification capability and the new authority. A test checks the spec, the tombstones and production composition against it.
- **A6. The cutover protocol.** The cutover runs in this order:
  1. Enter `CUTOVER`.
  2. Disable legacy ingress, refuse retired calls, and drain in-flight legacy work.
  3. Verify the legacy chain and its anchor.
  4. Append the final marker and write the signed final checkpoint (reason `V0_3_CUTOVER`).
  5. Refresh the legacy anchor. The legacy chain is now `SEALED`, and legacy append is structurally impossible.
  6. Write the comms genesis, then `system.audit_cutover`.
  7. Refresh the comms anchor.
  8. `COMPLETE`.

  One durable `cut_` ref is bound into both sides. On replay, an existing seal or genesis for that ref is reused, never recreated, and the same ref with a conflicting digest fails closed. A comms genesis without its matching legacy seal is an **integrity failure** whenever it is observed.
- **A7. The comms chain.** One parameterised chain engine in `comms.core.audit` serves two profiles:
  - **legacy:** the existing Telegram domains, verify only once `SEALED`;
  - **comms:** new domain `comms-audit-chain/v1\0` and its own genesis domain.

  An `audit_lineage` record binds the cutover. It is exempt from ordinary retention, and its digest is part of the comms genesis. `audit verify --all` walks legacy → lineage → comms.
- **A8. The external anchor.** The comms chain has an authenticated head anchor outside SQLite, called `comms/audit-head-anchor`. While `audit_integrity_degraded` is latched:
  - reads and context MAY continue;
  - no new provider mutation may pass `PREPARED → IN_FLIGHT`, no campaign delivery may be claimed, and no retention, import or rotation mutation may begin;
  - completing and recovering work already `IN_FLIGHT`, reconciling provider events, `audit verify`, `audit repair` and `doctor` remain available.

  If a provider outcome commits but the anchor refresh then fails, the response carries the `op_` ref, the known provider outcome and `AUDIT_INTEGRITY_DEGRADED`. It is never an ordinary success or an ordinary failure.

### Part B: durability, audit, keys, retention, recovery, backup

- **A9. Keys are purpose-separated.** Every key lives in a versioned immutable slot with an SQL active pointer. Rotation is staged, proved, then activated: write the new version and fsync, prove it, switch the pointer in one transaction, then reload. The inventory, its purposes and its rules are listed in the design, §B.4.
- **A10. HMAC keys have no public half.** Retired `principal-key` and `privacy-key` secrets are either kept while a proven historical dependency needs them, or destroyed after migration plus proof of that dependency. The retired signers (`disclosure-key`, consent keys) keep only the public material that historical verification requires.
- **A11. Backup signer states.** A backup signer is `ACTIVE`, `TRUSTED_RETIRED`, `VERIFICATION_ONLY` or `REVOKED`. A compromised signer keeps its public key for forensic verification but loses import trust.
- **A12. The campaign commitment.** It is computed in two steps:

  ```text
  snapshot_digest     = SHA256("comms-campaign-snapshot/v1\0" ‖ JCS(§5.4 preimage))   (stored encrypted, as today)
  campaign_commitment = HMAC-SHA256(campaign-commit-key,
                                    "comms-campaign-commit/v1\0" ‖ generation_ref ‖ snapshot_digest)
  ```

  Only `campaign_commitment`, its key ID, `generation_ref` and safe metadata reach the chain. Redacting a campaign's body never prevents re-verifying its commitment.
- **A13. Provider credentials** are staged in a versioned Keychain item, proved with a live identity or capability check, then activated atomically; the old slot is retired, and any provider-side revoke is a runbook step. A candidate that fails leaves the working credential active.
- **A14. The `comms.db` rekey** runs: stage the key, `PRAGMA rekey`, close, reopen verified with the new key, switch the pointer, destroy the old key. Every boundary has an explicit crash-recovery rule.
- **A15. Retention.**
  - **Chain truncation** happens only at the latest verified checkpoint at or before the retention cutoff.
  - **Campaign body retention** covers every body-bearing column: `generations.content`, `delivery_jobs.payload`, and any frozen render or content blob. Directory identities have their own lifecycle rule.
  - **Jobs:** unresolved jobs and attempts are never purged. Completed job metadata is kept for the life of v0.3, deliberately.
  - **Cryptographic material** is kept per purpose.
- **A16. The journal and the chain are bound.** Each `campaign_events` row carries `event_digest`. The chain event, of kind `campaign_event` with `subject_ref = event_ref` and `subject_digest = event_digest`, is appended in the same transaction.
- **A17. Backup.**
  - **`comms-backup/v1` contents:** the directory (identities are allowed *inside the ciphertext only*), audiences, locations, metadata-only campaign definitions, and non-secret settings.
  - **Excluded:** bodies, generations, payloads, attempts, provider events and secrets.
  - **Binding:** the backup is bound to the installation and the present provider-account set (Telegram user, Telegram bot, and the WhatsApp business phone).
  - **Import:** the diff is staged against a `base_digest`, the base is rechecked at commit, and a stale base refuses. A missing or replaced provider is an explicit staged incompatibility.
  - **Signature:** the domain is `comms-backup-signature/v1`.

### Part C: provider adapters

- **A18. Adapter contracts.** The machine-readable `ADAPTER_CONTRACTS` declares:
  - `telegram_bot` and `telegram_user`: `{delivery, capability, admin, context}`;
  - `whatsapp_cloud`: `{delivery, capability, admin}`;
  - `whatsapp_webhooks`: `{inbound_context, provider_updates}`.
- **A19. Outcome classification uses named provider cases only.**
  - `FAILED_TRANSIENT` requires a documented rejection before acceptance, such as a Bot API response with `ok=false`, `error_code=429` and `retry_after`, or an MTProto `FLOOD_WAIT_X`. The Meta mapping is an explicit per-code table.
  - Timeouts after writing, resets, `5xx` responses, malformed responses and unknown codes are `OUTCOME_UNKNOWN`.
- **A20. MTProto idempotency.**
  - User sends carry `random_id = signed_nonzero_u64(SHA256("comms-mtproto-random-id/v1\0" ‖ idempotency_key))`.
  - When a send's outcome is ambiguous, `deliver()` may reissue the identical request once, within a short bound. `RANDOM_ID_DUPLICATE` proves the message was accepted.
  - Reconciliation ends as `ACCEPTED` or `OUTCOME_UNKNOWN`, with at most one provider-visible message effect.
  - The Bot API and the Cloud API have no idempotency key.
- **A21. No hidden retries.** The single Telethon client runs with `request_retries=0` and `flood_sleep_threshold=0`, and every retry is an explicit Comms decision. Reconnecting never replays an in-flight RPC.
- **A22. The 24-hour window.** Webhook ingestion mirrors `last_customer_message_at`, together with its observation time and source event, into state Comms owns. `prepare()` and `still_valid()` stay pure (5b-4 S3). Missing or uncertain window state means the template path.
- **A23. Template binding.** The template name, language, component schema version and parameter digests are frozen into the prepared payload. If the template becomes unavailable, `still_valid` returns false and the job is skipped. Another template is never swapped in.
- **A24. Update modes.** The Bot API adapter uses `getUpdates` long polling, or a webhook, never both. The user adapter uses the update stream of the single Telethon session.
- **A25. Capability snapshots are advisory.** They exist to optimise and explain. The provider's response to a mutation is the final authority.
- **A26. Egress.**
  - The Bot API talks only to `https://api.telegram.org`, and the Graph API only to `https://graph.facebook.com`.
  - Media moves by provider media ID. The only downloader accepts Meta-issued HTTPS URLs on Meta host suffixes, follows no redirects off those hosts, and is bounded in bytes, content type and time. No MCP-supplied URL is ever fetched.
  - MTProto sockets are opened only by Telethon's own machinery, and no application argument chooses the host, port or data centre.

### Part D: the unified surface

- **A27. Operation semantics.** `OperationSemantics{retry_class, idempotency_strategy, ambiguity_policy}` is a table indexed by semantic operation and transport. It covers `SET_STATE` (generally idempotent), `CREATE` (non-idempotent) and `MESSAGE_SEND` (idempotent only over MTProto). Conformance tests pin every entry.
- **A28. Request IDs.** Every mutating MCP tool requires a bounded opaque `request_id`. Comms stores `(authenticated_client, request_id, request_digest, operation_ref)` under `UNIQUE(authenticated_client, request_id)`:
  - the same ID with the same digest returns the existing operation;
  - the same ID with a different digest returns `REQUEST_ID_REUSE`.

  The CLI generates the ID itself and prints the `req_` and `op_` refs.
- **A29. One tool catalog.** One `TOOL_CATALOG` generates `tools/list`, the dispatch allowlist, the input and output schemas, the annotations and the documentation table. The catalog is static and ordered: capability state is answered at call time, never by hiding tools. Pinned: the tool count, the ordered names, each tool's schema digest, and an overall catalog digest.
- **A30. `ctx_` handles** are short-lived and bound to the client, the owner, the `security_epoch`, the query digest, the target, the actor, the source snapshot and an expiry. They are never bearer capabilities.
- **A31. Refs.** `rcp_` is a person, `dst_` a delivery destination, and `grp_` a group object mapped 1:1 to a group-like `dst_`. There is no `usr_` ref in v0.3.
- **A32. Untrusted content.** No write tool accepts retrieved text, or a `ctx_` handle, as authority. Mutations take explicit structured refs only.
- **A33. `cml1` local leases** use the domain `comms-local-lease/v1\0`, audience `comms-loopback`, and fresh client seeds. `tgml1` is retired at cutover.
- **A34. stdio is a proxy.** `comms mcp --stdio` is an unprivileged proxy that holds MCP framing, schemas and client authentication, and connects to the single daemon over authenticated local IPC. It holds no provider secret, Telethon session, database or key.
- **A35. Remote OAuth.** Remote MCP uses OAuth 2.1 with PKCE `S256` and protected-resource and authorization-server metadata. Access tokens are validated on issuer, subject (exactly the owner), audience, resource, expiry and scope (`comms.full_admin`), and nothing unknown is ever upgraded.
- **A36. Two public ingresses.**
  - **`comms-mcp-tunnel`:** `/mcp`, OAuth only.
  - **`comms-webhook-tunnel`:** `/webhooks/meta` only. Each request's raw bytes are bounded, the `X-Hub-Signature-256` HMAC is checked in constant time before parsing, and content type, deadline and rate are bounded.

  A webhook request has no route to MCP dispatch.
- **A37. One service layer.** MCP handlers, CLI handlers and the maintenance runner all call one typed service layer, and none of them writes SQLite or calls an adapter directly. The operator-only commands (keys, audit repair, cutover, credentials) are never MCP tools. `telegram-mcp` remains only as a CLI alias, and `telegram-mcp serve` refuses.
- **A38. The secret inventory** covers:
  - the `comms.db` key;
  - the Telegram session material;
  - the bot token;
  - the Meta access token, app secret and webhook secret;
  - the audit, checkpoint, campaign-commit, cursor and backup keys;
  - the `cml1` client seeds;
  - OAuth signing, refresh and client-registration material;
  - TLS private keys, if Comms terminates TLS.

  All of it lives in the daemon's credential boundary. None of it ever reaches argv, env, MCP, logs or the repository.

### Proof and release

- **A39. Spec and plan conflicts.** A detected contradiction between the plan and this spec stops the task. The spec stays authoritative; the plan or the spec is then amended explicitly, the ruling is recorded in `docs/verification/comms-v0.3-rulings.md`, and the affected gate reruns. No work proceeds under a known contradiction.
- **A40. Release is separate from merge.** A green automatic gate is **not** a production release. Comms v0.3 receives its release designation only when the owner-run P §88 acceptance evidence is complete for Telegram (bot and user), Meta, ChatGPT, Codex and Claude Code.

## Tombstones added in v0.3

The 10 Telegram MCP tool names, the 6 WhatsVault MCP tool names, `tgml1`, `tg-mcp-policy-bundle/v1` and `tg-mcp-policy-signature/v1`. The legacy audit domains `telegram-mcp-audit-v1` and `telegram-mcp-audit-genesis-v1` are closed to new appends and remain valid for verification. `tests/security/test_tombstones.py` counts each one by name. None is ever reassigned.
