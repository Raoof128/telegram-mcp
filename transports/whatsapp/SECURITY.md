# Security Policy

WhatsVault stores private message content. A vulnerability here is a privacy
incident, not an inconvenience, and the project is structured accordingly.

## Reporting a vulnerability

**Please do not open a public issue for a security problem.**

Report privately through [GitHub Security Advisories](https://github.com/Raoof128/whatsvault/security/advisories/new).
If that is unavailable to you, open a public issue containing only the words
"security report, requesting private contact" and nothing else, and you will be
given a private channel.

Please include:

- What boundary you believe is broken, in terms of the invariants below
- A minimal reproduction — a failing test is the ideal form
- What an attacker gains, concretely
- Any constraints on exploitability (local access, an enrolled device, a
  prompt-injected model, and so on)

**Expected response:** an acknowledgement within 7 days, an assessment within 14.
Fixes for confirmed issues land with a regression test that pins the exploit, and
you will be credited unless you prefer otherwise.

## Scope

This project is a single-user, local-first system. That shapes what counts.

### In scope

- Any path by which message content, a full phone number, or key material leaves
  its intended boundary — an MCP response, a log, a plist, an error string, disk
- Any bypass of the `LOCAL_ONLY` visibility fence
- Any way to cause a WhatsApp message to be sent without a valid, fresh,
  single-use hardware signature over the exact bytes and recipient
- Any way a **message body** can escalate into authority: selecting tools,
  widening retrieval scope, altering policy, or reaching a write path
- Authentication or authorisation flaws in the MCP transport
- Anything in the OAuth 2.1 authorization server mounted by
  `WHATSVAULT_PUBLIC_URL`: a token issued without an out-of-band approval, a PKCE
  or redirect-URI bypass, a code replayed, a revoked or rotated credential still
  accepted, or any path under `/oauth/` or `/.well-known/` that reaches a tool
- Sealed-envelope, replay, nonce, or clock-trust weaknesses
- Audit-log integrity failures — including a failed action recorded as a success

### Out of scope

- Compromise of the macOS user account itself. The threat model states plainly
  that the guarantee is *hardware-backed, biometrically gated approval*, **not**
  "root cannot approve".
- Physical access to an unlocked, enrolled device
- Vulnerabilities in WhatsApp, Meta's Cloud API, Cloudflare, or an LLM provider
  (please report those to the relevant vendor)
- The named residual risk **R1**: a message body engineered to defeat both the
  confusables/bidi display guard *and* a human reading it. This is documented as
  an accepted V1 limitation, not an oversight — see the design specification.
- Denial of service against your own **local** daemon. Resource exhaustion
  against a *published* deployment is in scope — unbounded storage from
  unauthenticated requests is the concrete case, and the reason those fields are
  capped and expired rows collected.

## The invariants a report should aim at

A finding is most useful when framed against the property it breaks:

| Invariant | Broken if you can… |
|---|---|
| **INV-APPROVAL** | cause a send without an out-of-band approval |
| **INV-SIGNATURE** | make a database state substitute for a signature |
| **INV-HARDWARE** | approve without the enrolled device's Secure Enclave key |
| **INV-ATREST** | find plaintext content, media, or key material on disk, in a log, or in git |
| **INV-CIPHERTEXT** | recover plaintext from anything the edge persists |
| **INV-EDGE-AAD** | swap, downgrade, or relabel envelope metadata without failing the tag |
| **INV-CONTENT** | make retrieved content create authority or widen scope |
| **INV-ACK** | get an ACK for a message that was not durably accounted for |

## What this project does to find these itself

- **A proven-empty write surface.** CI asserts the registered MCP tool set is
  disjoint from a named forbidden set, so a write verb cannot appear by accident.
- **An adversarial suite.** [`tests/adversarial/`](tests/adversarial/) contains
  prompt-injection cases and a regression test for every red-team finding, each
  written as the attacker's goal rather than the implementation detail.
- **A secret-tracking gate.** `tests/test_no_secrets.py` fails the build if a key
  literal, PEM block, or database file is ever tracked. `gitleaks` runs in
  pre-commit and in CI.
- **Content-free logging.** `ops.structlog` refuses any field that could carry
  message content, so a log line cannot leak a body even by mistake.
- **Verification before activation.** External assumptions are recorded with the
  primary-source quote that supports them, and gates stay closed until observed.

## Supported versions

Alpha. Only `main` receives fixes.

## Handling your own data

- Never commit `vault.db`, `control.db`, a raw export, or anything from
  `imports/` or `blobs/`. `.gitignore` and the secret gate both guard this.
- WhatsVault reads **no** secrets from the environment. Every key lives in the
  macOS Keychain. If you find yourself putting a token in `.env`, that is a bug.
- Connecting an assistant intentionally discloses the excerpts it retrieves to
  that provider. That is the trade being made; make it deliberately.
- Publishing the surface (`WHATSVAULT_PUBLIC_URL`) widens that considerably: the
  content of anything the model can read now reaches a third party on every tool
  call, and the authorization server becomes the thing standing between your
  archive and the internet. Fence what should never leave *before* publishing —
  `whatsvault mcp-visibility --conversation-id … --visibility LOCAL_ONLY` — and
  `whatsvault oauth-revoke` is the off switch.
