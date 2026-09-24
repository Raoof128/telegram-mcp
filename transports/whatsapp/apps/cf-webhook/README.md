# cf-webhook Worker — STRUCTURED, Phase-0-gated (not built/run in CI)

This is the Cloudflare Worker contract for the sealed edge relay. It is **activated
only after Phase 0** (V4/V7/V14 + a Miniflare WebCrypto probe). The Python ingest
core (`apps/ingest`, `whatsvault.crypto.sealed`, `whatsvault.ingest.*`) is fully
built and tested against fakes; this Worker is the remaining provider-facing edge.

## Contract (must match the Python side)
- Verify Meta `X-Hub-Signature-256` HMAC over the **raw** body, constant-time, **before** parse; GET handshake validates `verify_token`; POST-only; `Content-Type` allowlist; body ≤ 1 MB.
- Seal with WebCrypto **matching `whatsvault.crypto.sealed`**: `X25519 → HKDF-SHA256 → AES-256-GCM`, envelope `MAGIC(WVE1)||env_ver||alg||crypto_version||recipient_key_id(4be)||event_id_hash(32)||ephemeral_pub(32)||nonce(12)||ct_len(4be)||ct‖tag`, AAD = header prefix through `event_id_hash`. **Never log plaintext.**
- **Build gate:** a Miniflare/`wrangler dev` probe must confirm Workers WebCrypto exposes X25519 `deriveBits` + HKDF + AES-GCM. If X25519 is unavailable, fall back to an ECDH **P-256** sealed envelope (add a second `algorithm_id`; the Python `sealed.open_sealed` gains the P-256 branch). **Cross-vector required:** TS-seal → `tests/golden/sealed_vectors.json` open in Python (both directions).

## Ledger #3 — oversized ciphertext spills to R2 (normal path stays Queue)
- Cloudflare Queues cap a message at ~128 KB; the Worker admits ≤ 1 MB. Normal webhooks are single-digit KB → enqueue the sealed body **inline**.
- If the sealed body > 128 KB: store the ciphertext as an **encrypted R2 object** and enqueue only a pointer `{r2_object_id, ciphertext_sha256, recipient_key_id, crypto_version, event_id_hash}`. The Mac consumer fetches the object, verifies `ciphertext_sha256`, commits, ACKs, then deletes the R2 object. Requires an R2 lifecycle/orphan-cleanup policy + scoped R2 credentials. **Never route normal traffic through R2.**

## Ledger #4 — Queue/DLQ configuration
- Retry/DLQ config belongs to the **consumer**, not the producer binding. An edge DLQ with **no active consumer retains only ~4 days**, so define an explicit edge-DLQ **drainer/consumer** to reach the 14-day net (confirm exact semantics at Phase 0 V14). `wrangler.jsonc`: producer binding + a consumer with `max_retries` + `dead_letter_queue`.

## Files (to be authored at activation)
`src/index.ts` (HMAC verify + seal + enqueue/R2-spill), `wrangler.jsonc`, `package.json`, `test/` (vitest + Miniflare: forged-sig rejected, oversized→R2, TS-seal→Py-open vector).
