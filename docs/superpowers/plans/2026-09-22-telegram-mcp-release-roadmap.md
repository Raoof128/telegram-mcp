# Telegram MCP V0.1.10 Release Roadmap

**Status:** Draft for review; authorizes no implementation or production deployment.

**Goal:** Deliver the supplied ten-tool, single-owner Telegram gateway with project isolation, human consent and accountable disclosure, then qualify the exact release artifact against Gates A–R.

**Spec:** [Telegram MCP V0.1.10 engineering specification](../../../telegram-mcp-v0.1.10-final-engineering-spec.md).

**Source SHA-256:** `36b67f488415f2ab1c44b8d906de7f192fbe0dc562a2aeac76938b24c4a61b0a`.

**Detailed first phase:** [Synthetic protocol and contract foundation](2026-09-22-telegram-mcp-phase-1-foundation.md).

## Intended outcome and current state

Raouf wants bounded access to an existing Telegram user account from ChatGPT, Codex and/or Claude Code, with separate gateway projects such as society namespaces. The gateway retrieves structured data; the calling model performs summaries. Ordinary calls stay inside one explicit project. Cross-project search is a deliberate, separately granted and consented operation.

The supplied specification fixes the architecture. This roadmap organizes its implementation rather than reopening the product design. The user selected a release roadmap plus a detailed first-phase plan. The first phase is assumed to be a synthetic foundation; its review is the opportunity to adjust that boundary.

At planning time the workspace contains only the supplied specification, with no product code, dependency lock, tests or Git repository. No production capability or acceptance gate has been established. This planning change adds documents only.

## Fixed boundaries

- Exactly ten tools; `telegram_status` alone is non-sensitive. All nine other tools require daemon-enforced user presence, including project discovery and empty successful results.
- One `telegram-mcpd` process owns the Telethon session and metadata database. The production daemon, tunnel and interactive consent agent occupy separate privilege boundaries.
- Effective access is the intersection of owner scope, gateway-project membership and authenticated client-project permission. Client metadata, repository paths and ChatGPT Projects are routing hints only.
- Production defaults to an allowlist with archived chats excluded. No Telegram writes, read acknowledgements, media downloads, external URL fetching, raw MTProto passthrough or body/search indexing.
- A disclosure must pass consent, reservation, revalidation, egress, receipt/exposure/audit commit and external audit-anchor publication before sensitive transport handoff.
- Direct public HTTPS/OAuth, optional inspector UI and optional workflow bundles are outside the required implementation phases. The ordinary authenticated loopback coding-client ingress remains part of production.
- The original spec remains unchanged during planning. Corrections below must become reviewed contract decisions before the affected implementation is accepted.

## Corrections and contract clarifications

These findings distinguish demonstrable defects from engineering choices. Line numbers refer to the original source hash above.

| ID | Evidence | Required treatment | Owning phase |
|---|---|---|---|
| C1 | Section 47, lines 3872–3898, puts final A–R before mandatory disclosure/security work | Use the dependency order below. Final A–R follows every required subsystem. Earlier sensitive tools stay synthetic or fail closed. | All |
| C2 | `client_projects` CHECK, lines 1195–1206, accepts `excerpt` plus SQL NULL | Add `excerpt_max_codepoints IS NOT NULL` to the excerpt branch, preserving the exact 64–4000 range. Prove invalid direct inserts fail. | 2 |
| C3 | E.4/E.5 use root `#/$defs/message` references at 4307/4364; E.10 embeds their definitions below `data` | Hoist definitions to the assembled schema root, rejecting name collisions. Validate non-empty messages/context against the complete advertised output schema. | 1 |
| C4 | Membership check at 1181 considers only enabled projects; frozen overlap rules at 3945/3977 do not | Proposed rule: inspect all retained memberships when adding a peer; every overlap requires an explicit shared action. Enabling a project rejects undeclared overlaps. Enable/disable changes increment `project_epoch` and invalidate affected authority. | 2 |
| C5 | Section 38.8 still requires direct-HTTPS OAuth tests; Sections 8.3/48 disable that release profile | Mark OAuth tests conditional on a future approved profile. V0.1.10 tests startup refusal for direct HTTPS with real data. | 1, 7 |
| C6 | Appendix D, line 4101, says `0.1.2` | Generated release labels and package version must say `0.1.10`. Retain older versions only in explicitly historical material. | 1, 7 |
| C7 | Invariant 17, line 2540, calls tunnel mTLS the only production ingress | Read “only remote production route”; retain the separately authenticated coding-client loopback ingress required by Sections 8/48. | 2, 6 |
| C8 | V0.1.10 consent text reconstructs the UI from validated arguments but freezes no display binding in the challenge tuple | Add `display_digest = SHA256("telegram-mcp-display-v1" \|\| JCS(display_payload))` to the signed challenge; the agent renders only on constant-time equality. Recorded here so the strengthening is explicit, not silent divergence. | 2 |

Mechanical evidence gathered while planning: all 33 JSON fences parse with duplicate-key rejection; an in-memory SQLite reproduction accepts the erroneous excerpt/NULL combination; literal schema assembly leaves the two message references without root definitions. JSON syntax passing does not prove schema semantics.

## Engineering decisions to carry into subsequent phase plans

1. **Shared disclosure coordinator.** Tool handlers never independently implement consent or disclosure transactions. A coordinator owns immutable requests, authority generations, exposure reservations, transformation, commit and handoff. No sensitive handler receives a bypass interface.
2. **Revocation ordering.** The Phase 3 design must serialize authority mutations with the final disclosure decision. Recommended boundary: a short disclosure barrier covers final revalidation, SQLite commit, monotonic anchor publication and handoff to the transport. No Telegram RPC or human prompt holds that barrier. If lock/revoke wins first, discard the payload; if disclosure handoff wins first, it is already an accounted disclosure. A committed result cancelled before handoff remains charged. Test both legal orderings and delayed anchor I/O before accepting this design.
3. **Anchor ordering.** Serialize database audit appends and external anchor publication. An older publisher cannot overwrite a newer anchor. Anchor failure retains the already committed receipt/charge and enters the specified degraded state; repair is user-presence gated.
4. **Canonical preimages.** Phase 3 must freeze typed JCS vectors for each sensitive tool, including project-catalogue and empty results, per-record truncation and provenance ordering. Freeze exposure-snapshot window boundaries and fields in golden fixtures. Generic sorted JSON is not a substitute for JCS signing.
5. **Budget estimation.** Start with a conservative ceiling derived from each tool's record bound and the 65,536-byte response cap; reserve globally and for each potentially contributing selected project. Display the same estimate in consent. Release unused reservation at commit. The estimator must be demonstrably an upper bound and must not omit shared-project charges. Any estimate shortfall refuses disclosure.
6. **Egress.** For each record use the most restrictive contributing project grant; the response's reported level is the maximum level actually present across its records. Metadata-only catalogues still require consent, receipts and global exposure accounting.
7. **Coverage.** Select bounded partial search with deterministic continuation when eligible peers exceed the configured bound; report `peer_budget` truthfully. The Phase 4 plan must define anchors and per-project coverage before implementing continuation. Do not advertise complete search after examining only a subset.
8. **Verification claims.** Provide receipt-attestation verification separately from live-result provenance/coverage recomputation. Neither proves exact message-body integrity or remote delivery.
9. **Backup baseline.** Use the stricter age-v1-compatible X25519 format and a separate signing purpose. Never include Telegram session material or private keys in a policy export.

These are planning decisions, not claims that the concurrency or cryptography already works. Each phase's detailed plan must make the relevant types, lock order and failure behavior executable and reviewable.

## Dependency-ordered phases

### Phase 1 — Synthetic protocol and contract foundation

Deliver reproducible packaging, pinned SDK/dependencies, the exact static catalogue, assembled input/output contracts, bounded validation/errors, conservative demo configuration, privacy-safe logging and a disconnected synthetic status response. Serve a synthetic-only loopback endpoint using the SDK's public low-level API. All nine sensitive handlers return bounded failures; positive data fixtures run offline.

Exit evidence: source/schema digests, duplicate-key and reference tests, positive/negative fixtures, raw modern HTTP discovery/list/status/domain-error/unknown-tool probes, Host/Origin/body-limit checks, no telemetry export and no Telegram imports/session access. Contributions to A/B/I/K/L are explicitly foundation evidence only.

### Phase 2 — Privileged runtime, identity, consent and authority

Build the dedicated daemon/tunnel identities, protected storage and session ownership, authenticated admin IPC, coding-client lease and tunnel identity verification, signed consent LaunchAgent and key pairing. Implement owner policy, projects, grants, refs, epochs and emergency lock against fake adapters. Add metadata migrations with C2/C4, direct-DB integrity checks and restrictive sidecars.

Exit evidence: actual macOS user-presence signatures, prompt tampering/replay/cancellation tests, failed direct-secret and noninteractive impersonation probes, deny precedence, shared-membership rules, client attribution and epoch invalidation. Contributes to E/H/M/N/P and privilege/key requirements in R. Native consent/platform work may run alongside Python authority work once their shared challenge format is frozen.

### Phase 3 — Disclosure, budgets, proofs and audit integrity

Implement the shared coordinator, JCS preimages, egress/provenance, prompt-bound exposure snapshots, atomic reservations, signed receipts and transactionally committed exposure/audit state. Add monotonic external anchor publication, checkpoints, historical public keys and verification. Build the bounded formal model and matching implementation stateful tests before enabling concurrent sensitive results.

Exit evidence: crash/cancellation/revoke/lock interleavings, concurrent-budget non-overspend, shared-peer accounting, no uncommitted data escape, anchor-failure refusal and independently verified receipts. Contributes to F/O/P/Q. This phase is a hard dependency of every live sensitive tool success.

### Phase 4 — Allowlisted Telegram adapter and vertical tool slices

Add daemon-only authentication on a dedicated test account or supported Test DC. Implement operator discovery, project catalogue, chat listing/peer resolution, history/unread pagination, then context and single/cross-project search. Each slice consumes the Phase 3 pipeline. Extend read-only import/RPC guards as each adapter method is reviewed.

Exit evidence: independent unread/read-marker observation, no mutation RPCs, scoped retrieval before disclosure, sender isolation, forum-topic context, deleted/renamed data, Unicode, bounded retries/work, pagination races and truthful coverage. Contributes to B/C/D/E/F/G/H/N and extends O–Q. Test infrastructure never opens a production session.

### Phase 5 — Operator controls, retention and recovery

Complete doctor, local logout versus remote revocation, credential/key rotation, session recovery, retention and audit checkpoint preservation, policy explain/simulate/diff, overlap/drift inspection and signed encrypted policy backup/import. Add install/uninstall and incident runbooks with explicit state ownership.

Exit evidence: restore/repair failure injection, expired artifacts and historical key checks, import validation, recovery epoch invalidation, file protections and documented manual recovery. Contributes to F/H/O/P/Q/R. Early-phase doctor checks expand here; they are not postponed wholesale until this phase.

### Phase 6 — Installed clients and private tunnel acceptance

Integrate the exact installed Codex/Claude builds, configuration-isolation wrappers and short-lived credential helpers. Configure the actual tunnel client with separate service credentials, verified MCP-side mTLS and restricted associations. Run fresh tool scans and simultaneous client tests.

Exit evidence: two client families, all three when enabled, actual timeout/consent behavior, alias/shadowing refusal, raw-helper attempts, independent revocation, fairness and wire-contract parity. Contributes to B/I/J/K/L/M/N and rechecks O/P through complete clients. An unavailable tunnel feature blocks that production profile; no weaker fallback is introduced.

### Phase 7 — Release gauntlet and signed release evidence

Run all mandatory contract cases, adversarial corpus, stateful/formal checks, Telegram and manual client scenarios against one release candidate. Recheck advisories and exact installed versions. Build clean-checkout artifacts, SBOM, signed checksums, provenance and a populated security manifest with gate evidence paths/digests.

Exit evidence: final Gates A–R. A gate is PASS only with applicable evidence for this artifact and deployment; blocked and not-run are explicit states. Optional UI/workflow packaging is separate. No development milestone alone permits the “Production V0.1.10” label.

## Early external feasibility register

Record these during Phase 1 without provisioning accounts, invoking private Telegram tools or changing the host:

| Dependency | Planning evidence | Implementation evidence still required |
|---|---|---|
| MCP 2.2.0 / Telethon 1.45.0 | Release pages available; public SDK source inspected | Locked packages/hashes, advisory review and executable probes |
| MCP 2026-07-28 behavior | Public SDK source contains the modern request path | Exact wire, schema, error and compatibility behavior |
| macOS native consent | Required by the supplied design | Signing identity, hardware/key provider, ACLs and actual user-presence operation |
| Host privilege separation | Required by the supplied design | Installed service identities and denied elevation/secret access |
| OpenAI tunnel | Supplied spec describes required mode | Installed release's mTLS/config, account entitlement, associations and tool scan |
| Codex / Claude | Supplied spec defines required profiles | Exact builds, config precedence, helper behavior and >=75-second call timeouts |
| Telegram testing | Dedicated account/Test-DC profile required | Available isolated credentials, reproducible fixtures and independent read-state observations |

Public read-only checks used during planning: [MCP 2.2.0 release](https://pypi.org/project/mcp/2.2.0/), [Telethon 1.45.0 release](https://pypi.org/project/Telethon/1.45.0/), [SDK low-level server at v2.2.0](https://github.com/modelcontextprotocol/python-sdk/blob/v2.2.0/src/mcp/server/lowlevel/server.py), and [SDK transport manager at v2.2.0](https://github.com/modelcontextprotocol/python-sdk/blob/v2.2.0/src/mcp/server/streamable_http_manager.py). Release availability and source inspection are not runtime verification.

## Coverage and evidence ownership

| Spec material | Phase owner |
|---|---|
| 1–5 scope and assumptions; 48–49 frozen decisions | All phases; scope checked at each review |
| 6 architecture; 7 dependency baseline; 8 transport | 1 foundation, 2 identity, 3 disclosure, 6 real clients |
| 9 authentication/keys/consent | 2, 3, 4, 5 |
| 10–12 policy/refs/data model | 2; disclosure/audit tables and integration in 3 |
| 13–15 common contracts/catalogue; Appendix E | 1 contract freeze; 3–4 semantic implementation |
| 16 status | 1 synthetic; 2/4 live authority/account status |
| 17–23 tools/cursors; 23D coverage | 2 cursor foundations, 4 complete tool slices |
| 23A–23C proofs/egress/budgets; 23E lock | 2 authority/lock, 3 disclosure integration |
| 24–31 security/privacy/resources/invariants | Baseline 1–3; adversarial/Telegram proof 4/6/7 |
| 32–37 config/CLI/structure/adapter/guards | 1 foundations, 2 native/admin, 4 adapter, 5 full operations |
| 38–39 tests/contract matrix | Each owning phase; complete rerun in 7 |
| 40 A–R; 41 manual scenarios; 42 performance | 6 installed scenarios, 7 final decision |
| 43 recovery; 44 review; 45 future scope; 46 done | 5 recovery, 7 full review; future features excluded |
| 47 implementation order | Superseded in this proposed plan by C1 |
| 50 references; Appendices A–D/F/I/J | 1 instructions/ignore/banner, 6 client workflows, 7 consistency |
| Appendices K/L/M/N | 3 proofs/formal, 4 test infrastructure, 7 benchmark/release evidence |
| Appendices G/H/O/P | Historical/informative or optional; not independent release authority |

For each later phase, write and review its detailed task plan before executing it. Keep a gate ledger with status, exact command/environment, artifact digest, privacy-safe evidence path and unresolved dependency. Local synthetic, dedicated-account and installed-client evidence must remain distinguishable.

## Review and execution handoff

Review this roadmap and the detailed Phase 1 plan together. Recommended execution is subagent-driven, with fresh review of each testable task, because the schema and security boundaries become dependencies of every later phase. Native execution is also available. Implementation starts only after plan review and execution-method selection; no method has yet been selected.
