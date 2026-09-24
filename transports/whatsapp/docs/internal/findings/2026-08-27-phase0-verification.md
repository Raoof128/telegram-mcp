# WhatsVault Phase 0 — Provider/Platform Verification (FINDINGS SCAFFOLD)

> **This is a verification/finding scaffold, NOT an onboarding guide.** It records what must be *observed* before any live component is activated. **Do not press any Meta/Cloudflare/OpenAI activation control until the relevant prerequisite below is independently verified.** Its deliverable is this document, filled in with evidence — not code.

> **Spec/ledger:** design §0.1/§9/§6.6, INV-PROVIDER; Corrections Ledger #2, #3, #4, #18, #24, #44, #60; Deferred-Decision Register DD1/DD2/DD4.

## Exit rule (strict)

- **UNKNOWN is not YES.** A behaviour that protects a security boundary MUST NOT be activated from documentation inference alone when the design requires an *observed* provider behaviour.
- A gate is PASSED only when every blocking item in it is `YES` with recomputable evidence. A `FALLBACK REQUIRED` item does not block its gate if the fallback is the design's stated conservative path (e.g. V8 → manual reconciliation).
- Every figure taken from a secondary source is **reported**, not confirmed, until the primary provider doc is pinned with a verbatim quote and a date.

## Phase-0 assumptions registry (everything here is `PROVISIONAL` until its V-item is `YES`)

Downstream plans MUST treat each of these as `PROVISIONAL` — never silently assume true:

- `PROVISIONAL` — Meta webhook payload shapes for inbound/echo/history/status (V4/V5/V6/V7); current fixtures are marked `PROVISIONAL` in Phase 3.
- `PROVISIONAL` — inbound timestamp unit is **seconds** (V4); the seconds→ms conversion depends on it.
- `PROVISIONAL` — `biz_opaque_callback_data` is accepted on send and echoed into the status webhook (V8); until confirmed, status reconciliation is **manual-only** (conservative INDETERMINATE + POSSIBLE_MATCH, #59).
- **CANDIDATE v21.0 (2026-08-28)** — `META_GRAPH_VERSION`: read-only Graph calls succeed on v21.0 (V4/V12, #44/DD1). Confirm it also serves the send + webhook path before pinning; no live send yet.
- **DOC-CONFIRMED (WebFetch 2026-08-28)** — Cloudflare 128 KB cap / 100 retries / 24h free / paid ≤14-day / 4-day no-consumer DLQ (V14.1–V14.4). Still `PROVISIONAL`: THIS account's paid-retention configuration, R2 credentials + spill test (V14.5), and the realtime metric field name (V14.6).
- `PROVISIONAL` — OpenAI/ChatGPT supported private-MCP connection route, its auth, and tool support (O1–O3, DD4).
- **reported** — Meta Cloud-API content retention ~30 days (V13); Cloudflare paid retention ~14 days (V14.4) — pin both.

---

## GATE 1 — Coexistence (V1–V3)
*Blocks the entire write path. A NO here pauses Phases 3/4/5 activation; the read/vault half proceeds regardless.*

> **NOTE (2026-08-28):** the number provided for testing (+1 555-200-6424, `platform_type=CLOUD_API`, `verified_name="Test Number"`) is a standard Cloud API **test number, NOT a Coexistence number** — Gate 1 must be run against the personal WhatsApp Business App number and cannot be verified here. The test number is used only for the Gate 2 behavioural contract.

### V1 — Is the existing WhatsApp Business App number eligible for Coexistence onboarding?
**Status:** `[ ] YES  [ ] NO  [ ] UNKNOWN  [ ] BLOCKED`
**Why it matters:** eligibility gates activation of the entire write path (Phases 3/4/5). Without it there is no Coexistence + direct Cloud API.
**Required evidence:** provider eligibility screen or API response; account age; messaging-quality state; any provider-stated prerequisite (e.g. the *reported* 7-day active-use requirement); exact date checked. (No Graph version applicable yet.)
**Observed result:** _(unfilled)_
**Raw evidence reference:** _(unfilled)_
**Security consequence:** proceeding on an ineligible number risks resorting to the ordinary Cloud API `/register`, which can change the number's registration and jeopardise the Business App.
**Decision:** PROCEED / BLOCK / FALLBACK REQUIRED _(unfilled)_
**Reverification trigger:** Meta changes Coexistence eligibility criteria, or this number's quality rating changes.

### V2 — What is the exact, current, self-managed onboarding path (Business App → Coexistence)?
**Status:** `[ ] YES  [ ] NO  [ ] UNKNOWN  [ ] BLOCKED`
**Why it matters:** activation of the write path; a wrong step can migrate the number one-way.
**Required evidence:** primary Meta doc with the exact end-to-end steps, quoted verbatim; a screenshot of the flow captured **before** performing any step; date. If the official path is not documented clearly enough to execute safely → STOP and record that as the finding.
**Observed result:** _(unfilled)_
**Raw evidence reference:** _(unfilled)_
**Security consequence:** an unclear path invites improvising, risking ordinary `/register` on the personal number.
**Decision:** PROCEED / BLOCK / FALLBACK REQUIRED _(unfilled)_
**Reverification trigger:** Meta changes the onboarding flow.

### V3 — Does Coexistence keep the WhatsApp Business App usable simultaneously with API access (not a one-way migration)?
**Status:** `[ ] YES  [ ] NO  [ ] UNKNOWN  [ ] BLOCKED`
**Why it matters:** the design assumes the phone app remains usable alongside the API; a one-way migration breaks the user's normal usage.
**Required evidence:** Meta doc quote + a post-onboarding test on a throwaway/secondary number if available; date.
**Observed result:** _(unfilled)_
**Raw evidence reference:** _(unfilled)_
**Security consequence:** an irreversible migration of the personal number is a non-recoverable operational loss.
**Decision:** PROCEED / BLOCK / FALLBACK REQUIRED _(unfilled)_
**Reverification trigger:** Meta changes Coexistence behaviour.

---

## GATE 2 — Meta behavioural contract (V4–V13)
*Blocks activation of the ingest and send paths (Phase 3/4/5 live). Each item pins a behaviour a local invariant depends on.*

### V4 — Is a live inbound 1:1 message delivered to the webhook, and what is the exact payload shape (including the timestamp UNIT)?
**Status:** `[ ] YES  [ ] NO  [ ] UNKNOWN  [ ] BLOCKED`
**Why it matters:** activates `MESSAGE_INBOUND` ingest; the Mac-side fan-out normaliser (#2) and `window_eligible` depend on the real nested shape; the seconds→ms conversion (§3.3) depends on the unit.
**Required evidence:** captured (redacted) payload; confirm the nested `entry[].changes[].value.messages[]`/`statuses[]` structure (multi-event fan-out, #2); confirm the message `timestamp` is in **seconds**; the Graph API version used; date.
**Observed result:** PARTIAL (2026-08-28): Graph **v21.0** confirmed working for read calls → candidate `META_GRAPH_VERSION` pin (#44). The inbound webhook SHAPE + timestamp unit are STILL UNVERIFIED — capturing them requires a subscribed HTTPS webhook receiver (not yet stood up); the test number confirms `platform_type=CLOUD_API`.
**Raw evidence reference:** live Graph API read calls (v21.0, 2026-08-28); webhook capture pending a receiver.
**Security consequence:** a wrong shape/unit yields mis-parsed evidence and a wrong 24h-window computation (INV-SENDPOLICY).
**Decision:** PROCEED / BLOCK / FALLBACK REQUIRED _(unfilled)_
**Reverification trigger:** Meta bumps the webhook schema or the Graph API version.

### V5 — Are Business-App-typed messages mirrored to the webhook as echoes (`smb_message_echoes`)?
**Status:** `[ ] YES  [ ] NO  [ ] UNKNOWN  [ ] BLOCKED`
**Why it matters:** the "read your own sent messages" feature; the `MESSAGE_ECHO` family (which is `window_eligible=0`, never inbound).
**Required evidence:** captured echo payload shape and its distinguishing marker, or explicit confirmation that none are delivered; Graph version; date.
**Observed result:** _(unfilled)_
**Raw evidence reference:** _(unfilled)_
**Security consequence:** if an echo is mislabelled as inbound it could set `window_eligible=1` and wrongly open the send window — the echo detector must be verified against the real marker (the current `_wv_echo` fixture flag is `PROVISIONAL`).
**Decision:** PROCEED / BLOCK / FALLBACK REQUIRED _(unfilled)_
**Reverification trigger:** Meta changes Coexistence echo behaviour.

### V6 — Is any historical-message sync delivered on onboarding, and what is its volume + shape (`HISTORY_EVENT`)?
**Status:** `[ ] YES  [ ] NO  [ ] UNKNOWN  [ ] BLOCKED`
**Why it matters:** sizes the backfill gap (the export importer covers it); the design must **not** depend on this.
**Required evidence:** captured `HISTORY_EVENT` shape + volume, or confirmation that none is delivered; date.
**Observed result:** _(unfilled)_
**Raw evidence reference:** _(unfilled)_
**Security consequence:** history events must never set `window_eligible=1` (they are not live inbound).
**Decision:** PROCEED / BLOCK / FALLBACK REQUIRED _(unfilled)_
**Reverification trigger:** Meta changes history-sync behaviour.

### V7 — Are `sent`/`delivered`/`read`/`failed`/`deleted` status notifications delivered, in what ordering, and what is the payload shape?
**Status:** `[ ] YES  [ ] NO  [ ] UNKNOWN  [ ] BLOCKED`
**Why it matters:** status reconciliation (#59) and the monotonic delivery-rank lattice; the callback column (#60).
**Required evidence:** captured status events for a test send; observed ordering behaviour; whether `biz_opaque_callback_data` appears here; Graph version; date.
**Observed result:** _(unfilled)_
**Raw evidence reference:** _(unfilled)_
**Security consequence:** mis-ordered/mis-parsed status could distort delivery state, but the monotonic lattice already tolerates reordering; confidentiality is unaffected.
**Decision:** PROCEED / BLOCK / FALLBACK REQUIRED _(unfilled)_
**Reverification trigger:** Meta changes the status schema.

### V8 — Does the direct Cloud API accept `biz_opaque_callback_data` on a send AND echo it back into the status webhook?
**Status:** `[ ] YES  [ ] NO  [ ] UNKNOWN  [ ] BLOCKED`
**Why it matters (REVISED):** this gates the **deterministic status-reconciliation UX only** (#60). **V8 does NOT block Phase 4 approval correctness.** If callback correlation fails, WhatsVault still operates safely with conservative `INDETERMINATE` + manual reconciliation (`POSSIBLE_MATCH`, #59). V8 blocks the *deterministic reconciliation UX*, not *approval security*.
**Required evidence:** the send response + the correlated status webhook showing the field; Graph version; date.
**Observed result:** _(unfilled)_
**Raw evidence reference:** _(unfilled)_
**Security consequence:** none to approval security. A NO degrades only reconciliation determinism; the fallback is manual-only reconciliation (residual R7).
**Decision:** PROCEED (deterministic reconciliation) / **FALLBACK REQUIRED (manual-only reconciliation)** — **never BLOCK the whole write path on V8.**
**Reverification trigger:** Meta changes callback-data support on the direct API.

### V9 — Does `GET /{media-id}` return a fresh temporary URL after the first URL expires?
**Status:** `[ ] YES  [ ] NO  [ ] UNKNOWN  [ ] BLOCKED`
**Why it matters:** a sleeping Mac must not permanently lose media within the retention window; `materialise_media` (#43 daemon).
**Required evidence:** two `GET`s of the same media id minutes apart showing a refreshed URL; Graph version; date.
**Observed result:** _(unfilled)_
**Raw evidence reference:** _(unfilled)_
**Security consequence:** media availability (not confidentiality) — a NO tightens the media-materialisation deadline.
**Decision:** PROCEED / BLOCK / FALLBACK REQUIRED _(unfilled)_
**Reverification trigger:** Meta changes the media URL lifetime.

### V10 — Does free-form send fail outside the 24h window and require an APPROVED template, and does the templates endpoint list status?
**Status:** `[ ] YES  [ ] NO  [ ] UNKNOWN  [ ] BLOCKED`
**Why it matters:** P2 policy (#11), template send (#17); the window is send-authoritative (INV-SENDPOLICY).
**Required evidence:** a rejected free-form send outside the window with the exact error; a `message_templates` list response showing status; Graph version; date.
**Observed result:** OBSERVED (2026-08-28, live Graph v21.0, `GET /{WABA}/message_templates`): the endpoint returns per-template `status`; APPROVED templates present (`hello_world` UTILITY/en_US, plus `jaspers_market_*`). Template-listing half = **YES**. PENDING (needs a send): does a free-form send fail outside the 24h window and require an APPROVED template (the enforcement half)?
**Raw evidence reference:** live Graph API `GET /{WABA}/message_templates` (v21.0, 2026-08-28).
**Security consequence:** if Meta does not enforce the window, the local `control.db` projection remains the sole gate (it already is) — verify it is never bypassed.
**Classification:** compatibility/fallback (P1/P2 policy is deliberately local + send-authoritative).
**Decision:** FALLBACK REQUIRED — revise the Meta adapter/policy compatibility before affected free-form/template sends; may block free-form/template **production use** until understood, but does NOT block the Meta contract or invalidate approval cryptography _(unfilled)_
**Reverification trigger:** Meta changes the window/template policy.

### V11 — Confirm Coexistence numbers cannot send to / are not delivered group messages via the API.
**Status:** `[ ] YES  [ ] NO  [ ] UNKNOWN  [ ] BLOCKED`
**Why it matters:** P7 recipient policy (no group); the design excludes groups from the send path.
**Required evidence:** provider doc quote; date.
**Observed result:** _(unfilled)_
**Raw evidence reference:** _(unfilled)_
**Security consequence:** none to activation. Groups are excluded and enforced **locally** regardless of Meta: `write_capable=false` + `conversation_type=group` → deny. Unexpected Meta group support is a useful compatibility finding, not a dependency.
**Classification:** compatibility (non-blocking).
**Decision:** PROCEED — record the finding; keep enforcing the local group-deny _(unfilled)_
**Reverification trigger:** Meta adds group support to Coexistence.

### V12 — Is the runtime messaging scope (`whatsapp_business_messaging`) separable from the management scope (`whatsapp_business_management`)?
**Status:** `[ ] YES  [ ] NO  [ ] UNKNOWN  [ ] BLOCKED`
**Why it matters:** the `whatsvault-meta` daemon must hold ONLY the messaging scope (#43, topology invariant: one process holds the token, no management authority).
**Required evidence:** the token/scope configuration screen showing the split; the exact `META_GRAPH_VERSION` in use (pin it here for DD1/#44); date.
**Observed result:** OBSERVED (2026-08-28, live Graph v21.0, `GET /me/permissions`): `whatsapp_business_messaging` and `whatsapp_business_management` are **distinct, independently-granted permissions** → separable in principle (separability = **YES**). CAVEAT: the token tested holds BOTH (a dev/over-granted token). Full confirmation requires minting a **System User token scoped to `whatsapp_business_messaging` only** for `whatsvault-meta` and proving it can send without the management scope.
**Raw evidence reference:** live Graph API `GET /me/permissions` (v21.0, 2026-08-28); findings only — token not stored.
**Security consequence:** if inseparable, the daemon would hold management authority — a least-privilege violation.
**Decision:** PROCEED / BLOCK / FALLBACK REQUIRED _(unfilled)_
**Reverification trigger:** Meta changes the scope/permission model or deprecates the Graph version.

### V13 — Pin Meta's current Cloud-API message-content retention.
**Status:** `[ ] YES  [ ] NO  [ ] UNKNOWN  [ ] BLOCKED`
**Why it matters:** sizes how promptly media must be materialised; feeds ingest retention alerting (#39, `retention.assess`).
**Required evidence:** primary Meta doc quote with the exact figure (reported ~30 days); date.
**Observed result:** _(unfilled)_
**Raw evidence reference:** _(unfilled)_
**Security consequence:** operational (media availability), not confidentiality — a shorter figure tightens the media-materialisation deadline, not send safety.
**Classification:** operational (non-blocking for Meta activation).
**Decision:** PROCEED — feed the figure into `retention.assess`/materialisation policy; does not gate signed sends _(unfilled)_
**Reverification trigger:** Meta changes content retention.

---

## GATE 3 — Cloudflare (V14.1–V14.6)
*Blocks activation of the sealed edge relay + queue (Phase 3b live). Expanded to cover the corrections from the gauntlet.*

### V14.1 — Confirm the current Cloudflare Queues per-message size cap (target: 128 KB).
**Status:** `[ ] YES  [ ] NO  [ ] UNKNOWN  [ ] BLOCKED`
**Why it matters:** #3 — the Worker admits bodies ≤1 MB but a queue message caps at ~128 KB, so an oversized sealed body must spill to R2 (V14.5). A wrong assumption silently drops events.
**Required evidence:** current Cloudflare Queues limits doc quote; date.
**Observed result:** PRIMARY-DOC VERIFIED (WebFetch 2026-08-28) — doc-level fact = **YES**: max message size «128 KB»; max retries «100»; free retention «24 hours» (non-configurable); paid retention «Configurable up to 14 days». Account-independent, so the R2 oversized-ciphertext spill (#3) is mandatory.
**Raw evidence reference:** https://developers.cloudflare.com/queues/platform/limits/ (WebFetch-verified 2026-08-28).
**Security consequence:** silent event loss if a sealed body exceeds the cap without the R2 path.
**Decision:** PROCEED (R2 spill implemented) / BLOCK (no spill) / FALLBACK REQUIRED _(unfilled)_
**Reverification trigger:** Cloudflare changes queue message limits.

### V14.2 — Confirm how HTTP pull-consumer retry/DLQ configuration attaches (consumer, not producer).
**Status:** `[ ] YES  [ ] NO  [ ] UNKNOWN  [ ] BLOCKED`
**Why it matters:** #4 — retry/DLQ config belongs to the consumer configuration.
**Required evidence:** current pull-consumer configuration doc; date.
**Observed result:** PRIMARY-DOC VERIFIED (WebFetch 2026-08-28): «Enabling HTTP pull from a Wrangler configuration file is no longer supported» — enable «via the wrangler CLI or via the Cloudflare dashboard». (The DLQ-belongs-to-consumer fact lives on the DLQ page → recorded under V14.3, not here.) ACCOUNT-SPECIFIC PENDING: pull enabled on THIS queue.
**Raw evidence reference:** https://developers.cloudflare.com/queues/configuration/pull-consumers/ (WebFetch-verified 2026-08-28).
**Security consequence:** mis-placed config could disable retries/DLQ, losing transient-failure events.
**Decision:** PROCEED / BLOCK / FALLBACK REQUIRED _(unfilled)_
**Reverification trigger:** Cloudflare changes consumer configuration.

### V14.3 — Confirm an edge DLQ with NO active consumer retains only ~4 days.
**Status:** `[ ] YES  [ ] NO  [ ] UNKNOWN  [ ] BLOCKED`
**Why it matters:** #4 — a DLQ is itself a queue; naming one is insufficient. An explicit edge-DLQ **consumption/recovery design is required, with the DLQ's own configured retention independently verified** — do not assume a drainer/consumer implies infinite (or 14-day) retention.
**Required evidence:** DLQ retention doc quote; date.
**Observed result:** PRIMARY-DOC VERIFIED (WebFetch 2026-08-28): «A Dead Letter Queue (DLQ) is defined within your consumer configuration»; «Messages delivered to a DLQ without an active consumer will persist for four (4) days before being deleted from the queue». DESIGN/ACCOUNT PENDING: the edge-DLQ consumption/recovery design + its own configured retention (a drainer alone does not extend retention).
**Raw evidence reference:** https://developers.cloudflare.com/queues/configuration/dead-letter-queues/ (WebFetch-verified 2026-08-28).
**Security consequence:** silent loss of quarantined events past ~4 days without a drainer.
**Decision:** PROCEED (drainer built) / BLOCK / FALLBACK REQUIRED _(unfilled)_
**Reverification trigger:** Cloudflare changes DLQ retention.

### V14.4 — Confirm the paid-tier main-queue retention (target 14 days; Free tier 24h).
**Status:** `[ ] YES  [ ] NO  [ ] UNKNOWN  [ ] BLOCKED`
**Why it matters:** the INV-ACK durability window; `retention.assess` bands (#39).
**Required evidence:** Queues retention doc quote + the account's plan tier; date.
**Observed result:** DOC-CONFIRMED (2026-08-28): Free tier retention = 24h; **paid retention configurable up to 14 days**. ACCOUNT-SPECIFIC UNCONFIRMED: whether THIS account's queue is actually configured for 14 days.
**Raw evidence reference:** https://developers.cloudflare.com/queues/platform/limits/ (verified 2026-08-28); account queue config pending.
**Security consequence:** a shorter-than-assumed window shortens the offline-Mac tolerance before loss.
**Decision:** PROCEED / BLOCK / FALLBACK REQUIRED _(unfilled)_
**Reverification trigger:** Cloudflare changes retention or the account plan changes.

### V14.5 — Confirm the R2 oversized-ciphertext spill path (put/get, scoped credentials, lifecycle).
**Status:** `[ ] YES  [ ] NO  [ ] UNKNOWN  [ ] BLOCKED`
**Why it matters:** #3 — an oversized sealed body is stored as an encrypted R2 object; a pointer is enqueued; the Mac fetches, verifies `ciphertext_sha256`, commits, ACKs, deletes.
**Required evidence:** R2 API + lifecycle doc; a test put/get of a ciphertext object; the scoped-credential configuration; date.
**Observed result:** _(unfilled)_
**Raw evidence reference:** _(unfilled)_
**Security consequence:** R2 must hold **ciphertext only** (INV-CIPHERTEXT); verify the Worker seals *before* the put — a misconfiguration that stored plaintext would be a confidentiality breach.
**Decision:** PROCEED / BLOCK / FALLBACK REQUIRED _(unfilled)_
**Reverification trigger:** Cloudflare changes R2 API/lifecycle, or the sealing order regresses.

### V14.6 — Confirm the realtime metrics field for oldest-message age + backlog (best-effort).
**Status:** `[ ] YES  [ ] NO  [ ] UNKNOWN  [ ] BLOCKED`
**Why it matters:** retention monitoring (#50). **These metrics are best-effort/approximate — NOT proof that an already-expired event never existed.**
**Required evidence:** Queues metrics doc naming the field (e.g. `oldest_message_timestamp_ms`); date.
**Observed result:** _(unfilled)_
**Raw evidence reference:** _(unfilled)_
**Security consequence:** over-relying on approximate metrics would produce a false "no loss" claim; Phase 6 C5 must claim only impending-loss warning + honest post-expiry incompleteness (#50).
**Decision:** PROCEED / BLOCK / FALLBACK REQUIRED _(unfilled)_
**Reverification trigger:** Cloudflare changes the metrics surface.

---

## GATE 4 — 2b OpenAI/ChatGPT connectivity (O1–O4)
*Kept adjacent but SEPARATE from Meta activation. Blocks the live ChatGPT integration only; the local MCP (Phase 2a) is already shipped and testable without it. Resolves DD4.*

### O1 — What is the actual supported route to connect a private/local MCP to ChatGPT?
**Status:** `[ ] YES  [ ] NO  [ ] UNKNOWN  [ ] BLOCKED`
**Why it matters:** 2b activation (#18). ChatGPT does not simply connect to arbitrary local MCP servers; a supported route (Developer Mode / Secure MCP Tunnel / remote MCP) is required, and product-plan support varies.
**Required evidence:** OpenAI help/doc quote naming the supported route; **the intended account's actual plan tested against the currently-supported MCP modes** (not merely proof that the route exists); date. If the account's plan is not currently listed as supporting the required MCP mode, O1 is `BLOCKED` even though the technical route exists.
**Observed result:** DOC-VERIFIED IN-SESSION (2026-08-28, article "Developer mode and MCP apps in ChatGPT", updated "6 days ago"). Verbatim:
> "Not directly. ChatGPT connects to remote MCP servers. If your MCP server runs on a private network, on-premises, or on a developer machine, use **Secure MCP Tunnel** to connect it to supported OpenAI products without exposing the server to the public internet."

> "Pro users can build apps using the Apps SDK. **Full MCP is only available to Business and Enterprise/Edu users, currently.** Pro users can connect MCPs with **read/fetch permissions** in developer mode."

> "Apps, full MCP support, and developer mode are available for ChatGPT Business and Enterprise/Edu customers **on ChatGPT web**."

Route CONFIRMED (Secure MCP Tunnel). **PLAN TIER RECORDED (2026-08-28): Raouf is on ChatGPT Plus.** Plus IS eligible for developer mode — primary source `developers.openai.com/api/docs/guides/developer-mode`, verbatim:
> "Available to **Pro, Plus**, Business, Enterprise, and Education accounts on the web."

**DOC CONFLICT — UNRESOLVED, do not pick a side without a test.** Two OpenAI primary sources disagree on whether Plus can invoke *write* tools:
- ChatGPT Help (updated ~2026-08-22): "Full MCP is only available to Business and Enterprise/Edu users, currently. Pro users can connect MCPs with read/fetch permissions in developer mode." (Plus is not named at all.)
- Platform developer-mode guide: Plus is named as eligible, and "No per-plan differences are documented" for tool access.

CONSEQUENCE: the **Phase-2a six-tool read surface is connectable on Plus** (both docs agree). Whether the **Phase-4 prepare/draft tools** would be invokable on Plus is GENUINELY UNKNOWN and must be settled by an actual test, not by reading. Do not scope the prepare-tool MCP registration for the ChatGPT path until that test runs.
**Raw evidence reference:** https://help.openai.com/en/articles/12584461-developer-mode-and-mcp-apps-in-chatgpt — read in-session 2026-08-28 via a real browser. NOTE: the earlier HTTP 403 was **bot-gating, not login-gating** — the page is public (it renders with a "Login" link present); the prior "login/bot-gated" characterisation is corrected. Secure MCP Tunnel's own guide (auth model, O2) is a SEPARATE unread doc: https://developers.openai.com/api/docs/guides/secure-mcp-tunnels
**Security consequence:** an unsupported or insecure route could expose the loopback MCP surface (private message history) beyond the intended boundary.
**Decision:** PROCEED (supported route) / FALLBACK REQUIRED (OpenAI Responses API remote-MCP) / BLOCK _(unfilled)_
**Reverification trigger:** OpenAI changes MCP connectivity or product-plan support.

### O2 — How is the connection authenticated, and does it satisfy the loopback-auth requirement (#19)?
**Status:** `[ ] YES  [ ] NO  [ ] UNKNOWN  [ ] BLOCKED`
**Why it matters (requirement, mechanism-agnostic):** the selected route MUST provide an authenticated binding from the authorised ChatGPT connection to the local WhatsVault MCP, and MUST prevent unauthorised local or tunnel clients from invoking it. The existing Keychain-backed bearer token MAY be used where the selected route supports it; otherwise the route's authenticated identity MUST be mapped to the same local access decision (#19). Verify the *actual* mechanism the route provides — do not assume the local bearer token literally traverses it.
**Required evidence:** OpenAI doc on the chosen route's authentication model; a test proving an unauthorised local/tunnel client cannot invoke the MCP; the mapping used (bearer token, or route-identity → local access decision); date.
**Observed result:** PARTIAL (2026-08-28). The Developer-mode article documents the **OAuth** path only, and imposes a refresh-token requirement, verbatim:
> "For OpenID Connect providers, the standard way to request a refresh token is to include the `offline_access` scope in the authorization request... If OAuth is configured without `offline_access`, ChatGPT may lose access after the original authorization expires."

CONSEQUENCE FOR #19: WhatsVault's MCP currently authenticates with a **Keychain bearer token**, not OAuth. If the selected route requires OAuth, the bearer token does **not** traverse it and #19 must be satisfied by mapping route identity → local access decision (as O2 already anticipated).

**CORRECTION (2026-08-28, supersedes the OAuth-2.1 escalation recorded earlier the same day in commit 23cc6d1).** That escalation conflated two distinct OpenAI tracks and its stop-ship conclusion is WITHDRAWN:
- **Plugin-directory submission** (`developers.openai.com/plugins/build/auth`) — mandates "an OAuth 2.1 flow that conforms to the MCP authorization spec". This track applies only to publishing to the public directory, which is a recorded NON-GOAL for WhatsVault. It does not bind us.
- **Personal developer-mode connector** (`developers.openai.com/api/docs/guides/developer-mode`) — the track WhatsVault actually uses. Verbatim:
> "Authentication supported: OAuth, No Authentication, and Mixed Authentication. **For OAuth, if static credentials are provided, then they will be used.** ... Mixed authentication supports OAuth and No Authentication. This means the initialize and list tools APIs use no auth, and tools use OAuth or no auth based on the security schemes set on their tool metadata."

**NET O2 POSITION (revised again — read this one, it supersedes the optimistic reading above).**

UI EVIDENCE (2026-08-28, screenshots of the live "New Plugin" dialog on Raouf's **Plus** account): the Authentication control is a dropdown with **exactly three options — `OAuth` (default), `No Auth`, `Mixed`**. There is **no** bearer-token / API-key / custom-header option. `Advanced OAuth settings` is present but inert, labelled "Enter a valid MCP Server URL to review discovered OAuth settings" — i.e. **OAuth config is DISCOVERED from the server**, not hand-entered. `Connection` offers a `Server URL` | `Tunnel` toggle (tunnel path is available on Plus). Server URL placeholder is `https://example.com/sse`.

REVISED READING of "if static credentials are provided": in context its listed alternatives are **CIMD** and **DCR**, both of which are OAuth *client-registration* mechanisms. "Static credentials" therefore most likely means a pre-supplied OAuth `client_id`/`client_secret` that lets ChatGPT skip dynamic registration — **not** an arbitrary bearer token, and **not** a substitute for the server operating an authorization server. The claim recorded in commit a5795ce that the Phase-2a Keychain bearer token (#19) "is a plausible direct fit" is therefore DOWNGRADED to **UNLIKELY**.

**HONEST CURRENT STATE — three live options, none yet verified:**
- (a) Stand up an OAuth 2.1 AS (loopback-only if the browser-driven leg permits it — still untested, see below). Heaviest, but certain to satisfy #19.
- (b) `Mixed` — REJECTED on inspection regardless of feasibility: "the initialize and list tools APIs use no auth", leaking surface enumeration to anyone reaching the endpoint.
- (c) `No Auth` — REJECTED categorically; the surface returns private message history.

The deciding evidence is **the field list inside `Advanced OAuth settings`**, which cannot be inspected until a reachable MCP server URL is entered. **O2 is therefore blocked on Phase-2b transport existing at all** — not on further doc reading. Stop reading; build the transport and inspect the panel.

The earlier "publicly reachable AS → potential 2b STOP-SHIP" branch is NO LONGER RETRACTED but remains UNRESOLVED: whether a `127.0.0.1` authorization endpoint satisfies the discovery + browser-redirect flow is exactly the untested question, and if it does not, the stop-ship tension with the no-public-surface premise returns.

**REMAINING O2 WORK (unchanged in kind, much smaller in size):**
1. Verify empirically that the connector's static-credential field maps to an `Authorization` header the WhatsVault MCP can check with the existing `hmac.compare_digest` path. The docs do not state the header form — this is a test, not a doc read.
2. **Do NOT select Mixed Authentication.** Under Mixed, "the initialize and list tools APIs use no auth" — an unauthenticated party reaching the tunnel could enumerate the tool surface. Content stays protected, but surface enumeration is a free reconnaissance gift. Use the fully-authenticated OAuth-with-static-credentials mode so every call is checked.
3. The tunnel finding STANDS and is unaffected by this correction: the tunnel is an org/workspace reachability fence, not a caller-identity binding, so server-side auth remains mandatory (#19). Never run this surface with "No Authentication".
4. The unauthorised-client test O2 requires is still UNRUN.

**Tunnel mechanics (unchanged, still accurate):** `tunnel-client` authenticates to OpenAI's control plane with a Platform API key (`CONTROL_PLANE_API_KEY="sk-..."`); tunnels are created in Platform settings, not by the CLI; "A tunnel can be associated with one or more Platform organizations or ChatGPT workspaces." NOTE: this implies a **Platform (API) account** alongside the ChatGPT Plus subscription — unverified whether Plus alone provides the required Platform org access for tunnel creation. Flag as an open account-config question.
**Raw evidence reference (corrected):** https://developers.openai.com/api/docs/guides/developer-mode (DECIDING SOURCE for the connector path), https://help.openai.com/en/articles/12584461-developer-mode-and-mcp-apps-in-chatgpt , https://developers.openai.com/api/docs/guides/secure-mcp-tunnels , and https://developers.openai.com/plugins/build/auth (submission track only — NOT binding here). All in-session 2026-08-28.

**RESOLUTION (2026-08-29). O2 is CLOSED by option (a): WhatsVault now operates its
own OAuth 2.1 authorization server.** The record above said the deciding evidence
was blocked on the transport existing — so the transport was built, and the
question answered by observation rather than more doc reading.

**What was observed, not inferred:**
- The three-option dropdown recorded above is accurate, and item 1 of REMAINING
  O2 WORK is answered **NO**: there is no static-credential field that maps to an
  `Authorization` header. The existing Keychain bearer token does not traverse
  this route, exactly as the downgrade to UNLIKELY predicted. Items 2 and 3 stand
  unchanged; Mixed and No Auth remain rejected.
- The "loopback authorization endpoint" branch is **moot**, not resolved: the
  chosen deployment publishes the AS on a named Cloudflare tunnel, so the
  browser-redirect leg reaches a public https origin. (The operator's actual
  hostname is deliberately not recorded here: this repository is public, and
  naming the endpoint that serves a private message archive is a free gift to a
  scanner. It lives in the local, gitignored operator notes.) Whether a `127.0.0.1` authorization endpoint would have satisfied
  discovery remains untested and is no longer on the critical path.
- The no-public-surface premise is **narrowed, not abandoned**, and this is the
  honest cost of closing O2. The bind is still `127.0.0.1`; reachability comes
  from an outbound tunnel with an edge ingress admitting only `/mcp`, `/oauth/*`
  and `/.well-known/*` (verified over the internet: `/admin` and `/` return 404
  from Cloudflare). But message content now reaches a third party on every tool
  call, and the authorization server is the boundary rather than network
  position.
- **The unauthorised-client test O2 required is now RUN**, against the live public
  origin: anonymous `POST /mcp` → 401 carrying the RFC 9728 `resource_metadata`
  pointer; a forged bearer → 401; a full authorization-code + PKCE exchange →
  `initialize` 200. Polling before the operator approves never yields a code, and
  the whole surface was red-teamed separately (nineteen findings, all fixed —
  `tests/adversarial/test_oauth_grants.py`, `test_oauth_http.py`,
  `test_redteam_public.py`).
- Approval is deliberately **not** on the web page: the consent page has no form,
  and the grant is made with `whatsvault oauth-approve` from a terminal on the
  machine holding the vault. This preserves the project's shape — authority on a
  channel the requester cannot reach.

**Secure MCP Tunnel (the alternative) is confirmed real and NOT taken.**
`tunnel-client` 0.0.13 was installed and `doctor` reached `RESULT ok` against the
local server, so the route works. It was not used because it requires a
`CONTROL_PLANE_API_KEY` from an OpenAI **Platform** account — resolving the
account-config question flagged above: ChatGPT Plus alone does not supply it, and
no Platform credential exists on this machine. Recorded as a live fallback that
needs no public origin, should the public deployment be withdrawn.

**Security consequence:** an unauthenticated route would let any party reaching the tunnel read message history.
**Decision:** **PROCEED** — O2 closed 2026-08-29 via a self-hosted OAuth 2.1 AS with out-of-band approval. Read-only scope only; this decides nothing about the write path, which stays inert behind Gates 1-3.
**Reverification trigger:** OpenAI changes the auth model for the route; or the public origin is withdrawn, which returns the choice to Secure MCP Tunnel.

### O3 — Does the route support the read tool surface (and later the write/prepare tools)?
**Status:** `[ ] YES  [ ] NO  [ ] UNKNOWN  [ ] BLOCKED`
**Why it matters:** the Phase 2a MCP surface must be callable; tool-annotation support determines what ChatGPT can invoke.
**Required evidence:** OpenAI doc on tool support for the route; a test tool call against the loopback surface; date.
**Observed result:** DOC-CONFIRMED (2026-08-28), four findings, all NEW:
1. **No mandatory tool names.** > "Are search and fetch tools required for connected servers? **No. They are no longer required.**" — the Phase-2a surface naming is unconstrained.
2. **Frozen tool snapshot.** > "After an admin first approves an MCP app for the workspace, ChatGPT uses a 'frozen' snapshot of its available tools and inputs. Changes made later by the app's developer are not applied until an admin reviews and publishes an update." AND > "If the live app no longer matches the frozen snapshot, tool calls can error." **Consequence: adding the Phase-4 prepare tools later is not a hot change — it needs an admin refresh/republish.** On Business plans, > "apps cannot be updated after publishing at launch... you must recreate and republish."
3. **Web only.** > "Are MCP apps available on mobile? **No - web only.**" — WhatsVault-via-ChatGPT is desktop-web only; this does not affect the iOS approval app (separate path).
4. **Agent/deep-research limits.** > "Agent mode will not use custom apps. Deep research can use custom apps, but for read/fetch actions only - not for write actions."
A live tool call against the loopback surface is still UNRUN (needs 2b transport + tunnel).
5. **Private use needs no submission.** (2026-08-28, plugin connect guide) > "Use **Secure MCP Tunnel** to connect a private MCP server in developer mode **without exposing the server to the public internet**. A development tunnel or another HTTPS forwarding service can also provide an endpoint for local testing." The "reachable through a public HTTPS endpoint" requirement applies to **submission** to the public plugin directory only. **WhatsVault must never be submitted or published** — it is a personal message vault; the directory path is out of scope and should be recorded as a non-goal. Developer-mode availability "can depend on account and workspace policy" (still plan-gated, consistent with O1).
**Raw evidence reference:** https://help.openai.com/en/articles/12584461-developer-mode-and-mcp-apps-in-chatgpt (in-session 2026-08-28).
**Security consequence:** none direct; a gap here limits functionality, not safety. (But see O4 — finding 2's frozen-snapshot behaviour means a *silently stale* tool definition errors rather than misfires, which is the safe failure direction.)
**Decision:** PROCEED / BLOCK / FALLBACK REQUIRED _(unfilled)_
**Reverification trigger:** OpenAI changes tool support.

### O4 — Confirm and document the selected-plaintext disclosure boundary (#24).
**Status:** `[ ] YES  [ ] NO  [ ] UNKNOWN  [ ] BLOCKED`
**Why it matters:** INV-CONTENT / #24 — using ChatGPT with WhatsVault intentionally discloses the selected plaintext excerpts to the configured LLM service. The user must consent to this boundary before it goes live.
**Required evidence:** the disclosure invariant recorded in the spec + a written user acknowledgement; OpenAI data-use terms quote; date.
**Observed result:** PARTIAL (2026-08-28) — one NEW disclosure surface found that #24 does not currently name. Verbatim:
> "User conversations — including those using any app — are available in the **Compliance API** for Enterprise/Edu customers."

CONSEQUENCE: on Enterprise/Edu the disclosure boundary is **wider than "excerpts reach OpenAI"** — selected WhatsApp plaintext excerpts also become retrievable by the *workspace's own admins* via the Compliance API. For a personal-message vault this is a materially different consent question from the Pro/individual case. #24 should be amended to name it before 2b goes live. STILL UNFILLED: OpenAI data-use terms quote, and Raouf's written acknowledgement.
**Raw evidence reference:** https://help.openai.com/en/articles/12584461-developer-mode-and-mcp-apps-in-chatgpt (in-session 2026-08-28).
**Security consequence:** selected plaintext excerpts leave the Mac and reach OpenAI — intentional, but it must be explicit and acknowledged, not implicit. **Additionally (new): on Enterprise/Edu those excerpts are exposed to workspace admins through the Compliance API.**
**Decision:** PROCEED (user acknowledges the disclosure) / BLOCK (if not acceptable) / FALLBACK REQUIRED _(unfilled)_
**Reverification trigger:** OpenAI changes its data-use terms, or the disclosure scope widens.

---

## Gate decision summary (fill at closeout)

| Gate | Blocking items | Status | Decision |
|------|----------------|--------|----------|
| 1 — Coexistence | V1, V2, V3 | _(unfilled)_ | PROCEED / BLOCK |
| 2 — Meta contract | **Blocking:** V4, V7, V12. **Compatibility/fallback:** V5, V6, V8, V9, V10, V11, V13 — V10 may still block free-form/template *production use* until understood (not the whole contract); V8 → manual-only fallback | _(unfilled)_ | PROCEED / BLOCK |
| 3 — Cloudflare | V14.1, V14.3, V14.4, V14.5 (V14.2/V14.6 non-blocking) | _(unfilled)_ | PROCEED / BLOCK |
| 4 — OpenAI (2b) | O1, O2, O4 (O3 non-blocking) | **O2 CLOSED 2026-08-29 — self-hosted OAuth 2.1 AS, verified live; see the RESOLUTION block in O2.** O1 route CONFIRMED / **plan = Plus, eligible**; write-tool availability on Plus has CONFLICTING primary sources (needs test); **O2 UNRESOLVED — live UI confirms exactly 3 modes (OAuth/No Auth/Mixed), no bearer-token option; 'static credentials' re-read as OAuth CLIENT credentials, so bearer-token fit DOWNGRADED to unlikely; No Auth and Mixed both rejected on security grounds. Deciding evidence is the discovery-gated Advanced OAuth panel → BLOCKED ON 2b TRANSPORT EXISTING, not on more doc reading**; O3 DOC-CONFIRMED (live call unrun); O4 PARTIAL (new Compliance-API surface) | PROCEED / FALLBACK / BLOCK _(unfilled — gated on plan tier + tunnel auth model)_ |

**Activation is permitted only per-gate, only when that gate's blocking items are `YES` with evidence.** A NO on Gate 1 pauses the entire write path; the read/vault half (already shipped) is unaffected. Gate 4 is independent of Gates 1–3.
