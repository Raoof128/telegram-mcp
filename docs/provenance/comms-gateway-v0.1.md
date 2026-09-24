<!-- Provenance: the owner's source requirements for comms, pasted into the 2026-09-24 session and
recovered verbatim from that session's transcript for 5b-4. Superseded where docs/comms-spec-v0.2.md
or a phase design says so (e.g. 5b-4 D5: SCHEDULED is immutable). -->

# Persian Society Communications Gateway v0.1

## 0. Purpose

The Persian Society Communications Gateway is a single communications control plane for operating Society messaging across multiple platforms.

Initial transports:

* Telegram
* WhatsApp

Future transports may include:

* Email
* SMS
* Website announcements
* Push notifications
* Instagram or other supported channels

The system has one shared model for:

* audiences
* locations
* campaigns
* message variants
* scheduling
* delivery tracking
* audit history
* deduplication
* privacy
* operator control

Transport-specific credentials and platform rules remain isolated inside individual adapters.

---

# 1. Core Principle: The Owner Is the Authority

The system MUST distinguish between:

1. AI assistance
2. operator authority
3. transport execution

The AI is never an approval authority.

The operator is the approval authority.

A deliberate operator command to send a campaign is itself sufficient authorization.

There MUST NOT be an additional:

* Touch ID prompt
* Face ID prompt
* AI confirmation
* "Are you sure?" prompt
* second approval
* approval challenge
* consent token
* repeated permission dialogue

for ordinary owner-issued communications.

The system therefore follows:

```text
AI prepares
    ↓
Owner reviews
    ↓
Owner issues SEND
    ↓
Gateway executes
```

not:

```text
Owner says send
    ↓
AI asks permission
    ↓
OS asks permission
    ↓
system asks again
    ↓
second confirmation
    ↓
send
```

---

# 2. Trust Model

## 2.1 AI plane

The AI-facing MCP interface MAY:

* create drafts
* modify drafts
* translate messages
* create Persian and English variants
* attach media references
* select proposed audiences
* calculate recipient counts
* preview rendering
* inspect delivery status
* inspect campaign history
* inspect destinations
* search previous campaigns

The AI-facing MCP interface MUST NOT possess the primitive that actually transmits a campaign.

There is no:

```text
comms_send_campaign
```

tool available to an autonomous AI client.

This eliminates the need for per-send AI permissions.

## 2.2 Operator plane

The local operator plane MAY execute sending commands directly.

Examples:

```text
comms campaign send cmp_xxx
comms campaign send cmp_xxx --at 18:00
comms campaign retry-failed cmp_xxx
comms campaign cancel cmp_xxx
```

Access is established by the existing trusted local daemon/admin boundary.

The operating-system account, admin socket ownership and peer credentials establish operator authority.

Once an authorised operator reaches this interface, individual campaign operations require no additional presence ceremony.

## 2.3 Transport plane

Transport adapters hold only the credentials necessary for their transport.

Telegram credentials cannot authorize WhatsApp.

WhatsApp credentials cannot authorize Telegram.

Transport credentials do not grant access to the operator control plane.

---

# 3. Architecture

```text
                    Persian Society
                 Communications Gateway
                           │
          ┌────────────────┼────────────────┐
          │                │                │
      Campaigns        Audiences         Audit
          │                │                │
          ├────────────────┼────────────────┤
          │          Policy / Routing       │
          └────────────────┬────────────────┘
                           │
                    Delivery Engine
                           │
             ┌─────────────┴─────────────┐
             │                           │
      Telegram Adapter             WhatsApp Adapter
             │                           │
          Telegram                    WhatsApp
```

The common core MUST NOT contain Telegram-specific or WhatsApp-specific recipient logic.

Adapters translate common delivery jobs into transport operations.

---

# 4. Location Model

Locations are logical Society destinations.

Initial examples:

```text
loc_mq
loc_unsw
loc_usyd
loc_uts
loc_western_sydney
loc_sydney
loc_melbourne
loc_canberra
loc_all
```

A location MAY contain destinations on multiple transports.

Example:

```text
MQ
├── Telegram: MQ Persian Society group
├── Telegram: announcements channel
└── WhatsApp: MQ audience
```

`ALL LOCATIONS` is a logical union.

It MUST NOT be implemented as a special hard-coded broadcast loop.

Instead:

```text
all-locations
    ↓
resolve constituent locations
    ↓
resolve destinations
    ↓
resolve recipients
    ↓
deduplicate
    ↓
freeze delivery set
```

---

# 5. Audience Model

Locations and audiences are separate concepts.

Examples:

```text
aud_all_members
aud_students
aud_committee
aud_volunteers
aud_weekly_meetup
aud_counselling
aud_mq
aud_all_sydney
aud_all_locations
```

Audiences MAY include:

* locations
* individual recipients
* Telegram destinations
* WhatsApp recipients
* other audiences

Circular audience definitions MUST be rejected.

---

# 6. Recipient Identity

The common system MUST NOT expose raw phone numbers or raw platform identifiers to AI clients.

Examples:

```text
rcp_...
dst_...
aud_...
loc_...
cmp_...
msg_...
```

Internal mappings MAY contain the platform identity necessary to deliver the message.

Logs and normal operator output MUST use opaque refs.

The operator MAY explicitly request protected identity information through a dedicated administrative inspection command if needed for troubleshooting.

---

# 7. Campaign Object

A campaign is immutable once transmission begins.

Example logical structure:

```text
campaign_ref
title
created_at
created_by
state

message:
    canonical_body
    fa_variant
    en_variant
    media[]
    links[]

targets:
    audiences[]
    locations[]
    transports[]

options:
    deduplicate
    schedule
    reply_mode

delivery_snapshot:
    destination_digest
    recipient_digest
    recipient_count
```

Campaign states:

```text
DRAFT
READY
SCHEDULED
SENDING
PARTIAL
SENT
FAILED
CANCELLED
```

No campaign may move from `SENDING`, `PARTIAL` or `SENT` back to `DRAFT`.

---

# 8. Drafting

Example:

```text
comms campaign create weekly-meetup
```

returns:

```text
cmp_7...
```

Then:

```text
comms campaign set-message cmp_7... --fa ...
comms campaign set-message cmp_7... --en ...
comms campaign attach cmp_7... poster.jpg
comms campaign target cmp_7... --audience aud_all_sydney
comms campaign transports cmp_7... telegram whatsapp
```

The AI may perform these operations because none transmits a message.

---

# 9. Preview

Preview is strongly supported but is not an authorization ceremony.

```text
comms campaign preview cmp_7...
```

Example output:

```text
Campaign: Weekly meetup

Telegram
  MQ announcements         1 destination
  UNSW                     1 destination
  USYD                     1 destination
  UTS                      1 destination

WhatsApp
  eligible recipients      384

Total logical locations    4
Unique recipients          701

Persian variant            present
English variant            present
Poster                     present
Registration URL           present

Validation                  PASS
```

The operator can inspect this for as long as desired.

There is no "approve preview" operation.

---

# 10. Sending

The following command is itself authorization:

```text
comms campaign send cmp_7...
```

It MUST immediately:

1. load the campaign;
2. validate its state;
3. resolve audiences;
4. resolve destinations;
5. deduplicate recipients;
6. validate transport eligibility;
7. freeze the delivery snapshot;
8. compute campaign and audience digests;
9. move the campaign to `SENDING`;
10. create delivery jobs;
11. begin transport execution.

There is no additional permission prompt.

---

# 11. Critical Security Property

The AI does not receive direct transmission capability.

Therefore:

```text
AI hallucination
AI prompt injection
AI autonomous tool call
AI mistaken interpretation
```

cannot independently produce an outbound campaign.

The privileged transition is:

```text
READY → SENDING
```

and only the operator control plane owns it.

This is the primary safety boundary instead of repeated confirmation prompts.

---

# 12. Telegram Transport

The Telegram adapter MAY support:

* groups
* supergroups
* channels
* direct recipients where appropriate
* text
* media
* links
* replies
* delivery result capture where available

Each Telegram destination is stored as:

```text
destination_ref
transport = telegram
location_ref
platform_identity_encrypted
display_name_cache
enabled
capabilities
```

Platform-level rights remain authoritative.

The gateway MUST NOT pretend it can send somewhere that the Telegram account or integration itself cannot send.

---

# 13. WhatsApp Transport

The WhatsApp adapter operates as an independent delivery provider.

It MAY support:

* approved individual recipients
* message templates where required by the platform
* conversational messages where allowed
* media
* links
* delivery status events
* replies

WhatsApp platform eligibility rules MUST be checked by the adapter before queueing a recipient.

Those rules are external transport requirements.

They are NOT gateway permission prompts.

An ineligible recipient produces:

```text
SKIPPED_PLATFORM_POLICY
```

rather than causing the entire campaign to fail.

---

# 14. Cross-Transport Campaigns

One campaign can target:

```text
telegram
whatsapp
```

simultaneously.

Example:

```text
comms campaign send cmp_7...
```

may produce:

```text
Telegram
  6 destinations
  6 accepted

WhatsApp
  384 recipients
  372 accepted
  12 skipped

Campaign
  status: PARTIAL
```

The canonical campaign remains one object.

Transport-specific deliveries remain separate child records.

---

# 15. Deduplication

Deduplication occurs before transmission.

A person belonging to:

```text
MQ
Sydney
Students
Weekly Meetup
```

must not receive four identical WhatsApp messages.

The system computes:

```text
campaign_ref
+
transport
+
canonical recipient identity
```

into an idempotency key.

Exactly one active delivery job may exist for a key.

Telegram destinations are deduplicated separately from WhatsApp recipients.

Cross-platform deduplication MUST NOT suppress different platforms by default.

A recipient may therefore receive:

* one Telegram copy
* one WhatsApp copy

if the campaign intentionally targets both.

---

# 16. Retry Semantics

Transport results:

```text
PENDING
ACCEPTED
DELIVERED
FAILED_TRANSIENT
FAILED_PERMANENT
SKIPPED
```

Only transient failures may be automatically retried.

Retries MUST reuse the original idempotency identity.

A confirmed successful delivery MUST never be retransmitted by an automatic retry.

---

# 17. Partial Failure

Campaign transmission is not all-or-nothing across external networks.

Example:

```text
Telegram: 6 / 6 accepted
WhatsApp: 372 / 384 accepted
```

produces:

```text
PARTIAL
```

not `FAILED`.

The operator can execute:

```text
comms campaign retry-failed cmp_7...
```

Only retry-eligible failed deliveries are considered.

---

# 18. Cancellation

```text
comms campaign cancel cmp_7...
```

stops jobs that have not yet been handed to the transport.

It cannot truthfully claim to recall messages that have already been transmitted.

Results MUST distinguish:

```text
cancelled_before_send
already_sent
currently_in_flight
```

---

# 19. Scheduling

Campaigns may be scheduled:

```text
comms campaign send cmp_7... --at "2026-10-01T18:00:00+10:00"
```

This is authorization at scheduling time.

The daemon MUST NOT request authorization again at execution time.

The exact campaign snapshot being scheduled is frozen when the operator schedules it.

Changing a draft later does not modify the scheduled snapshot.

---

# 20. Audit

Every campaign records privacy-safe operational events:

```text
campaign.created
campaign.modified
campaign.scheduled
campaign.send_started
campaign.transport_completed
campaign.partial
campaign.completed
campaign.cancelled
campaign.retry_started
```

The event MAY contain:

```text
campaign_ref
audience_digest
recipient_count
destination_count
transport
success_count
failure_count
timestamp
```

It MUST NOT contain:

* message bodies
* phone numbers
* raw Telegram IDs
* API credentials
* WhatsApp tokens
* private recipient data

---

# 21. Message Content Storage

Campaign content MAY be retained because this is an outbound content-management system, unlike the privacy model for private Telegram retrieval.

However retention MUST be explicit.

Recommended:

```text
draft content             retained
sent campaign content     retained
private inbound messages  separate policy
transport credentials     never in campaign records
```

A later configuration option may support automatically deleting old campaign bodies while preserving delivery/audit metadata.

---

# 22. Operator Authentication

The daemon control socket MUST remain restricted by:

* filesystem ownership
* filesystem permissions
* peer credentials
* service identity

The system does NOT perform user-presence approval for each operation.

If an attacker fully controls the authenticated owner's operating-system account, the system does not claim to protect the owner from themselves.

That is the deliberate trust boundary of Owner-Authorized Direct Mode.

---

# 23. AI Boundary

AI tools:

```text
comms_list_locations
comms_list_audiences
comms_create_draft
comms_update_draft
comms_translate_draft
comms_attach_media
comms_target_draft
comms_preview_campaign
comms_campaign_status
comms_delivery_report
```

Operator-only capabilities:

```text
campaign send
campaign schedule
campaign retry-failed
campaign cancel
destination add
destination remove
credential rotate
transport configure
```

There is deliberately no AI-callable `send_campaign` primitive.

---

# 24. Optional Future Direct-AI Mode

If we ever decide the AI itself may send, that MUST be a separate release profile.

Example:

```text
owner_direct       default
ai_direct          optional
```

`ai_direct` must never silently inherit from `owner_direct`.

Version 0.1 does not need it.

---

# 25. Administration

Commands:

```text
comms location list
comms location create
comms location rename
comms location enable
comms location disable

comms audience list
comms audience create
comms audience members
comms audience add-location
comms audience remove-location

comms destination list
comms destination add
comms destination remove
comms destination enable
comms destination disable
comms destination test

comms campaign create
comms campaign list
comms campaign show
comms campaign preview
comms campaign send
comms campaign cancel
comms campaign retry-failed
comms campaign status

comms transport status
comms transport telegram status
comms transport whatsapp status

comms audit verify
comms doctor
```

---

# 26. "Send Everywhere"

The operator command:

```text
comms campaign target cmp_7... --audience all-locations
comms campaign send cmp_7...
```

must mean:

```text
all enabled locations
×
all campaign-selected transports
×
all eligible destinations
```

with deduplication.

It MUST NOT mean:

```text
every recipient ever observed
every Telegram chat
every WhatsApp contact
```

Only explicitly configured Society audiences and destinations participate.

---

# 27. Location Example

```text
ALL LOCATIONS
├── Sydney
│   ├── MQ
│   ├── UNSW
│   ├── USYD
│   ├── UTS
│   └── Western Sydney
├── Melbourne
└── Canberra
```

A command could therefore be:

```text
comms campaign target cmp_7... --location sydney
```

or:

```text
comms campaign target cmp_7... --all-locations
```

---

# 28. Inbound Replies

Inbound messages are a separate subsystem.

They MUST NOT automatically trigger outbound bulk communication.

Possible future workflow:

```text
incoming WhatsApp reply
        ↓
Inbox
        ↓
AI can summarise / draft response
        ↓
operator sends response
```

Automatic conversational agents may be introduced separately.

---

# 29. Failure Isolation

Telegram failure MUST NOT stop WhatsApp.

WhatsApp failure MUST NOT stop Telegram.

One malformed destination MUST NOT abort unrelated destinations.

One transport credential failure SHOULD stop that transport and leave the others operational.

---

# 30. Secrets

Secrets include:

* Telegram session/auth material
* WhatsApp access tokens
* webhook verification secrets
* private signing keys
* transport credentials

They MUST:

* stay outside campaign records
* stay outside logs
* stay outside MCP output
* never appear in audit events
* never appear in generated AI context

---

# 31. Tests

Minimum security/correctness tests:

```text
AI cannot obtain send primitive
operator send requires no secondary permission
one send command creates one immutable snapshot
all-locations resolves only configured locations
disabled location excluded
disabled destination excluded
duplicate WhatsApp recipient sent once
same recipient may receive once per selected transport
campaign content cannot mutate after SENDING
retry never duplicates successful delivery
cancel stops only unsent work
Telegram failure does not stop WhatsApp
WhatsApp failure does not stop Telegram
raw phone numbers absent from audit/log output
transport credentials absent from campaign data
scheduled campaign needs no second approval
scheduled content remains frozen
unknown audience fails closed
audience cycle rejected
recipient-set digest stable
delivery idempotency survives restart
restart resumes unfinished campaign safely
```

---

# 32. Crash Recovery

Before sending the first external message, the campaign snapshot and every delivery job MUST be durable.

Therefore:

```text
resolve
→ freeze
→ commit delivery jobs
→ begin network sends
```

never:

```text
send externally
→ later record what was sent
```

On restart:

```text
PENDING jobs        resume
FAILED_TRANSIENT    eligible for retry policy
ACCEPTED            never blindly resend
DELIVERED           never resend
FAILED_PERMANENT    remain failed
```

Ambiguous external outcomes must remain explicit rather than being silently resent.

---

# 33. Doctor

`comms doctor` checks:

* database integrity
* daemon ownership
* admin socket permissions
* Telegram adapter state
* WhatsApp adapter state
* credential presence without printing credentials
* configured locations
* invalid audiences
* orphan destinations
* scheduled campaign health
* delivery queue health
* webhook state where applicable
* duplicate recipient mappings
* audit integrity
* disabled or unavailable transports

It MUST NOT disclose raw phone numbers or secrets.

---

# 34. Product Rule

The gateway is an **operator-controlled communications system with AI assistance**.

It is not an autonomous mass-messaging agent.

The owner decides what goes out.

The owner-issued send command is authorization.

No redundant approval ceremony is required.

---

# 35. Recommended Repository Shape

```text
persian-comms/
├── core/
│   ├── audiences/
│   ├── campaigns/
│   ├── destinations/
│   ├── delivery/
│   ├── audit/
│   └── identity/
│
├── transports/
│   ├── telegram/
│   └── whatsapp/
│
├── mcp/
│   └── assistant_tools/
│
├── admin/
│   ├── socket/
│   └── commands/
│
├── storage/
├── runbooks/
├── tests/
└── docs/
```

---

# 36. Final Security Boundary

The system's central invariant is:

```text
AI can prepare.
AI can inspect.
AI can recommend.
AI cannot transmit.

The authenticated owner can transmit.
The owner's send command is the authorization.
```

This replaces repetitive permissions with a simpler and stronger separation of authority.

I particularly like this because it solves your annoyance **without weakening the boundary in a silly way**. Rather than giving the AI permanent blanket permission, we remove approval authority from the AI altogether.

The only permissions we cannot eliminate are **platform permissions**. For example, a Telegram account/bot still needs the platform rights necessary to send into the destination; Telegram's current API exposes those send/admin rights at the chat level. ([Telegram][1]) Those are Telegram rules, not our gateway asking you for permission.

For our own system, though: **one owner command, one send. No permission pinball machine.** 🔐→📣

[1]: https://core.telegram.org/bots/api?utm_source=chatgpt.com "Telegram Bot API"
