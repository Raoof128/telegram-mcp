// agent/consent-agent.swift — Phase-2b Swift consent agent (Tasks 1-2).
//
// Task 1: TG-JCS-v1 canonical encoder + Ed25519 challenge verification, with
// `selftest-jcs` / `selftest-verify` subcommands — pure computation, headless.
//
// Task 2: the display gate and the approval flow. Order is fixed and no step
// may be skipped: verify the daemon Ed25519 signature over the exact
// challenge bytes -> recompute SHA256("telegram-mcp-display-v1" || JCS(display))
// and compare it in constant time against the challenge's own
// `display_digest` -> render -> Touch ID on a fresh LAContext -> obtain the
// approval key with that same context -> sign the exact challenge bytes ->
// emit the ApprovalEnvelope. A display payload that does not hash to the
// digest inside the signed challenge never reaches LocalAuthentication, so
// what the operator reads and what the daemon receives cannot diverge.
//
// The agent decides nothing. It verifies, shows and signs; every policy
// question belongs to the daemon.
//
// TG-JCS-v1 (frozen wire contract): ASCII property names only, keys sorted
// (byte-wise UTF-8 order), `,`/`:` separators, literal UTF-8, escaping that
// is byte-identical to Python `json.dumps(..., ensure_ascii=False)`:
//   `"` `\` plus U+0000–U+001F only; short forms `\b \t \n \f \r` where
//   Python emits them, `\u00xx` lowercase hex otherwise; C1-and-above stay
//   literal UTF-8. Values: str/int/bool/null/array/object. Any float,
//   lone surrogate, or non-ASCII key is a fatal error (parity with Plan 2a).

import CryptoKit
import Foundation
import LocalAuthentication

// MARK: - Errors

enum JCSError: Error, CustomStringConvertible {
    case floatValue
    case loneSurrogate
    case nonASCIIKey(String)
    case nonCanonical(String)

    var description: String {
        switch self {
        case .floatValue: return "non-canonical float"
        case .loneSurrogate: return "lone surrogate"
        case let .nonASCIIKey(k): return "non-ASCII key: \(k)"
        case let .nonCanonical(m): return "non-canonical value: \(m)"
        }
    }
}

// MARK: - Canonical value model (floats unrepresentable by construction)

enum JCSValue {
    case null
    case boolean(Bool)
    case integer(Int64)
    case string(String)
    case array([JCSValue])
    case object([(key: String, value: JCSValue)])
}

/// Strict Foundation → JCSValue conversion. Floats (any JSON number that
/// JSONSerialization stores as floating-point, e.g. `1.5`, `1e3`, `1.0`)
/// throw; booleans are separated from integers via the CFBoolean type ID.
func jcsFromFoundation(_ obj: Any) throws -> JCSValue {
    if obj is NSNull {
        return .null
    }
    if CFGetTypeID(obj as CFTypeRef) == CFBooleanGetTypeID() {
        return .boolean((obj as! NSNumber).boolValue)
    }
    if let n = obj as? NSNumber {
        guard CFNumberGetType(n) == .sInt64Type else {
            throw JCSError.floatValue
        }
        return .integer(n.int64Value)
    }
    if let s = obj as? String {
        for scalar in s.unicodeScalars where (0xD8_00 ... 0xDF_FF).contains(scalar.value) {
            throw JCSError.loneSurrogate
        }
        return .string(s)
    }
    if let a = obj as? [Any] {
        return .array(try a.map(jcsFromFoundation))
    }
    if let d = obj as? [String: Any] {
        var pairs: [(key: String, value: JCSValue)] = []
        pairs.reserveCapacity(d.count)
        for (k, v) in d {
            guard k.unicodeScalars.allSatisfy({ $0.value < 128 }) else {
                throw JCSError.nonASCIIKey(k)
            }
            pairs.append((key: k, value: try jcsFromFoundation(v)))
        }
        return .object(pairs)
    }
    throw JCSError.nonCanonical(String(describing: type(of: obj)))
}

/// Decode arbitrary JSON document data into a strict canonical value.
/// Floats, lone surrogates, and non-ASCII keys throw.
func jcsDecode(_ data: Data) throws -> JCSValue {
    let obj = try JSONSerialization.jsonObject(with: data, options: [.allowFragments])
    return try jcsFromFoundation(obj)
}

// MARK: - TG-JCS-v1 encoder (byte-identical to Python ensure_ascii=False)

private func lowercaseHexDigit(_ nibble: UInt32) -> UInt8 {
    nibble < 10 ? 0x30 + UInt8(nibble) : 0x61 + UInt8(nibble - 10)
}

func jcsEncodeString(_ s: String, into out: inout Data) throws {
    out.append(0x22) // "
    for scalar in s.unicodeScalars {
        let v = scalar.value
        switch v {
        case 0x22: // "
            out.append(contentsOf: [0x5C, 0x22])
        case 0x5C: // \
            out.append(contentsOf: [0x5C, 0x5C])
        case 0x08: // Python short escape \b
            out.append(contentsOf: [0x5C, 0x62])
        case 0x09: // \t
            out.append(contentsOf: [0x5C, 0x74])
        case 0x0A: // \n
            out.append(contentsOf: [0x5C, 0x6E])
        case 0x0C: // \f
            out.append(contentsOf: [0x5C, 0x66])
        case 0x0D: // \r
            out.append(contentsOf: [0x5C, 0x72])
        case 0x00 ... 0x1F: // remaining C0 controls: \u00xx lowercase
            out.append(contentsOf: [0x5C, 0x75, 0x30, 0x30, lowercaseHexDigit((v >> 4) & 0xF), lowercaseHexDigit(v & 0xF)])
        case 0xD8_00 ... 0xDF_FF:
            throw JCSError.loneSurrogate
        default: // literal UTF-8, C1-and-above included
            out.append(contentsOf: String(scalar).utf8)
        }
    }
    out.append(0x22) // "
}

func jcsEncodeValue(_ value: JCSValue, into out: inout Data) throws {
    switch value {
    case .null:
        out.append(contentsOf: [0x6E, 0x75, 0x6C, 0x6C])
    case let .boolean(b):
        out.append(contentsOf: b ? [0x74, 0x72, 0x75, 0x65] : [0x66, 0x61, 0x6C, 0x73, 0x65])
    case let .integer(i):
        out.append(contentsOf: String(i).utf8)
    case let .string(s):
        try jcsEncodeString(s, into: &out)
    case let .array(items):
        out.append(0x5B) // [
        for (index, item) in items.enumerated() {
            if index > 0 { out.append(0x2C) } // ,
            try jcsEncodeValue(item, into: &out)
        }
        out.append(0x5D) // ]
    case let .object(pairs):
        out.append(0x7B) // {
        // Byte-wise UTF-8 order == codepoint order for ASCII keys.
        let sorted = pairs.sorted { $0.key.utf8.lexicographicallyPrecedes($1.key.utf8) }
        for (index, pair) in sorted.enumerated() {
            if index > 0 { out.append(0x2C) } // ,
            guard pair.key.unicodeScalars.allSatisfy({ $0.value < 128 }) else {
                throw JCSError.nonASCIIKey(pair.key)
            }
            try jcsEncodeString(pair.key, into: &out)
            out.append(0x3A) // :
            try jcsEncodeValue(pair.value, into: &out)
        }
        out.append(0x7D) // }
    }
}

/// Canonical TG-JCS-v1 bytes for a value. Throws on float (unrepresentable),
/// lone surrogate, or non-ASCII key.
func jcsEncode(_ value: JCSValue) throws -> Data {
    var out = Data()
    try jcsEncodeValue(value, into: &out)
    return out
}

// MARK: - Ed25519 challenge verification

/// Verify a daemon Ed25519 signature over the exact JCS bytes.
/// Never throws: malformed keys/signatures verify as false.
func verifyChallenge(jcs: Data, sig: Data, daemonPub: Data) -> Bool {
    guard sig.count == 64,
        let pub = try? Curve25519.Signing.PublicKey(rawRepresentation: daemonPub)
    else {
        return false
    }
    return pub.isValidSignature(sig, for: jcs)
}

// MARK: - Small codecs

func hexDecode(_ hex: String) -> Data? {
    guard hex.count % 2 == 0 else { return nil }
    var out = Data()
    out.reserveCapacity(hex.count / 2)
    var index = hex.startIndex
    while index < hex.endIndex {
        let next = hex.index(index, offsetBy: 2)
        guard let byte = UInt8(hex[index ..< next], radix: 16) else { return nil }
        out.append(byte)
        index = next
    }
    return out
}

func base64URLDecode(_ text: String) -> Data? {
    guard !text.isEmpty, text.allSatisfy({ $0.isASCII && "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_".contains($0) }) else {
        return nil
    }
    var std = text.replacingOccurrences(of: "-", with: "+").replacingOccurrences(of: "_", with: "/")
    std += String(repeating: "=", count: (4 - std.count % 4) % 4)
    return Data(base64Encoded: std)
}

func eprint(_ s: String) {
    FileHandle.standardError.write(Data((s + "\n").utf8))
}

// MARK: - Vectors-file loading (fails closed)

struct VectorCase {
    let name: String
    let input: JCSValue
    let jcsBytes: Data
    let sig: Data
    let displayInput: JCSValue?
    let displayDigest: Data?
}

func loadVectors(at path: String) throws -> (daemonPub: Data, cases: [VectorCase]) {
    guard let data = FileManager.default.contents(atPath: path) else {
        throw JCSError.nonCanonical("vectors file absent: \(path) (Plan 2a dependency: tests/fixtures/consent/jcs_vectors.json)")
    }
    let top: Any
    do {
        top = try JSONSerialization.jsonObject(with: data, options: [])
    } catch {
        throw JCSError.nonCanonical("vectors file is not valid JSON: \(path)")
    }
    guard let dict = top as? [String: Any],
        let pubHex = dict["challenge_key_public_hex"] as? String,
        let daemonPub = hexDecode(pubHex),
        let rawCases = dict["cases"] as? [[String: Any]]
    else {
        throw JCSError.nonCanonical("vectors file missing challenge_key_public_hex/cases: \(path)")
    }
    var cases: [VectorCase] = []
    for raw in rawCases {
        guard let name = raw["name"] as? String,
            let input = raw["input"],
            let jcsHex = raw["jcs_hex"] as? String,
            let jcsBytes = hexDecode(jcsHex),
            let sigText = raw["sig"] as? String,
            let sig = base64URLDecode(sigText)
        else {
            throw JCSError.nonCanonical("malformed vector case in \(path)")
        }
        cases.append(VectorCase(
            name: name,
            input: try jcsFromFoundation(input),
            jcsBytes: jcsBytes,
            sig: sig,
            displayInput: try raw["display_input"].map(jcsFromFoundation),
            displayDigest: try raw["display_digest"].map { digestHex throws -> Data in
                guard let s = digestHex as? String, let d = hexDecode(s) else {
                    throw JCSError.nonCanonical("malformed display_digest in \(path)")
                }
                return d
            }
        ))
    }
    return (daemonPub, cases)
}

// MARK: - Display gate

let displayDomain = Data("telegram-mcp-display-v1".utf8)

/// `SHA256("telegram-mcp-display-v1" || JCS(payload))`.
func displayDigest(of payload: JCSValue) throws -> Data {
    var hasher = SHA256()
    hasher.update(data: displayDomain)
    hasher.update(data: try jcsEncode(payload))
    return Data(hasher.finalize())
}

/// Constant-time equality. Length is not secret; content comparison is.
func constantTimeEqual(_ a: Data, _ b: Data) -> Bool {
    guard a.count == b.count else { return false }
    var difference: UInt8 = 0
    for (x, y) in zip(a, b) { difference |= x ^ y }
    return difference == 0
}

/// Look up a top-level string field in a decoded challenge object.
func challengeField(_ challenge: JCSValue, _ key: String) -> String? {
    guard case let .object(pairs) = challenge else { return nil }
    for pair in pairs where pair.key == key {
        if case let .string(value) = pair.value { return value }
    }
    return nil
}

/// Presentation-only sanitation: strip C0/C1 and bidi/invisible formatting
/// controls and cap at 160 codepoints. The digest covers the original bytes,
/// so this changes what is shown and never what is signed.
func renderableText(_ text: String, limit: Int = 160) -> String {
    var out = String.UnicodeScalarView()
    for scalar in text.unicodeScalars {
        let v = scalar.value
        let isControl = v < 0x20 || (0x7F ... 0x9F).contains(v)
        let isBidi = (0x20_2A ... 0x20_2E).contains(v) || (0x20_66 ... 0x20_69).contains(v)
        let isInvisible = v == 0x20_0B || v == 0x20_0C || v == 0x20_0D || v == 0xFE_FF
        if isControl || isBidi || isInvisible { continue }
        out.append(scalar)
        if out.count >= limit { break }
    }
    return String(out)
}

/// One line per display field, in the frozen field order.
func renderedLines(from payload: JCSValue) -> [String] {
    guard case let .object(pairs) = payload else { return [] }
    var byKey: [String: JCSValue] = [:]
    for pair in pairs { byKey[pair.key] = pair.value }
    func text(_ key: String) -> String? {
        if case let .string(value)? = byKey[key] { return renderableText(value) }
        return nil
    }
    var lines: [String] = []
    if let client = text("client_display") { lines.append("client:  \(client)") }
    if let action = text("action_display") { lines.append("action:  \(action)") }
    if case let .array(projects)? = byKey["project_display"] {
        let names = projects.compactMap { item -> String? in
            if case let .string(value) = item { return renderableText(value) }
            return nil
        }
        if !names.isEmpty { lines.append("project: \(names.joined(separator: ", "))") }
    }
    if let peer = text("peer_display") { lines.append("peer:    \(peer)") }
    if let risk = text("risk_class") { lines.append("risk:    \(risk)") }
    return lines
}

// MARK: - Approval keys

/// The approval signer. Production is a Secure Enclave P-256 key obtained
/// with the same `LAContext` the biometric prompt ran on; the selftest
/// double is an in-memory key of the same shape.
protocol ApprovalKey {
    var publicKeyDER: Data { get }
    func signature(over data: Data) throws -> Data
}

extension ApprovalKey {
    /// `p256:sha256:<hex of SHA256 over the DER SubjectPublicKeyInfo>`.
    var keyID: String {
        "p256:sha256:" + Data(SHA256.hash(data: publicKeyDER)).map { String(format: "%02x", $0) }.joined()
    }
}

/// Supplies the approval key for exactly one consent.
protocol KeyProvider {
    func keyForApproval(context: LAContext?) throws -> ApprovalKey
}

enum ApprovalError: Error, CustomStringConvertible {
    case challengeSignatureInvalid
    case displayMismatch
    case malformedChallenge(String)
    case uiSuppressed
    case biometryUnavailable(String)
    case biometryFailed(String)
    case providerUnavailable(String)

    var description: String {
        switch self {
        case .challengeSignatureInvalid: return "CHALLENGE-SIGNATURE-INVALID"
        case .displayMismatch: return "DISPLAY-MISMATCH"
        case let .malformedChallenge(m): return "MALFORMED-CHALLENGE: \(m)"
        case .uiSuppressed: return "NO-UI: interactive approval is disabled in this environment"
        case let .biometryUnavailable(m): return "BIOMETRY-UNAVAILABLE: \(m)"
        case let .biometryFailed(m): return "BIOMETRY-FAILED: \(m)"
        case let .providerUnavailable(m): return "PROVIDER-UNAVAILABLE: \(m)"
        }
    }
}

struct ApprovalEnvelope {
    let challengeSHA256: String
    let sig: String
    let keyID: String

    /// The frozen wire struct, verified whole by the broker.
    var json: [String: String] {
        ["challenge_sha256": challengeSHA256, "sig": sig, "key_id": keyID]
    }
}

/// True when this environment forbids any interactive prompt. The production
/// path treats it as a refusal and never substitutes a key; only `selftest-`
/// subcommands take a non-prompting path, with their own key.
func uiSuppressed() -> Bool {
    ProcessInfo.processInfo.environment["CONSENT_NO_UI"] == "1"
}

/// Verify, gate on the display digest, then sign. `prompt` is the only step
/// that may interact with the operator; it runs strictly after the gate.
func approveFlow(
    challengeJCS: Data,
    daemonSig: Data,
    daemonPub: Data,
    displayPayload: JCSValue,
    provider: KeyProvider,
    prompt: (([String]) throws -> LAContext?)
) throws -> (envelope: ApprovalEnvelope, publicKeyDER: Data) {
    guard verifyChallenge(jcs: challengeJCS, sig: daemonSig, daemonPub: daemonPub) else {
        throw ApprovalError.challengeSignatureInvalid
    }
    let challenge = try jcsDecode(challengeJCS)
    guard let digestHex = challengeField(challenge, "display_digest"),
        let expected = hexDecode(digestHex), expected.count == 32
    else {
        throw ApprovalError.malformedChallenge("display_digest")
    }
    guard constantTimeEqual(try displayDigest(of: displayPayload), expected) else {
        throw ApprovalError.displayMismatch
    }
    let context = try prompt(renderedLines(from: displayPayload))
    defer { context?.invalidate() }
    let key = try provider.keyForApproval(context: context)
    let signature = try key.signature(over: challengeJCS)
    let envelope = ApprovalEnvelope(
        challengeSHA256: Data(SHA256.hash(data: challengeJCS)).map { String(format: "%02x", $0) }.joined(),
        sig: base64URLEncode(signature),
        keyID: key.keyID
    )
    return (envelope, key.publicKeyDER)
}

/// Fresh `LAContext` per consent, biometrics only, never reused across
/// prompts. Returns the context the key must be obtained with.
func touchIDPrompt(_ lines: [String]) throws -> LAContext? {
    if uiSuppressed() { throw ApprovalError.uiSuppressed }
    let context = LAContext()
    context.localizedCancelTitle = "Deny"
    var probe: NSError?
    guard context.canEvaluatePolicy(.deviceOwnerAuthenticationWithBiometrics, error: &probe) else {
        throw ApprovalError.biometryUnavailable(probe?.localizedDescription ?? "unavailable")
    }
    for line in lines { eprint(line) }
    let reason = "Approve this Telegram disclosure:\n" + lines.joined(separator: "\n")
    let gate = DispatchSemaphore(value: 0)
    var approved = false
    var failure: String?
    context.evaluatePolicy(.deviceOwnerAuthenticationWithBiometrics, localizedReason: reason) { ok, error in
        approved = ok
        failure = error?.localizedDescription
        gate.signal()
    }
    gate.wait()
    guard approved else {
        context.invalidate()
        throw ApprovalError.biometryFailed(failure ?? "denied")
    }
    return context
}

func base64URLEncode(_ data: Data) -> String {
    data.base64EncodedString()
        .replacingOccurrences(of: "+", with: "-")
        .replacingOccurrences(of: "/", with: "_")
        .replacingOccurrences(of: "=", with: "")
}

func emitApproval(_ envelope: ApprovalEnvelope, publicKeyDER: Data) {
    let payload: [String: Any] = [
        "envelope": envelope.json,
        "public_key_der_b64url": base64URLEncode(publicKeyDER),
    ]
    guard let data = try? JSONSerialization.data(withJSONObject: payload, options: [.sortedKeys]),
        let text = String(data: data, encoding: .utf8)
    else {
        eprint("approval: could not encode output")
        exit(1)
    }
    print(text)
}

// MARK: - Selftests

func selfTestJCS(vectorsPath: String?) -> Int32 {
    guard let path = vectorsPath else {
        eprint("selftest-jcs: missing <vectors-file> argument")
        return 2
    }
    let vectors: (daemonPub: Data, cases: [VectorCase])
    do {
        vectors = try loadVectors(at: path)
    } catch {
        eprint("selftest-jcs: \(error)")
        return 2
    }
    _ = vectors.daemonPub // JCS test pins bytes only; key pinning lives in selftest-verify.
    var sawNonASCII = false
    for c in vectors.cases {
        let encoded: Data
        do {
            encoded = try jcsEncode(c.input)
        } catch {
            eprint("selftest-jcs: case '\(c.name)' encode error: \(error)")
            return 1
        }
        if encoded.contains(where: { $0 >= 128 }) {
            sawNonASCII = true
        }
        if encoded != c.jcsBytes {
            eprint("selftest-jcs: case '\(c.name)' byte mismatch")
            return 1
        }
        // The non-ASCII / control / escape vectors live in display_input;
        // pin their encoding via the frozen display_digest
        // (SHA256("telegram-mcp-display-v1" || JCS(display_input))).
        if let displayInput = c.displayInput, let displayDigest = c.displayDigest {
            let displayJCS: Data
            do {
                displayJCS = try jcsEncode(displayInput)
            } catch {
                eprint("selftest-jcs: case '\(c.name)' display encode error: \(error)")
                return 1
            }
            if displayJCS.contains(where: { $0 >= 128 }) {
                sawNonASCII = true
            }
            var hasher = SHA256()
            hasher.update(data: Data("telegram-mcp-display-v1".utf8))
            hasher.update(data: displayJCS)
            if Data(hasher.finalize()) != displayDigest {
                eprint("selftest-jcs: case '\(c.name)' display digest mismatch")
                return 1
            }
        }
    }
    guard sawNonASCII else {
        eprint("selftest-jcs: no non-ASCII case covered")
        return 1
    }
    print("JCS-OK")
    return 0
}

func selfTestVerify(vectorsPath: String?) -> Int32 {
    guard let path = vectorsPath else {
        eprint("selftest-verify: missing <vectors-file> argument")
        return 2
    }
    let vectors: (daemonPub: Data, cases: [VectorCase])
    do {
        vectors = try loadVectors(at: path)
    } catch {
        eprint("selftest-verify: \(error)")
        return 2
    }
    // Pin the normative fixture seed: derive the expected daemon public key
    // from b"\x01"*32 and require the vectors file to agree.
    let fixtureChallengeSeed = Data(repeating: 0x01, count: 32)
    guard let seedKey = try? Curve25519.Signing.PrivateKey(rawRepresentation: fixtureChallengeSeed) else {
        eprint("selftest-verify: cannot derive fixture key")
        return 2
    }
    let expectedPub = seedKey.publicKey.rawRepresentation
    guard vectors.daemonPub == expectedPub else {
        eprint("selftest-verify: vectors daemon pubkey does not match fixture seed b\"\\x01\"*32")
        return 1
    }
    guard !vectors.cases.isEmpty else {
        eprint("selftest-verify: no cases")
        return 1
    }
    for c in vectors.cases {
        guard verifyChallenge(jcs: c.jcsBytes, sig: c.sig, daemonPub: vectors.daemonPub) else {
            eprint("selftest-verify: case '\(c.name)' valid signature rejected")
            return 1
        }
    }
    // Tamper rejection: one-bit flip in the first case's signature must fail.
    var tampered = vectors.cases[0].sig
    tampered[0] ^= 0x01
    if verifyChallenge(jcs: vectors.cases[0].jcsBytes, sig: tampered, daemonPub: vectors.daemonPub) {
        eprint("selftest-verify: tampered signature accepted")
        return 1
    }
    print("VERIFY-OK")
    return 0
}

/// Direct-construction rejection probe: builds a `JCSValue.object` with a
/// non-ASCII key *without* going through decode, and requires `jcsEncode`
/// to throw (the encoder must re-assert ASCII keys itself).
func selfTestJCSRejectsNonASCIIKey() -> Int32 {
    let direct = JCSValue.object([(key: "cl\u{00E9}", value: .integer(1))])
    do {
        _ = try jcsEncode(direct)
    } catch JCSError.nonASCIIKey {
        print("REJECT-OK")
        return 0
    } catch {
        eprint("selftest-jcs-rejects-nonascii-key: wrong error: \(error)")
        return 1
    }
    eprint("selftest-jcs-rejects-nonascii-key: non-ASCII key encoded instead of throwing")
    return 1
}

/// In-memory approval key of the production shape. Selftests only: the
/// production path can only reach a Secure Enclave key.
struct EphemeralApprovalKey: ApprovalKey {
    private let key: P256.Signing.PrivateKey

    /// Deterministic when `CONSENT_SELFTEST_APPROVAL_SEED` (64 hex) is set,
    /// so a stub broker can pin the public key in advance.
    init() throws {
        if let hex = ProcessInfo.processInfo.environment["CONSENT_SELFTEST_APPROVAL_SEED"],
            let seed = hexDecode(hex), seed.count == 32,
            let derived = try? P256.Signing.PrivateKey(rawRepresentation: seed)
        {
            key = derived
        } else {
            key = P256.Signing.PrivateKey()
        }
    }

    var publicKeyDER: Data { key.publicKey.derRepresentation }

    func signature(over data: Data) throws -> Data {
        try key.signature(for: data).derRepresentation
    }
}

struct EphemeralApprovalKeyProvider: KeyProvider {
    func keyForApproval(context: LAContext?) throws -> ApprovalKey {
        guard context == nil else {
            throw ApprovalError.providerUnavailable("selftest key must not be used with a prompt")
        }
        return try EphemeralApprovalKey()
    }
}

/// Selftest prompt: proves the gate ran before any UI could. Reaching it in a
/// selftest is itself the failure signal the tests grep for.
func selfTestNoPrompt(_ lines: [String]) throws -> LAContext? {
    eprint("PROMPT-ATTEMPTED")
    throw ApprovalError.uiSuppressed
}

func selfTestSilentPrompt(_ lines: [String]) throws -> LAContext? {
    for line in lines { eprint("render: " + line) }
    return nil
}

/// Append one codepoint to the first display string, leaving the signed
/// challenge (and therefore its `display_digest`) untouched.
func mutateFirstString(_ value: JCSValue) -> JCSValue {
    guard case let .object(pairs) = value else { return value }
    var mutated = pairs
    for (index, pair) in pairs.enumerated() where pair.key == "action_display" {
        if case let .string(text) = pair.value {
            mutated[index] = (key: pair.key, value: .string(text + "x"))
            return .object(mutated)
        }
    }
    for (index, pair) in pairs.enumerated() {
        if case let .string(text) = pair.value {
            mutated[index] = (key: pair.key, value: .string(text + "x"))
            return .object(mutated)
        }
    }
    return value
}

func loadCase(_ vectorsPath: String?, _ indexText: String?, subcommand: String)
    -> (daemonPub: Data, vectorCase: VectorCase)?
{
    guard let path = vectorsPath else {
        eprint("\(subcommand): missing <vectors-file> argument")
        return nil
    }
    let vectors: (daemonPub: Data, cases: [VectorCase])
    do {
        vectors = try loadVectors(at: path)
    } catch {
        eprint("\(subcommand): \(error)")
        return nil
    }
    let index = Int(indexText ?? "0") ?? 0
    guard index >= 0, index < vectors.cases.count else {
        eprint("\(subcommand): case index out of range")
        return nil
    }
    let chosen = vectors.cases[index]
    guard chosen.displayInput != nil else {
        eprint("\(subcommand): case '\(chosen.name)' carries no display payload")
        return nil
    }
    return (vectors.daemonPub, chosen)
}

func selfTestDisplayTamper(vectorsPath: String?) -> Int32 {
    guard let loaded = loadCase(vectorsPath, "0", subcommand: "selftest-display-tamper") else {
        return 2
    }
    let tampered = mutateFirstString(loaded.vectorCase.displayInput!)
    do {
        _ = try approveFlow(
            challengeJCS: loaded.vectorCase.jcsBytes,
            daemonSig: loaded.vectorCase.sig,
            daemonPub: loaded.daemonPub,
            displayPayload: tampered,
            provider: EphemeralApprovalKeyProvider(),
            prompt: selfTestNoPrompt
        )
    } catch let error as ApprovalError {
        eprint("selftest-display-tamper: \(error)")
        return error.description.hasPrefix("DISPLAY-MISMATCH") ? 1 : 3
    } catch {
        eprint("selftest-display-tamper: unexpected error: \(error)")
        return 3
    }
    eprint("selftest-display-tamper: tampered display was approved")
    return 4
}

func selfTestApprove(vectorsPath: String?, indexText: String?) -> Int32 {
    guard let loaded = loadCase(vectorsPath, indexText, subcommand: "selftest-approve") else {
        return 2
    }
    do {
        let result = try approveFlow(
            challengeJCS: loaded.vectorCase.jcsBytes,
            daemonSig: loaded.vectorCase.sig,
            daemonPub: loaded.daemonPub,
            displayPayload: loaded.vectorCase.displayInput!,
            provider: EphemeralApprovalKeyProvider(),
            prompt: selfTestSilentPrompt
        )
        emitApproval(result.envelope, publicKeyDER: result.publicKeyDER)
        return 0
    } catch {
        eprint("selftest-approve: \(error)")
        return 1
    }
}

func selfTestApproveWrongDaemonKey(vectorsPath: String?) -> Int32 {
    guard let loaded = loadCase(vectorsPath, "0", subcommand: "selftest-approve-wrong-daemon-key")
    else {
        return 2
    }
    // A syntactically valid Ed25519 public key that did not sign this challenge.
    guard let impostor = try? Curve25519.Signing.PrivateKey(
        rawRepresentation: Data(repeating: 0x02, count: 32)
    ) else {
        eprint("selftest-approve-wrong-daemon-key: cannot derive impostor key")
        return 2
    }
    do {
        _ = try approveFlow(
            challengeJCS: loaded.vectorCase.jcsBytes,
            daemonSig: loaded.vectorCase.sig,
            daemonPub: impostor.publicKey.rawRepresentation,
            displayPayload: loaded.vectorCase.displayInput!,
            provider: EphemeralApprovalKeyProvider(),
            prompt: selfTestNoPrompt
        )
    } catch let error as ApprovalError {
        eprint("selftest-approve-wrong-daemon-key: \(error)")
        return error.description.hasPrefix("CHALLENGE-SIGNATURE-INVALID") ? 1 : 3
    } catch {
        eprint("selftest-approve-wrong-daemon-key: unexpected error: \(error)")
        return 3
    }
    eprint("selftest-approve-wrong-daemon-key: forged challenge was approved")
    return 4
}

// MARK: - Production key provider

/// Bound by Plan 2b Task 4 to the Secure Enclave key in the operator
/// keychain. Anything else is a refusal.
func productionKeyProvider() -> KeyProvider { EnclaveKeyProvider() }

struct EnclaveKeyProvider: KeyProvider {
    func keyForApproval(context: LAContext?) throws -> ApprovalKey {
        throw ApprovalError.providerUnavailable(
            "no paired Secure Enclave approval key (Plan 2b Task 4)"
        )
    }
}

// MARK: - Production approval command

/// The operator-facing approval path: real pin, real prompt, real key.
/// Until Plan 2b Task 4 provisions the Secure Enclave key and the keychain
/// pairing record, the provider refuses with a fixed message rather than
/// falling back to anything weaker.
func approveCommand(vectorsPath: String?, indexText: String?) -> Int32 {
    guard let loaded = loadCase(vectorsPath, indexText, subcommand: "approve") else {
        return 2
    }
    do {
        let result = try approveFlow(
            challengeJCS: loaded.vectorCase.jcsBytes,
            daemonSig: loaded.vectorCase.sig,
            daemonPub: loaded.daemonPub,
            displayPayload: loaded.vectorCase.displayInput!,
            provider: productionKeyProvider(),
            prompt: touchIDPrompt
        )
        emitApproval(result.envelope, publicKeyDER: result.publicKeyDER)
        return 0
    } catch {
        eprint("approve: \(error)")
        return 1
    }
}

// MARK: - Entry point
//
// NOTE: written as top-level dispatch into `ConsentAgent.main()` rather than
// `@main`, because this toolchain's single-file driver defaults to script
// mode and rejects `@main` without `-parse-as-library`; the frozen build
// line (`swiftc -O agent/consent-agent.swift -o ...`) carries no such flag.

struct ConsentAgent {
    static let usage = """
    usage: telegram-mcp-consent <command> [arguments]

      approve <vectors-file> [index]   verify, show and sign one challenge
      selftest-jcs <vectors-file>
      selftest-verify <vectors-file>
      selftest-jcs-rejects-nonascii-key
      selftest-display-tamper <vectors-file>
      selftest-approve <vectors-file> [index]
      selftest-approve-wrong-daemon-key <vectors-file>
    """

    static func main() {
        let args = CommandLine.arguments
        guard args.count >= 2 else {
            eprint(usage)
            exit(2)
        }
        let path = args.count >= 3 ? args[2] : nil
        let extra = args.count >= 4 ? args[3] : nil
        switch args[1] {
        case "approve":
            exit(approveCommand(vectorsPath: path, indexText: extra))
        case "selftest-jcs":
            exit(selfTestJCS(vectorsPath: path))
        case "selftest-verify":
            exit(selfTestVerify(vectorsPath: path))
        case "selftest-jcs-rejects-nonascii-key":
            exit(selfTestJCSRejectsNonASCIIKey())
        case "selftest-display-tamper":
            exit(selfTestDisplayTamper(vectorsPath: path))
        case "selftest-approve":
            exit(selfTestApprove(vectorsPath: path, indexText: extra))
        case "selftest-approve-wrong-daemon-key":
            exit(selfTestApproveWrongDaemonKey(vectorsPath: path))
        default:
            eprint("unknown subcommand: \(args[1])")
            exit(2)
        }
    }
}

ConsentAgent.main()
