# Phase 2b verification — Swift consent agent

**Status:** Tasks 1–4 complete and qualified headlessly against the real
Python broker. **NOT production.** No pairing record exists on this host, no
Secure Enclave key has been generated, no Touch ID approval has been
performed, the LaunchAgent has never been loaded, and the agent has never
spoken to a privileged runtime — only to the test-driven broker. The two
interactive legs (`pairing generate`, a real Touch ID approval) are
implemented, gated and unrun.

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
- Signing identities available: one, an **Apple Development** certificate,
  team `3L5A4R7JNY`. No Developer ID Application certificate, no
  provisioning profiles.

## Artifacts

- `agent/consent-agent.swift` — 1602 lines, SHA-256
  `8e51c1a9fa5af1744f101c3812ce31c4ad6260d782fc0ecd6e4b138934da0b0f`.
- `tests/fixtures/consent/jcs_vectors.json` — 9 cases, SHA-256
  `eaa200b119d81e397186841fa74be67e2f5626b65eb1d901db1a07b9b11ee1be`.
- Default bundle `build/consent/TelegramMCPConsent.app`:
  `Identifier=com.telegram-mcp.consent`, `flags=0x10002(adhoc,runtime)`,
  `Signature=adhoc`, `TeamIdentifier=not set` — deliberately unpairable.
- Signed bundle `build/consent-signed/TelegramMCPConsent.app`:
  `flags=0x10000(runtime)`, `TeamIdentifier=3L5A4R7JNY`, pairable.

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
| real `pairing generate` | **not run** (platform-gated) |

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

1. **No pairing record exists.** `pairing generate` has never been run, so no
   Enclave key, no transport key and no daemon pin exist on this host. Until
   it is run, `run` refuses with `MISSING-RECORD` and the production approval
   path cannot sign anything.
2. **No Touch ID approval has been performed.** The prompt path is
   implemented (fresh `LAContext` per consent, biometrics only, key obtained
   with that same context, context invalidated after) but has never faced a
   real sensor.
3. **The LaunchAgent has never been loaded.** `RunAtLoad` is false and the
   definition ships disabled; `launchctl bootstrap gui/$UID` has not run.
4. **Phase-2J join gate remains formally unrun.** Six of its thirteen
   scenarios now pass through the real broker ↔ real agent path here, but
   Plan 2a's harness has not been re-pointed at this driver and the remaining
   scenarios (challenge tamper, wrong `key_id`, wrong `challenge_sha256`,
   duplicate approval, `runtime_id` mismatch, broker death, agent death,
   daemon-key rotation, agent-key rotation, and the one interactive Touch ID
   approval) are not yet driven.
5. **Identity change forces re-pairing.** Keychain ACLs bind to the signing
   identity, so re-signing the bundle with a different certificate makes the
   existing records unreadable. This is the intended behaviour and is
   documented, not tested.
6. **Biometric-set change and different-Mac restoration** are asserted by the
   design of Enclave binding (`.biometryCurrentSet`, device-bound blob) and
   are not executed.

Sources for the signing analysis: Apple's Developer ID and notarization
documentation, and the Secure Enclave key-protection documentation.
