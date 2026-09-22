// agent/consent-agent.swift — Phase-2b Swift consent agent (Task 1).
//
// Task 1 scope: TG-JCS-v1 canonical encoder + Ed25519 challenge verification
// + `selftest-jcs` / `selftest-verify` subcommands. No keychain, no codesign,
// no Touch ID here — pure computation, fully headless.
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

// MARK: - Entry point
//
// NOTE: written as top-level dispatch into `ConsentAgent.main()` rather than
// `@main`, because this toolchain's single-file driver defaults to script
// mode and rejects `@main` without `-parse-as-library`; the frozen build
// line (`swiftc -O agent/consent-agent.swift -o ...`) carries no such flag.

struct ConsentAgent {
    static func main() {
        let args = CommandLine.arguments
        guard args.count >= 2 else {
            eprint("usage: telegram-mcp-consent <selftest-jcs|selftest-verify> <vectors-file>")
            exit(2)
        }
        let path = args.count >= 3 ? args[2] : nil
        switch args[1] {
        case "selftest-jcs":
            exit(selfTestJCS(vectorsPath: path))
        case "selftest-verify":
            exit(selfTestVerify(vectorsPath: path))
        default:
            eprint("unknown subcommand: \(args[1])")
            exit(2)
        }
    }
}

ConsentAgent.main()
