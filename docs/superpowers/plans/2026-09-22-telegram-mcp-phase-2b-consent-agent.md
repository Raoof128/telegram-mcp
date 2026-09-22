# Telegram MCP Phase 2b Implementation Plan — Swift Consent Agent

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the minimal signed Swift consent agent: verify daemon challenges, enforce display-digest equality, prompt with Touch ID, and sign exact bytes with a Secure Enclave key — tested through its wire protocol against a stub broker.

**Architecture:** One small auditable Swift binary (`telegram-mcp-consent`), no dependencies beyond Apple frameworks (Foundation, CryptoKit, LocalAuthentication, Security). It speaks the frozen challenge-wire contract defined in §4 of the [Phase-2 design](../specs/2026-09-22-telegram-mcp-phase-2-design.md) and asserted by Plan 2a's `tests/fixtures/consent/jcs_vectors.json`. All cryptography testable headless; Touch ID and keychain paths platform-gated.

**Tech Stack:** Swift 6.x (Xcode toolchain, verified present), swiftc direct build (no Xcode project), CryptoKit (Ed25519 verify, P-256 Secure Enclave, SHA-256), LocalAuthentication (Touch ID), Security (keychain pairing record), `codesign` with Developer ID, pytest-driven shell tests against a Python stub broker.

**Spec:** [Phase-2 design](../specs/2026-09-22-telegram-mcp-phase-2-design.md) §4; wire vectors from Plan 2a ([phase-2a authority plan](2026-09-22-telegram-mcp-phase-2a-authority.md) Task 3).

**Status:** Draft for review. Depends on Plan 2a Task 3's frozen `jcs_vectors.json` for byte-equality tests; everything else is independent and may run alongside Plan 2a.

## Global Constraints

- Single source file preferred (`agent/consent-agent.swift` + `agent/Protocol.swift` only if the file grows unwieldy); no third-party packages.
- JCS encoder handles str/int/bool/None only; floats are a fatal error (matches Plan 2a rule).
- Challenge signature: Ed25519 over exact JCS bytes, raw 64-byte signature, base64url transport; verify with the pinned daemon public key (raw 32 bytes).
- Approval signature: ECDSA-SHA256 over the exact challenge JCS bytes with the Enclave P-256 key, DER-encoded, base64url transport; Touch ID (`deviceOwnerAuthenticationWithBiometrics`) per signature, no caching across prompts.
- Display digest: `SHA256("telegram-mcp-display-v1" || JCS(display_payload))`, lowercase hex, constant-time compare; render if and only if equal.
- Pairing record (daemon challenge pubkey + fingerprint) lives in the operator login keychain under an access group bound to the Developer ID identity — never compiled into the binary.
- LaunchAgent plist installed disabled-by-default; runtime loads (`bootstrap`) at start and unloads (`bootout`) at stop; agent auto-exits after broker disconnect plus grace.
- Signed with Developer ID before any Touch ID/keychain acceptance test; ad-hoc builds are dev-only and MUST fail the pairing-identity test.
- No implementation without a failing test first; shell-test failures show full output.

## Review Focus

1. A tampered display payload with a valid challenge signature must refuse to render — Task 2 pins it.
2. A replayed approval signature must fail at the broker — Task 3 pins exact-once against the stub broker.
3. JCS bytes must equal Plan 2a's frozen vectors byte-for-byte, including non-ASCII text — Task 1 pins it.
4. An ad-hoc-signed binary must fail pairing identity, never silently proceed — Task 4 pins it.
5. Killing the broker mid-prompt must exit the agent (no orphan UI) — Task 3 pins it.

## Scope and execution preparation

Work in `/Users/raoof.r12/Desktop/Raouf/Telegram`. Read `AGENT.md`, the Phase-2 design §4, and Plan 2a Task 3 (frozen interface). macOS + Touch ID + Developer ID required; keychain/Touch ID/signing tests are platform-gated and interactive. Build with `swiftc -O agent/consent-agent.swift -o build/consent/telegram-mcp-consent` (exact flags in Task 1). Every binary path in task steps means the tests' `AGENT_BIN` constant (loose build until Task 4, bundle binary after). `build/` is git-ignored scratch (add it); only `agent/` sources, tests, plist data, and verification documents are committed.

## File responsibilities

| Files | Responsibility |
|---|---|
| `agent/consent-agent.swift` ( + `agent/Protocol.swift` if split) | JCS encoder, challenge verify, display-digest gate, Touch ID prompt, Enclave sign, rendezvous client, pairing commands |
| `agent/ConsentAgent-Info.plist` (data for install) | LaunchAgent definition, disabled by default |
| `tests/agent/stub_broker.py` | Python stub broker: serves frozen challenges, checks approval signatures with `cryptography`, drives tamper/replay/kill scenarios |
| `tests/agent/test_consent_agent.py` | pytest shell tests: build once, run scenarios, assert exit codes/outputs (platform-gated where noted) |
| `docs/verification/phase-2b.md` | Actual run evidence (build flags, signatures, test results, keychain/pairing record IDs — public material only) |

## Frozen wire contract (from Plan 2a; duplicated here so this plan is self-contained)

**TG-JCS-v1** (restricted RFC 8785 profile, byte-identical on both sides): ASCII property names only; keys sorted; `,`/`:` separators; literal UTF-8; escape only U+0000–U+001F plus `"` and `\` with lowercase hex (C1-and-above stay literal); values limited to str/int/bool/None/array/object; floats, NaN, Infinity, lone surrogates, and non-ASCII keys are all fatal errors.
- Challenge JCS fields: `account`, `canonical_request_hmac`, `client`, `display_digest`, `exposure_snapshot_digest`, `expiry` (integer seconds), `nonce` (22-char b64url of 16 random bytes), `policy_epoch`, `principal`, `project_scope_digest`, `runtime_id` (hex), `security_epoch`, `tool`.
- Daemon signature: Ed25519 over exact JCS bytes; transport `{"challenge": "<b64url JCS>", "sig": "<b64url raw 64B>"}`.
- Display payload fields: `client_display`, `action_display`, `project_display[]`, `peer_display`, `risk_class`; digest `SHA256("telegram-mcp-display-v1" || JCS)`, lowercase hex.
- **ApprovalEnvelope** (frozen struct, verified whole by the broker): `{"challenge_sha256": "<hex of JCS>", "sig": "<b64url DER ECDSA>", "key_id": "<p256:sha256:...>"}`.
- Fixture seeds (Plan 2a Task 3 seed table, asserted here too): challenge daemon key `b"\x01" * 32`.
- Byte-equality vectors: `tests/fixtures/consent/jcs_vectors.json` (Plan 2a Task 3 Step 7 freezes it).

## Task 1: JCS encoder and challenge verification

**Files:** Create `agent/consent-agent.swift`, `tests/agent/test_consent_agent.py` (skeleton with this task's tests; later tasks append).

**Interfaces:** Produces `jcsEncode(_:) -> Data` (throws on float), `verifyChallenge(jcs:sig:daemonPub:) -> Bool`. Consumes `jcs_vectors.json` (fails closed with a clear message if absent — Plan 2a dependency).

- [ ] **Step 1: Write the failing byte-equality test.**

```python
import json, subprocess

VECTORS = "tests/fixtures/consent/jcs_vectors.json"
AGENT_BIN = "build/consent/telegram-mcp-consent"  # Task 4 re-points this at the bundle binary and re-runs green


def test_jcs_vectors_match_frozen_bytes():
    cases = json.load(open(VECTORS))["cases"]
    assert len(cases) >= 3
    out = subprocess.run(
        [AGENT_BIN, "selftest-jcs", VECTORS],
        capture_output=True, text=True, timeout=60,
    )
    assert out.returncode == 0, out.stderr
    assert "JCS-OK" in out.stdout
```

- [ ] **Step 2: Run it; expect binary-missing failure.** Add a `@main struct ConsentAgent` entry in `agent/consent-agent.swift` with a `selftest-jcs <vectors-file>` subcommand: for each case, encode the input with the Swift JCS encoder and byte-compare against the frozen `jcs_hex`; print `JCS-OK` only if all match (including a non-ASCII case).
- [ ] **Step 3: Swift JCS encoder.** TG-JCS-v1 exactly: assert every key ASCII (throw otherwise — byte-wise UTF-8 order then equals codepoint order for our keys); escape only U+0000–U+001F plus `"`/`\` (`\u00XX` lowercase hex); C1-and-above literal UTF-8; integers plain; `true`/`false`/`null`. Any float, NaN-equivalent, lone surrogate, or non-ASCII key throws (parity with Plan 2a's rejections).
- [ ] **Step 4: Ed25519 verify.** `Curve25519.Signing.PublicKey(rawRepresentation:)` from the 32-byte pinned daemon key; `isValidSignature` over the exact JCS bytes. `selftest-verify` subcommand: verifies each vector's daemon signature fixture (Plan 2a vectors include daemon `sig` values made with the fixture challenge key) and rejects a one-bit-flipped signature.
- [ ] **Step 5: Build, run tests, commit `feat: add Swift JCS and challenge verification`.** Exact build: `mkdir -p build/consent && swiftc -O agent/consent-agent.swift -o build/consent/telegram-mcp-consent`.

## Task 2: Display gate and Touch ID approval

**Files:** Modify `agent/consent-agent.swift`; append `tests/agent/test_consent_agent.py`.

**Interfaces:** Consumes Task 1 verify. Produces `approveFlow(challengeJcs:displayPayload:)` (verify → digest recompute + constant-time compare → Touch ID → Enclave sign → approval JSON on stdout).

- [ ] **Step 1: Write the failing tamper test.**

```python
def test_tampered_display_refuses_to_render():
    out = subprocess.run(
        [AGENT_BIN, "selftest-display-tamper"],
        capture_output=True, text=True, timeout=60,
    )
    assert out.returncode != 0
    assert "DISPLAY-MISMATCH" in (out.stdout + out.stderr)
```

- [ ] **Step 2: Run; expect subcommand-missing failure.** Implement `selftest-display-tamper`: valid signed challenge + one-codepoint-modified display payload → digest mismatch → exit nonzero printing `DISPLAY-MISMATCH`, never touching LocalAuthentication (assert by environment flag `CONSENT_NO_UI=1` which makes any prompt attempt a fatal error in selftests).
- [ ] **Step 3: Implement the approval flow.** Key material arrives through a `KeyProvider` protocol (`keyForApproval(context:) throws -> SecureEnclave.P256.Signing.PrivateKey`); tests inject an ephemeral-key double, Task 4 binds the keychain-backed provider — no forward reference. Per consent: create a fresh `LAContext`; obtain the key with that exact context; evaluate `LAContext.evaluatePolicy(.deviceOwnerAuthenticationWithBiometrics)` on the same context (no reuse across prompts); sign the exact JCS bytes; invalidate the context; discard the key object; output the ApprovalEnvelope JSON. `CONSENT_NO_UI=1` short-circuits to a signed-with-test-key path ONLY in `selftest-` subcommands; the real `approve` path contains no test-key branch (pinned by the Step 2 `grep` assertion).
- [ ] **Step 4: Platform-gated live test** (Touch ID prompt appears; operator approves/denies once; approval verifies against the Enclave public key with Python `cryptography` in the test). Marked `platform_gated`, interactive.
- [ ] **Step 5: Run headless tests + full pytest file; commit `feat: add display gate and Touch ID approval`.**

## Task 3: Rendezvous client and stub-broker scenarios

**Files:** Modify `agent/consent-agent.swift`; create `tests/agent/stub_broker.py`; append tests.

**Interfaces:** Consumes Tasks 1–2. Produces `runAgent(socketPath:pairingRecord:)` (connect → pinned-key handshake → serve prompts until broker disconnect → auto-exit after grace).

- [ ] **Step 1: Write the failing stub-broker tests.**

```python
def test_good_challenge_round_trip():
    from tests.agent.stub_broker import run_scenario

    result = run_scenario("good", timeout=60)
    assert result["approved"] is True and result["signature_valid"] is True


def test_replay_rejected():
    from tests.agent.stub_broker import run_scenario

    result = run_scenario("replay", timeout=60)
    assert result["approved"] is True and result["second_consume"] == "rejected"
```

(`run_scenario` spawns the real agent binary against an in-proto stub broker over a tmp socket, using `CONSENT_NO_UI=1` selftest approval path for headless runs; `tamper` and `kill` scenarios assert refusal and agent exit respectively.)

- [ ] **Step 2: Run; expect missing-stub failure.** Implement `stub_broker.py`: frozen challenge fixtures, Ed25519 daemon-signer using the fixture challenge key from Plan 2a's seed table (`b"\x01" * 32`; assert equality at import so the two plans cannot drift silently), P-256 approval verifier, exact-once map, scenario drivers (`good`, `tamper-display`, `replay`, `kill-mid-prompt` asserting agent process exits within 5s of socket close).
- [ ] **Step 3: Implement the rendezvous client (RV-1).** Wire: `uint32_be` length + payload frames, 64 KiB max, UTF-8 JSON, strict decode (duplicate keys and unknown protocol fields rejected). Handshake: `HELLO {version: "rv-1", agent_key_id, agent_nonce, expected_runtime_id}` → `CHALLENGE {version, runtime_id, agent_nonce, daemon_nonce, daemon_key_id, sig}` where `sig` covers `SHA256("telegram-mcp-rendezvous/v1" || JCS(transcript))` with the daemon challenge key; agent verifies against its keychain-pinned daemon key, then `READY {agent_nonce, daemon_nonce, sig}` signed with the agent **transport** identity key (a software Ed25519 key in the keychain pairing record — Touch ID approval keys never authenticate connections, so starting MCP costs no biometric prompt). 5s handshake deadline; either side closes on violation; prompt loop; auto-exit on disconnect + 2s grace. `kill-mid-prompt` scenario green proves no orphan UI.
- [ ] **Step 4: Run all agent tests headless; commit `feat: add rendezvous client and broker scenarios`.**

## Task 4: Pairing, LaunchAgent, signing

**Files:** Create `agent/ConsentAgent-Info.plist`; modify agent pairing commands; create `docs/verification/phase-2b.md` (skeleton; finalized in Task 5).

**Interfaces:** Consumes Tasks 1–3. Produces `pairing generate/export/import`, installed plist data, Developer ID signature.

- [ ] **Step 1: Write the failing pairing tests.**

```python
def test_adhoc_build_cannot_pair():
    gen = subprocess.run(
        [AGENT_BIN, "pairing", "generate"],
        capture_output=True, text=True, timeout=60,
    )
    assert gen.returncode != 0
    assert "ad-hoc" in (gen.stdout + gen.stderr).lower()


def test_signed_build_pairs():
    out = subprocess.run(
        [AGENT_BIN, "pairing-status"],
        capture_output=True, text=True, timeout=60,
    )
    assert "developer-id:" in out.stdout
```

(`pairing generate`/`import` on an ad-hoc build must exit nonzero with no Enclave identity created and no pairing record changed — assert both by re-checking keychain state in the test. `pairing-status` must match `codesign -dv` output, never self-report alone. The signed-build test runs only after Step 4 signing; before that it fails at the signature check — correct RED ordering.)

- [ ] **Step 2: Run; expect subcommand-missing failure.** Implement `pairing generate`: create the `SecAccessControl` from Step 3, generate the Enclave approval key, generate the software transport Ed25519 keypair, store the approval key's encrypted `dataRepresentation` plus the transport private key in the login keychain under the app access group, export both publics (approval SPKI for the daemon's approval slot, transport raw bytes for its transport slot) and print both `p256:`/`ed25519:` fingerprints. `pairing export` (public bytes b64url), `pairing import-daemon-pin <b64>` (writes keychain pairing record under the app access group; prints stored fingerprint for on-screen compare), `pairing-status` (signing identity + pairing record presence, public material only). Keychain-backed `KeyProvider` binds here for the Task 2 approval flow. Lifecycle tests: agent restart → same public key; keychain item deleted → re-pair required with a fixed error; simulated biometric-set change → key unavailable → re-pair required; different Mac → representation unrestorable (documented, asserted by design of Enclave binding, not executed).
- [ ] **Step 3: App-bundle packaging (no Xcode project) + LaunchAgent data.** Assemble `build/consent/TelegramMCPConsent.app/Contents/` (`MacOS/telegram-mcp-consent` binary, `Info.plist` with stable `CFBundleIdentifier`, `embedded.provisionprofile` authorizing the `keychain-access-groups` restricted entitlement, `_CodeSignature/` via `codesign` with Developer ID + Hardened Runtime). All tests invoke the bundle binary path. `agent/ConsentAgent-Info.plist`: `Label` com-specific, `RunAtLoad false`, `ProgramArguments` pointing at the bundle executable, `StandardOut/StandardError` to `/dev/null` (challenge/display payloads never reach ordinary user logs; a `--diagnostics` flag may emit redacted lifecycle lines only), `ProcessType Interactive`. Install/uninstall is Plan 2a's launcher calling `bootstrap`/`bootout`; this task validates the plist (`plutil -lint`) and documents the commands. Re-point the tests' `AGENT_BIN` constant at the bundle binary and re-run the whole agent suite green against it. Add a test asserting no challenge/display bytes appear in any agent output file after a scenario run.
- [ ] **Step 4: Sign with Developer ID.** Discover the identity with `security find-identity -v -p codesigning` and use the `Developer ID Application` line verbatim (`codesign -s "<name>" --options runtime ...`); verify `codesign -dv`, re-run pairing-identity test green; commit `feat: add pairing, LaunchAgent data, signing`.

## Task 5: Evidence and handoff

**Files:** Modify `docs/verification/phase-2b.md`, `AGENT.md`, `CHANGELOG.md`.

- [ ] **Step 1: Reproducibility gate.**

```bash
mkdir -p build/consent && swiftc -O agent/consent-agent.swift -o build/consent/telegram-mcp-consent
uv run pytest tests/agent -q
plutil -lint agent/ConsentAgent-Info.plist
codesign -dv build/consent/telegram-mcp-consent
```

- [ ] **Step 2: Write `docs/verification/phase-2b.md`** (build flags, signing identity team, JCS vector match count, scenario results, pairing fingerprints — public material only, no private keys, no challenge secrets).
- [ ] **Step 3: Update `AGENT.md`/`CHANGELOG.md`** with the Raouf template; commit `test: qualify the Swift consent agent`.

## Phase-2b acceptance and explicit exclusions

Complete when JCS bytes equal Plan 2a vectors, tamper/replay/kill scenarios pass headless, pairing + signing verify, plist lints, and evidence states limits. No daemon integration beyond the stub broker, no production install, and no authority logic in the agent (it verifies and signs; it never decides policy) are claimed.
