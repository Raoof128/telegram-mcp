# WhatsVault

**A local-first, encrypted archive of your own WhatsApp messages — searchable by an AI assistant, but never sendable by one.**

[![CI](https://github.com/Raoof128/whatsvault/actions/workflows/ci.yml/badge.svg)](https://github.com/Raoof128/whatsvault/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/downloads/)
[![Tests](https://img.shields.io/badge/tests-539%20passing-brightgreen.svg)](#testing)

WhatsVault ingests your WhatsApp messages into an encrypted SQLCipher vault on your own Mac, indexes them for fast bilingual (English/Persian) search, and exposes a **strictly read-only** [MCP](https://modelcontextprotocol.io) surface so an assistant like ChatGPT or Claude can search and quote them.

It can also *draft* replies. It cannot send them. Every outbound message requires a fresh, per-message, biometric signature from a hardware key on your iPhone — on a channel the model cannot reach.

> **Status: alpha, and honest about it.** The vault, search, and read-only MCP are implemented and tested. The write path is built but deliberately inert: it is gated behind external verification that is [documented, not assumed](docs/internal/findings/2026-08-27-phase0-verification.md). See [Project status](#project-status).

---

## Why this exists

Handing a chat archive to an AI assistant usually means handing it to a third party, and giving an assistant the ability to reply usually means trusting it not to misuse that ability. WhatsVault refuses both trades:

- **Your messages stay on your machine.** The archive is SQLCipher-encrypted under a key that lives only in the macOS Keychain. Cloud infrastructure, where used, stores ciphertext only.
- **The assistant gets read access, and only read access.** There is no send verb, no approve verb, and no credential anywhere on the MCP surface — and a CI test proves it, by asserting the registered tool set is disjoint from a named forbidden set.
- **Sending requires a human, in hardware, out of band.** A `prepare → send` pair inside one model turn is not a security boundary; a model can call both back to back. So authorisation lives on your phone's Secure Enclave instead.

## Core guarantees

These are the invariants the design is built around. Each is enforced by code and covered by tests, not by convention.

| Invariant | Guarantee |
|---|---|
| **INV-APPROVAL** | No WhatsApp write can occur without an out-of-band approval that cannot be produced through any MCP, model, scheduler, or provider interface. |
| **INV-HARDWARE** | Approval authority is a Secure Enclave P-256 key on an enrolled iPhone. The Mac, MCP, scheduler and sender hold verification keys only and cannot manufacture an approval. |
| **INV-ATREST** | No plaintext message content, media, credential, or signing key ever reaches disk, environment variables, logs, git, or an MCP response. |
| **INV-CONTENT** | Retrieved message content can inform an answer but can never create authority, widen retrieval scope, select tools, or alter policy — no matter what the message says. |
| **INV-ACK** | The ingest pipeline acknowledges a message only after durable local disposition. ACK means "accounted for", never "went well". |

The full set, with rationale and threat model, is in the [design specification](docs/internal/specs/2026-08-27-whatsvault-design.md).

## How it fits together

```
  WhatsApp ──▶ Cloudflare Worker ──▶ Queue ──▶  ingest daemon  ──▶ ┌───────────────┐
              (seals to your key;            (decrypts locally,   │  vault.db     │
               stores ciphertext only)        ACK after commit)   │  SQLCipher    │
                                                                  │  + FTS5 index │
                                                                  └───────┬───────┘
                                                                          │ read-only
  Claude Code / Desktop ─────────────────▶  loopback MCP  ────────────────┘
                            bearer token   (6 read tools, redaction,
                                            LOCAL_ONLY fence, audited)
                                                     ▲
  ChatGPT ──▶ outbound tunnel ──▶ OAuth 2.1 AS ──────┘
              (nothing bound     (approval is granted from the
               publicly)          terminal, never on the web page)
                                                     │
                                                     │ prepare_message (draft only)
                                                     ▼
                                              ┌─────────────┐   Face ID + Secure Enclave
                                              │ control.db  │◀──── iPhone approval app
                                              │  drafts     │      (signs the exact bytes)
                                              └──────┬──────┘
                                                     │ valid signed envelope
                                                     ▼
                                              dispatcher ──▶ whatsvault-meta ──▶ WhatsApp
                                              (no send authority of its own)
```

A fuller walkthrough, including trust boundaries and the data model, is in [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Quick start

**Requirements:** macOS, Python 3.11+, and SQLCipher (`brew install sqlcipher`).

> `sqlcipher3` is a source build against Homebrew's SQLCipher — there is no prebuilt wheel for macOS arm64.

```bash
git clone https://github.com/Raoof128/whatsvault.git
cd whatsvault
make install          # creates .venv and installs the project with dev extras
make check            # lint + format check + the full test suite
```

Create the vault, import an existing WhatsApp export, and search it — no Meta
account, no cloud, no network:

```bash
whatsvault init --reveal      # encrypted databases + Keychain keys; prints the MCP token once
whatsvault accounts-add       # prints acc_… — the import refuses to guess its target
whatsvault conversations-add --account-id acc_… --subject "Alice"   # prints cnv_…
whatsvault import --path ~/Downloads/chat.txt --dry-run   # preview, writes nothing
whatsvault import --path ~/Downloads/chat.txt --conversation-id cnv_… --account-id acc_… \
  --timezone Australia/Sydney --date-format DMY --self-label "Me"
whatsvault doctor             # vault, search, ingest and MCP readiness
```

Then start the read-only MCP server:

```bash
whatsvault-mcp                # serves on http://127.0.0.1:8765/mcp
```

**Locally** — Claude Code and Claude Desktop speak HTTP MCP directly, so the
bearer token is all they need:

```bash
claude mcp add --transport http whatsvault http://127.0.0.1:8765/mcp \
  --header "Authorization: Bearer $TOKEN"
```

**From ChatGPT**, whose connector dialog offers only OAuth, No Authentication or
Mixed, set `WHATSVAULT_PUBLIC_URL` to an https origin fronted by an outbound
tunnel. That mounts an OAuth 2.1 authorization server; nothing is bound
publicly. The consent page has no password field — it shows a code, and you
grant it from a terminal:

```bash
whatsvault oauth-pending
whatsvault oauth-approve --code ABCDE-FGHIJ
whatsvault oauth-revoke        # the off switch
```

Publishing means message content reaches that provider on every tool call. The
read-only surface, redaction and the `LOCAL_ONLY` fence still hold, but they
bound the *model*, not the host. Fence anything that should never leave first.

Full walkthrough: [docs/USAGE.md](docs/USAGE.md).

## The MCP surface

Six read tools, each annotated `readOnlyHint: true`, `openWorldHint: false`:

| Tool | Returns |
|---|---|
| `search` | Ranked, redacted, untrusted-wrapped hits across the vault |
| `get_messages` | A window of one conversation, caller-scoped |
| `list_chats` | Conversations, excluding any marked `LOCAL_ONLY` |
| `get_message_status` | Delivery state reduced from the status-event lattice |
| `get_conversation_window` | Whether the 24-hour free-form window is open |
| `list_templates` | The locally synced template catalogue |

And an explicitly **forbidden** set that CI asserts can never appear: `approve_draft`, `send_prepared_message`, `add_approval_device`, `revoke_device`, `set_policy`, `create_capability`, `set_mcp_visibility`, `raw_fts_query`, `sql_query`, `http_request`, `graph_api_call`, `send_to_number`, `broadcast`, `delete_message`, `export_vault`, `get_credentials`.

Every returned string that originated from WhatsApp is wrapped as untrusted data, and no response ever contains a full phone number. Reference: [docs/MCP.md](docs/MCP.md).

## Security posture

Message content is the asset, and the threat model treats the assistant itself as a potential adversary — not out of paranoia, but because a prompt-injected model is a realistic attacker with legitimate credentials.

The read surface has been red-teamed twice, and the findings are in the history
rather than the marketing.

**The loopback surface:**

- A metadata leak past the `LOCAL_ONLY` privacy fence (activity timestamps for conversations explicitly marked private)
- Full phone numbers recoverable from raw `wamid` values, which base64-decode to the counterparty's E.164 — defeating the redaction layer for any reply
- A row limit that a negative value could escape entirely, because SQLite reads `LIMIT -1` as unbounded
- `get_conversation_window` took the current time *from the caller*, letting a model assert the clock that decides whether a send window is open
- An audit log that recorded every call as a success, so a failed probe left a clean trail

**The published surface**, attacked separately because publishing changes what
the auth layer is for — nineteen findings, including:

- Every unauthenticated OAuth field stored unbounded, so a loop of authorize calls wrote whatever the caller liked into the operator's database
- Grants unaudited: tool calls were recorded, the grant authorising them was not, so an incident had no answer to *who was given access, and when*
- Refresh rotation that contained nothing — the replaced access token stayed live for its full hour — and reuse of a rotated token that failed only the one call, leaving a thief's newer pair working
- A consent page that escaped only `<`, and did not strip bidi overrides — which can make a hostile client name read as a familiar one in the sentence asking for consent

All are fixed, and each is pinned by a regression test in [`tests/adversarial/`](tests/adversarial/). To report a vulnerability, see [SECURITY.md](SECURITY.md).

## Testing

```bash
make test       # full suite
make audit      # the security-boundary suite specifically
make secrets    # fail if anything secret-shaped is tracked
make test-cov   # with a coverage report
```

539 tests, covering crypto envelopes and golden vectors, the import grammar and its time model, FTS5 ranking, the MCP redaction and ACL fences, the approval chain and its replay gates, ingest crash recovery, and an adversarial suite for prompt injection and the red-team findings above.

## Project status

| Area | State |
|---|---|
| Vault core, importer, bilingual search | Implemented and tested |
| Read-only MCP (auth, redaction, ACL, audit) | Implemented, red-teamed, serving over loopback |
| Public deployment (OAuth 2.1, out-of-band approval) | Implemented and red-teamed; opt-in via `WHATSVAULT_PUBLIC_URL` |
| Approval chain (drafts, device enrolment, policy, signatures) | Implemented and tested locally |
| Sealed ingest pipeline | Implemented; the Cloudflare edge is a recorded contract, not yet built |
| Live WhatsApp send | **Deliberately inert.** Blocked behind Phase-0 verification |
| iPhone approval app | Contract defined; not built |

The [Phase-0 verification record](docs/internal/findings/2026-08-27-phase0-verification.md) tracks each external assumption with the primary-source quote that confirms or refutes it, and states plainly which gates are still open. Nothing in this project is marked "done" on the strength of a plausible reading of a doc.

## Documentation

| Document | What it covers |
|---|---|
| [Architecture](docs/ARCHITECTURE.md) | Trust boundaries, storage split, ingest, the MCP defences |
| [Usage](docs/USAGE.md) | Install, import, run the server, connect an assistant |
| [MCP reference](docs/MCP.md) | Tool-by-tool reference, response shape, audit behaviour |
| [Security policy](SECURITY.md) | Reporting, scope, and the invariants a finding should target |
| [Internal records](docs/internal/) | Design spec, implementation plans, and the verification evidence behind the claims above |

> **A note on `docs/internal/`.** Those are **working documents**, not polished
> documentation — the design spec, the phase-by-phase implementation plans, and
> the verification records, kept exactly as they were written while the work was
> being done. They are published on purpose: a project that makes security claims
> should be able to show how it reached them, including the corrections along the
> way. They are also *historical*, so where one disagrees with the code, the code
> and its tests are correct. For the maintained explanation of how the system
> works, read [Architecture](docs/ARCHITECTURE.md) instead.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for the development workflow, the test-driven expectation, and the security-review bar for anything touching a boundary. By participating you agree to the [Code of Conduct](CODE_OF_CONDUCT.md).

## License

[MIT](LICENSE) © Raouf
