# WhatsVaultApproval (iOS) — STRUCTURED, Apple-Developer-gated (not built in CI)

Recorded contract; buildable under a free Personal Team for SE/core dev, paid membership
for APNs/distribution (ledger #49).

- **Two** Secure Enclave P-256 keys (ledger #5): a Signing key and a KeyAgreement key,
  both `[.privateKeyUsage, .biometryCurrentSet]`, fresh `LAContext` per signature, reuse
  duration 0, no passcode fallback.
- Canonical encoder reproducing `tests/golden/decision_vectors.json` **byte-for-byte**
  (CI gate both directions: Swift-sign → Python-verify, Python-sign → Swift-verify).
- Enrolment signs `DOMAIN||pairing_id||challenge||signing_pub||agreement_pub` (ledger #6);
  fetches device-sealed draft detail (P-256 ECDH, `WVD1` envelope), decrypts locally,
  renders through the Persian-aware display guard (ledger #15), shows the masked recipient
  from the signed `recipient_wa_id`, offers **Approve & Send** only when the guard is clear.
