# MCP Reference

The WhatsVault MCP server exposes a **read-only** view of your vault over
Streamable HTTP on loopback.

- **Endpoint:** `http://127.0.0.1:8765/mcp`
- **Transport:** Streamable HTTP (MCP SDK 2.x)
- **Auth:** `Authorization: Bearer <token>`, token held in the macOS Keychain

## Connecting

```bash
whatsvault mcp-provision --reveal   # mints the token; prints it once
whatsvault-mcp                      # serves on 127.0.0.1:8765
```

`127.0.0.1` is **not** an authentication boundary — any local process running as
you can reach it — so every request is authenticated regardless of origin.
Requests are rejected with `401` when the header is absent, malformed, carries the
wrong scheme, or appears more than once (ambiguous credentials fail closed). A
forged `Host` header is rejected with `421`.

To reach the server from a hosted assistant, use an outbound tunnel; do not bind
it publicly. See **ChatGPT** below.

## Public deployment (ChatGPT)

Setting `WHATSVAULT_PUBLIC_URL` to the https origin the server is published at
mounts an OAuth 2.1 authorization server and pins the transport's Host check to
that hostname. Unset — the default, and how the local connectors talk to this
vault — none of it exists and a static bearer token is the only mechanism.

| Endpoint | Purpose |
|---|---|
| `/.well-known/oauth-protected-resource` | RFC 9728 metadata; the `401` on `/mcp` points here |
| `/.well-known/oauth-authorization-server` | RFC 8414 metadata |
| `POST /oauth/register` | RFC 7591 dynamic client registration |
| `GET /oauth/authorize` | consent page |
| `GET /oauth/poll` | the page waits here for approval |
| `POST /oauth/token` | code exchange and refresh |

**The consent page never asks for a secret.** It shows a code and waits; the
operator grants it from a terminal on the machine holding the vault:

```bash
whatsvault oauth-pending                 # what is waiting
whatsvault oauth-approve --code ABCDE-FGHIJ
whatsvault oauth-revoke                  # kill every token; the off switch
```

A public form accepting a password would be a phishing and brute-force target,
and approval in this project belongs on a channel the requester cannot reach —
the same reason sending needs the phone's Secure Enclave.

What is enforced: PKCE `S256` only (`plain` is neither accepted nor advertised),
exact-match https redirect URIs, single-use 60-second authorization codes bound
to client and redirect URI, rotating refresh tokens, and SHA-256 hashing of every
code and token at rest. The only scope issued is `whatsvault.read`, and the
schema CHECKs it.

## ChatGPT

ChatGPT's connector dialog offers OAuth, No Authentication, and Mixed — a static
bearer token fits none of them, so this server cannot be added as a plain HTTPS
connector without building an OAuth 2.1 authorization server in front of it.

Use OpenAI's [Secure MCP Tunnel](https://developers.openai.com/api/docs/guides/secure-mcp-tunnels)
instead. `tunnel-client` makes only outbound HTTPS connections to OpenAI's
control plane and forwards JSON-RPC to `127.0.0.1:8765`, so the vault is never
published and no inbound firewall rule is opened. In ChatGPT the connector is
added with **Connection: Tunnel** rather than a URL, which sidesteps the auth
dropdown entirely; the bearer token is carried by `mcp.extra_headers`.

```bash
brew install openai/tools/tunnel-client
tunnel-client doctor --profile whatsvault
tunnel-client run   --profile whatsvault
```

Set `discovery_extra_headers` as well as `extra_headers`: the client probes
`/.well-known/*` before it will report ready.

Understand what this changes. Locally, message content never leaves the machine.
Through a hosted assistant it reaches OpenAI's infrastructure on every tool call.
The read-only surface, the redaction rules and the `LOCAL_ONLY` fence all still
apply — the model still cannot send, and still never sees a phone number — but
the content of anything it *can* read is now handled by a third party. Fence the
conversations that should never leave first:

```bash
whatsvault mcp-visibility --conversation-id cnv_… --visibility LOCAL_ONLY
```

## Tools

All six are annotated `readOnlyHint: true`, `openWorldHint: false`,
`idempotentHint: true`.

### `search`
Ranked full-text search across the vault, English and Persian.

| Param | Type | Default |
|---|---|---|
| `q` | `str` | required; whitespace-separated terms |
| `conversation_id` | `str \| None` | all conversations |
| `direction` | `str \| None` | both |
| `from_ms` / `to_ms` | `int \| None` | unbounded |
| `limit` | `int` | `50`, clamped to `[1, 200]` |

The tool takes primitives and builds the query itself. It previously declared a
`SearchQuery` parameter, which no MCP client can construct — the value arrives as
JSON — so every call over the wire failed.

Returns message views ordered by rank, each with `rank` and `tier`
(`lexical` or `compact`). `LOCAL_ONLY` conversations are excluded.

### `get_messages`
A caller-scoped window of one conversation.

| Param | Type | Default |
|---|---|---|
| `conversation_id` | `str` | required |
| `from_ms` / `to_ms` | `int \| None` | unbounded |
| `limit` | `int` | `50`, clamped to `[1, 200]` |

Returns `[]` for an unknown or `LOCAL_ONLY` conversation — the two are
indistinguishable by design.

### `list_chats`
Conversations ordered by recency, excluding `LOCAL_ONLY`.
Params: `query` (substring over subject), `limit` (default `20`, max `200`).

### `get_message_status`
Delivery state reduced from the status-event lattice:
`{delivery_rank, failed_at_ms, deleted_at_ms, unknown_statuses}`.
Returns `None` for an unknown message or one in a fenced conversation.

### `get_conversation_window`
Whether the 24-hour free-form window is open:
`{open, last_inbound_ms, closes_at_ms}`. Takes `conversation_id` only.

The clock is the server's. This tool used to accept `now_ms`, which let the
caller assert the time that decides the answer — the model does not get to say
what time it is (INV-SENDPOLICY).

A `LOCAL_ONLY` conversation always reports `{open: false, last_inbound_ms: 0}` —
identical to an idle one. Activity timing is content, and this was a real leak
before it was closed.

### `list_templates`
The locally synced template catalogue: `{status, templates}`. On a vault created
by `init` the table exists and is empty, so this returns
`{"status": "OK", "templates": []}` until Phase 5 syncs a catalogue.
`FEATURE_NOT_INITIALISED` is reserved for a control database predating the
templates migration.

## The forbidden surface

These names may **never** appear. CI asserts the registered set is disjoint from
this set, against plain module constants so the guarantee survives SDK changes:

```
approve_draft   send_prepared_message   add_approval_device   revoke_device
set_policy      create_capability       set_mcp_visibility    raw_fts_query
sql_query       http_request            graph_api_call        send_to_number
broadcast       delete_message          export_vault          get_credentials
```

There is no `send_prepared_message` because approval on the phone *is* "approve
and send" — dispatch is triggered by a valid signed envelope, never by a model.

## Response shape

Every WhatsApp-originated string is wrapped:

```json
{
  "message_id": "msg_01J...",
  "conversation_id": "cnv_01J...",
  "direction": "in",
  "ts_lower_ms": 1700000000000,
  "ts_upper_ms_exclusive": 1700000060000,
  "ts_precision": "min",
  "delivery_rank": 3,
  "reply_to_ref": "wref_3f9a1c2b7d4e5f60",
  "body": { "_wv_untrusted": true, "text": "…" },
  "contact": {
    "contact_id": "cnt_01J...",
    "display_name": { "_wv_untrusted": true, "text": "…" },
    "wa_tail": "••••3812"
  }
}
```

Two things to note:

- **`_wv_untrusted`** marks attacker-controllable data. Treat it as data, never
  as instruction — the content of a message is not a command.
- **`reply_to_ref` is opaque.** A raw `wamid` base64-decodes to the counterparty's
  full E.164 number, so emitting one would defeat `wa_tail` masking entirely. The
  replacement is a deterministic handle: stable across calls, so reply chains stay
  correlatable, but carrying no recoverable identifier.

No response contains a full phone number.

## Privacy fence

Mark a conversation private:

```bash
whatsvault mcp-visibility --conversation-id cnv_… --visibility LOCAL_ONLY
```

It then never appears in `search`, `list_chats`, `get_messages`,
`get_message_status`, or `get_conversation_window`. The setter is CLI/phone only —
`set_mcp_visibility` is in the forbidden set, so the model cannot un-fence a chat.

## Audit

Every call is appended to `control.audit_log` with actor, tool, outcome, timestamp,
and an **HMAC** of the arguments — keyed, not a bare SHA-256, because a
low-entropy query like a contact name would otherwise be trivially recoverable by
dictionary attack. Content is never stored.

The record is written **after** the call with its real outcome, so a failed probe
cannot leave a clean trail. The table is append-only, enforced by triggers.

## Known limitation

INV-CONTENT is *hard* for writes: no message body can create authority, because no
write verb exists to reach. For **retrieval scope** it is an orchestration
property — the server cannot prevent a model from choosing to call `search` again
with a broader query after reading a message that suggests it.

The `LOCAL_ONLY` fence is the real, server-side boundary. This limitation is
stated rather than hidden.
