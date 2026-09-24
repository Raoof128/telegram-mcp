# Architecture

This document explains how WhatsVault is put together and, more importantly, why
each boundary sits where it does. The authoritative source is the
[design specification](internal/specs/2026-08-27-whatsvault-design.md); this is
the orientation guide.

## The problem being solved

Two things people reasonably want are in tension:

1. *"Let an AI assistant help me with my messages."*
2. *"Do not give an AI assistant the ability to message people as me."*

Most integrations resolve this by trusting the model. WhatsVault resolves it
structurally: the assistant is given a read surface with no write verb on it at
all, and the ability to send is placed on a channel the model has no path to.

## The central design decision

A naive design exposes `prepare_message` and `send_message` and calls the pair a
safety boundary. It is not one — a model can call both in a single turn, and a
prompt-injected model will. Whatever sits between them is a speed bump.

So authorisation is moved off the machine entirely:

```
model  ──prepare──▶  immutable draft (nonce, expiry, exact bytes)
                            │
                            │  the model's involvement ENDS here
                            ▼
                     iPhone approval app
                     Face ID ▶ Secure Enclave P-256 signs the exact bytes
                            │
                            ▼
                     signed envelope ──▶ sender verifies ──▶ WhatsApp
```

The Mac holds **verification** keys only. There is no code path — through the
MCP, the CLI, the scheduler, or the database — that produces an approval. This is
**INV-APPROVAL** and **INV-HARDWARE**, and the honest strength claim is stated in
the spec: the signing key is hardware-backed and biometrically gated, *not* that a
compromised root account is powerless.

## Components

| Component | Holds Meta token | Holds vault key | Can approve |
|---|---|---|---|
| `whatsvault-meta` | **yes — the only one** | yes | no |
| `mcp` | no | yes | no |
| `ingest` | no | yes | no |
| `scheduler` | no | yes | no |
| `dispatcher` | no | yes | no |
| `cli` | no | yes | no |

`ops/topology.py` encodes this table and `check_invariants()` asserts exactly one
process holds the Meta token and that no process can approve. It is a test, not a
diagram — a refactor that violates it fails CI.

### Storage

Two SQLCipher databases, both encrypted under Keychain-held keys:

- **`vault.db`** — evidence. Messages, contacts, conversations, attachments, the
  FTS5 search index, and the import provenance chain. Append-mostly, with triggers
  enforcing immutability of evidence fields.
- **`control.db`** — control plane. Drafts, approvals, nonces, send attempts,
  device enrolment, the 24-hour window projection, scheduled jobs, and the
  append-only audit log.

The split is deliberate: an import can write evidence but **can never** mutate
control state, so an imported timestamp cannot reopen a send window
(**INV-IMPORT**).

### Ingest

WhatsApp webhooks reach a Cloudflare Worker that seals each payload to a public
key whose private half never leaves the Mac's Keychain, then queues the
ciphertext. The Mac pulls, decrypts locally, and fans out.

Cloudflare therefore persists **ciphertext only** (**INV-CIPHERTEXT**), and every
envelope authenticates its own header as AEAD associated data, so metadata cannot
be swapped or downgraded without failing the GCM tag (**INV-EDGE-AAD**).

Acknowledgement happens only after durable local disposition — ingested, deduped,
or quarantined to the local DLQ. ACK means "accounted for", never "went well"
(**INV-ACK**). Decrypt failures are classified by *key health*: an isolated AEAD
failure is poison and goes to the DLQ, while a systemic one trips a circuit
breaker rather than quarantining a stream of messages that are merely undecryptable
because a key is wrong.

### Search

FTS5 over a normalised representation, tuned for mixed English/Persian text
(ZWNJ handling, Arabic/Persian character folding). The index is explicitly
**disposable and derived**: it may improve recall but can never alter evidence,
deduplication identity, quotations, or display (**INV-SEARCH**). It can be dropped
and rebuilt without loss.

### Time

Imported timestamps are modelled as **uncertainty intervals**, not instants. A
line in a WhatsApp export says `14:32` in some local zone with unknown seconds;
representing that as an exact UTC instant would fabricate precision. Each message
carries `ts_lower_ms`, `ts_upper_ms_exclusive`, and a precision, and DST-ambiguous
or skipped local times are classified rather than silently resolved.

## The MCP boundary

The read surface is the part most exposed to an adversary, because the adversary
may be the *content itself*: a message body is attacker-controlled text that the
model will read.

Four defences, in order of strength:

1. **No write verb exists.** Not gated — absent. CI asserts the registered tool
   set is disjoint from a named forbidden set, checked against plain module
   constants so the guarantee survives SDK churn.
2. **A server-side ACL.** Conversations marked `LOCAL_ONLY` are excluded in SQL,
   set only from the CLI or phone. A hard fence for explicitly-marked chats.
3. **Redaction.** No response contains a full phone number. Contacts surface as an
   opaque id plus a masked tail, and identifiers that *encode* a number — such as
   a `wamid`, which base64-decodes to the counterparty's E.164 — are replaced with
   opaque, correlatable handles.
4. **Untrusted wrapping.** Every WhatsApp-originated string is returned inside a
   labelled wrapper marking it attacker-controllable data rather than instruction.

**Where the honesty is required:** INV-CONTENT is *hard* for writes — no message
can create authority, because no write verb exists to be reached. For **retrieval
scope** it is an orchestration property, not a cryptographic one: the server
cannot stop a model from *choosing* to call `search` again with a wider query. The
`LOCAL_ONLY` fence is the real boundary; the rest is stated as a limitation rather
than papered over.

Transport is Streamable HTTP bound to loopback, with a bearer token from the
Keychain checked in ASGI middleware — because `127.0.0.1` is not an auth boundary
when any local process runs as the same user. DNS-rebinding protection pins the
`Host` header, since a hostile page can otherwise drive a browser at localhost.

### Publishing the read surface

ChatGPT's connector dialog offers OAuth, No Authentication or Mixed. A static
bearer token satisfies none of them, so reaching the vault from ChatGPT means
either publishing it with no authentication — out of the question — or running an
authorization server. Setting `WHATSVAULT_PUBLIC_URL` mounts one.

This changes what the auth layer is *for*. On loopback, reaching the port already
meant running as the user, and the token was a second lock on a door only you
could stand at. Published, the authorization server **is** the boundary. Three
decisions follow from that:

- **It is opt-in and off by default.** With the variable unset, `/oauth/*` and
  `/.well-known/*` return `404` and no token can be issued. A local deployment
  gains no attack surface from a feature it is not using.
- **The unauthenticated region is safe by ownership, not by exemption.** An
  authorization server must answer without a token. The router answers every path
  under those prefixes *itself* and forwards none, so no request shape — including
  `..` and percent-encoded traversal — reaches a tool.
- **The consent page never accepts a secret.** It displays a code and polls; the
  grant is made from a terminal with `whatsvault oauth-approve`. A public form
  asking for a password is a phishing and brute-force target, and this project
  already holds that approval belongs on a channel the requester cannot reach —
  the same reasoning that puts sending on the Secure Enclave.

The bind address does not change: the server still listens on `127.0.0.1` and is
reached through an outbound tunnel, so nothing is exposed by binding. The
published hostname is added to the `Host` pin rather than the pin being relaxed.

What the flow enforces: PKCE `S256` only, exact-match https redirect URIs,
single-use 60-second authorization codes bound to client and redirect URI,
rotating refresh tokens whose reuse revokes the whole grant family, SHA-256
hashing of every code and token at rest, and a single issuable scope that the
schema itself `CHECK`s. Approval, issuance, revocation and reuse detection are
written to the same append-only audit log as tool calls.

**The honest limit:** publishing discloses retrieved content to whichever
assistant you connect. The read-only surface, the redaction rules and the
`LOCAL_ONLY` fence all still apply — but they bound what the *model* can do, not
what the *provider* retains. That trade is the operator's to make deliberately.

## The approval chain

1. **Prepare** — an immutable draft with a 32-byte nonce and an expiry. The shared
   policy engine runs here, and again authoritatively at send.
2. **Fetch** — the phone retrieves draft detail sealed to the enrolled device's
   public key. Cloudflare Tunnel carries ciphertext; Access provides
   authentication and DoS protection, never confidentiality the design relies on
   (**INV-DEVICE-SEAL**).
3. **Display guard** — the signature binds *bytes*, but the human approves
   *rendered glyphs*. Bidi overrides, zero-width characters, and confusables are
   flagged before a one-tap approval is offered (**INV-DISPLAY**).
4. **Sign** — Face ID gates a Secure Enclave P-256 signature over a canonical
   `WHATSVAULT-DRAFT-DECISION-V1` structure binding recipient, body hash, nonce,
   and expiry.
5. **Send** — verified inside the same transaction that consumes the nonce, so a
   replay cannot race a send (**INV-SENDPOLICY**). A crash between commit and
   confirmation resolves to `INDETERMINATE`, never a blind resend.

## Operations

Four launchd units, all running under `KeepAlive={SuccessfulExit: false}` — crash
restart preserved, while a clean exit stays stopped. That distinction matters: a
daemon whose dependency is unavailable reports the blocker once and exits 0,
rather than being restarted forever and presenting an unbuilt component as a
running service.

Logging goes through `ops.structlog`, which **refuses** any field that could carry
message content. A log line cannot leak a body even by mistake.

## What is deliberately not built

The write path is implemented and tested locally but inert, because it depends on
external facts that have not been verified. Those are tracked in the
[Phase-0 verification record](internal/findings/2026-08-27-phase0-verification.md),
where each assumption carries the primary-source quote that supports it and each
open gate is named.

This is the project's central working discipline: a plausible reading of a
vendor's documentation is not evidence, and nothing is marked done on the strength
of one.
