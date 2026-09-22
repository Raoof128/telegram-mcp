# Phase 2b verification — Swift consent agent

**Status:** Tasks 1–5 complete. The agent is qualified headlessly against the
real Python broker, and both interactive legs have now been run on this host:
a Secure Enclave approval key exists and a real Touch ID approval produced a
signature the broker's verifier accepts. **NOT production.** The daemon pin
is absent because no real runtime exists yet, the LaunchAgent has never been
loaded, and the agent has never spoken to a privileged runtime — only to the
test-driven broker.

## Reproducibility

```bash
bash scripts/package_agent.sh          # swiftc -O + bundle + ad-hoc signature
uv run pytest tests/agent -q
plutil -lint agent/ConsentAgent-Info.plist
codesign -dv build/consent/TelegramMCPConsent.app
```

Interactive legs, opt-in and host-mutating:

```bash
uv run pytest tests/agent -q --run-platform-gated   # real pairing + Touch ID
TELEGRAM_MCP_SIGN_IDENTITY="<identity>" bash scripts/package_agent.sh
```

## Environment (2026-09-22, Australia/Sydney)

- macOS 27.0, Mac14,2 (arm64), Secure Enclave present.
- `swiftc` Apple Swift 6.4 (swiftlang-6.4.0.34.1); build flags `-O`, single
  source file, no third-party packages.
- Frameworks: CryptoKit, Foundation, LocalAuthentication, Security.
- Signing identities available: one, an **Apple Development** certificate.
  No Developer ID Application certificate, no provisioning profiles. The
  team identifier is redacted here as `<team-id>`; what matters to the
  evidence is that one is present and stable, not its value.

## Artifacts

- `agent/consent-agent.swift` — 1602 lines, SHA-256
  `8e51c1a9fa5af1744f101c3812ce31c4ad6260d782fc0ecd6e4b138934da0b0f`.
- `tests/fixtures/consent/jcs_vectors.json` — 9 cases, SHA-256
  `eaa200b119d81e397186841fa74be67e2f5626b65eb1d901db1a07b9b11ee1be`.
- Default bundle `build/consent/TelegramMCPConsent.app`:
  `Identifier=com.telegram-mcp.consent`, `flags=0x10002(adhoc,runtime)`,
  `Signature=adhoc`, `TeamIdentifier=not set` — deliberately unpairable.
- Signed bundle `build/consent-signed/TelegramMCPConsent.app`:
  `flags=0x10000(runtime)`, `TeamIdentifier=<team-id>`, pairable.

## Actual results

- `tests/agent`: **26 passed, 2 skipped**. The two skips are the interactive
  legs: the live Touch ID approval and `pairing generate`.
- JCS byte equality: all **9** frozen vectors encode byte-for-byte, including
  Persian, Latin-accent, emoji, C0 controls, C1 controls, escapes, bidi,
  nested and array-of-objects cases; the direct-construction probe proves the
  encoder re-asserts ASCII keys rather than trusting the decoder.
- Daemon signature verification: all 9 vectors verify against the fixture
  challenge key derived from `b"\x01" * 32`, and a one-bit flip is rejected.
- `plutil -lint agent/ConsentAgent-Info.plist`: OK; the installed copy at
  `scripts/consent-agent.plist` is asserted identical.

### Rendezvous scenarios (real broker, real agent binary)

The driver runs `telegram_mcp.ipc.rendezvous.serve_rendezvous` and
`telegram_mcp.consent.broker.ConsentBroker` — not a hand-written stub —
against the packaged agent over a Unix socket.

| Scenario | Result |
|---|---|
| `good` | approval accepted, ECDSA signature verified by the broker |
| `replay` | first consume accepted, second rejected (exact-once) |
| `tamper-display` | agent denied with `DISPLAY-MISMATCH`, no key touched |
| `wrong-daemon-key` | agent denied with `CHALLENGE-SIGNATURE-INVALID` |
| `kill-mid-prompt` | agent exited well inside 5 s of socket close; no orphan |
| `wrong-transport-key` | broker refused the handshake; agent exited |

### Pairing

| Check | Result |
|---|---|
| ad-hoc bundle `pairing generate` | refused, `AD-HOC-IDENTITY` |
| ad-hoc bundle `pairing import-daemon-pin` | refused, pairing state unchanged |
| identity reporting | matches `codesign -dv`, read from the signature |
| signed bundle | `apple-development`, team present, `pairable: true` |
| `run` without a record | `MISSING-RECORD`, tells the operator to re-pair |
| real `pairing generate` | **run** — see the interactive legs below |

### Interactive legs (run 2026-09-22, this host)

Both ran behind `--run-platform-gated` against the certificate-signed bundle
(`apple-development`, team `<team-id>`).

| Leg | Result |
|---|---|
| `pairing generate` | Secure Enclave approval key and software transport key created and stored in the login keychain |
| live Touch ID `approve` | prompt shown, operator approved, envelope emitted and verified |

Fingerprints as paired (public material):

- approval `p256:sha256:fbd39bb34fb63d7d14fa614cf03b265ce6420fd9d6575c1e31f1e54da36f0e90`
- transport `ed25519:sha256:6aed837fb11478eafc95fc2da0441361cba4cf1c9d4b24256889dcb0f5946b13`

What the Touch ID leg proves, precisely: the approval key's access control is
`.privateKeyUsage` plus `.biometryCurrentSet`, so the Enclave refuses to sign
without a fresh biometric authentication on the presenting `LAContext`. The
test verified the emitted DER ECDSA signature against the key's own public
half with Python `cryptography`, and asserted the envelope's `key_id` equals
the paired approval record. A valid signature under that ACL is therefore
cryptographic evidence that a real biometric authentication happened — not an
assertion that a prompt was seen.

The daemon pin was deliberately **not** imported. That record holds a real
runtime's challenge public key; pinning the test fixture's key
(`b"\x01" * 32`) into the operator's keychain would misreport what is paired.
`pairing-status` therefore reads `paired: false` with two of three records
present, which is the honest state.

Re-minting is a ceremony, not a side effect: the gated pairing test asserts
the paired state instead of calling `pairing generate` again, because
rotating the approval key would silently invalidate whatever the daemon has
pinned. Rotation has its own test behind `TELEGRAM_MCP_ALLOW_REPAIR=1`.

### Closed: keychain items are scoped to the writing code identity

Running the two legs exposed that the records were ordinary login-keychain
generic passwords, readable by anything running as this operator — including
the **transport private key**, which authenticates the agent to the daemon's
rendezvous socket. The approval key was never exposed: the keychain holds
only its encrypted, device-bound Enclave blob, useless without a biometric
check on this Mac.

Closed with the file keychain's own mechanism, which costs nothing: each
record is written with a `SecAccess` naming this binary as the only trusted
application, and reads disable interaction so a foreign identity fails
closed instead of raising an authorization dialog. Writes replace rather
than update, because an update inherits the ACL the existing item carries.
Scoping by access group instead would need the data-protection keychain,
whose entitlement requires a paid membership's provisioning profile.

Verified on this host:

| Reader | Result |
|---|---|
| the signed bundle that wrote them | reads its own records |
| the ad-hoc bundle (different identity) | sees no records at all |
| `/usr/bin/security` (permits interaction) | authorization prompt, not bytes |
| the signed bundle after a rebuild | still reads them — the ACL keys on the signing identity, not the binary hash |

Defence in depth behind it: the daemon serves one agent session at a time,
so a process that somehow held the transport key could not displace or race
an established agent.

## Deviations from the plan, and why

1. **Developer ID is not required for pairing.** The plan demanded a
   `Developer ID Application` signature before pairing, Touch ID or the
   Enclave key. This host has only a free Apple Development certificate, and
   Developer ID belongs to the distribution path: Apple's current
   documentation states Gatekeeper checks software distributed outside the
   Mac App Store and that locally built software is not checked by
   Gatekeeper, so notarization does not apply to it either. The rule the
   agent enforces is therefore a **stable, certificate-backed identity** —
   ad-hoc and unsigned builds are refused, because their identity changes
   with every rebuild and a key bound to one would be bound to nothing.
   A probe on this host, signed with the Apple Development certificate and
   carrying no provisioning profile, generated a Secure Enclave key (569-byte
   blob) and stored it in the login keychain successfully, which confirms the
   entitlement discussion in Apple's forums applies to the *data-protection*
   keychain path (`SecKeyCreateRandomKey` with `kSecAttrIsPermanent`) and not
   to the CryptoKit path used here, where the key stays in the Enclave and
   the app keeps only its encrypted `dataRepresentation`. Consequence: this
   agent is correct for a single operator on this Mac and would need a
   Developer ID certificate plus notarization before it is distributed to
   anyone else's machine.
2. **The stub broker is the real broker.** The plan called for a Python stub;
   running the real `serve_rendezvous` and `ConsentBroker` costs the same and
   proves byte agreement instead of agreement with a stub.
3. **Prompt frames were frozen here** (`PROMPT` / `APPROVAL` / `DENIAL`), as
   Plan 2a's Task 9 Step 4 driver depends on them and Plan 2b owns the agent
   side of the wire.
4. **`selftest-rendezvous` is a separate subcommand** from `run`. The plan
   allowed a `CONSENT_NO_UI=1` selftest path; keeping it on its own
   subcommand means the production `run` path contains no test-key branch at
   all, which the tests assert against the source.
5. **`scripts/package_agent.sh` is new.** The plan described the bundle
   layout without naming a script; assembling it by hand in a reviewable
   script is what makes the layout auditable and the tests reproducible.

## Cross-plan defects this work exposed

1. **Rendezvous key-id pattern.** Plan 2a's `_KEY_ID_RE` accepted
   `ed25519:<64 hex>`, while the key store's own fingerprints are
   `ed25519:sha256:<64 hex>`. A real agent could never have completed the
   handshake. Fixed in `ipc/rendezvous.py` with its tests.
2. **LaunchAgent arguments.** The installed plist passed `--socket <path>`,
   which the agent's `run` command does not accept. Both copies corrected.
3. **Escaped interpolation.** The `run` refusal printed a literal
   `\(error)` instead of the message. Caught by the refusal test.

## Unresolved items

1. **Transport-key scoping** — see the finding above; undecided.
2. **No daemon pin.** Two of three pairing records exist; the third is a real
   runtime's challenge public key, so `run` still refuses with
   `MISSING-RECORD` until a runtime is provisioned and its public key is
   imported with `pairing import-daemon-pin`.
3. **The LaunchAgent has never been loaded.** `RunAtLoad` is false and the
   definition ships disabled; `launchctl bootstrap gui/$UID` has not run.
4. *(closed)* **The Phase-2J join gate now passes**, all thirteen scenarios
   plus the interactive Touch ID one. See the phase-2a evidence for the
   ledger.
5. **Identity change forces re-pairing.** Keychain ACLs bind to the signing
   identity, so re-signing the bundle with a different certificate makes the
   existing records unreadable. This is the intended behaviour and is
   documented, not tested.
6. **Biometric-set change and different-Mac restoration** are asserted by the
   design of Enclave binding (`.biometryCurrentSet`, device-bound blob) and
   are not executed.

Sources for the signing analysis: Apple's Developer ID and notarization
documentation, and the Secure Enclave key-protection documentation.
