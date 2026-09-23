# Telegram MCP Phase 4c — Context, Both Searches, Coverage and Qualification

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans (the owner's standing rule: no subagents; execute inline). Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Serve `telegram_get_context`, `telegram_search_messages` and `telegram_cross_project_search` through the 4b daemon, ingress, consent and coordinator. The searches are per-peer only, carry signed §23D coverage, and use a continuation model that can never skip a peer, lose a hit or exceed a work bound.

**Architecture:**
- **Adapter.** The Telethon module gains three reviewed reads: by-id anchor, topic replies, and per-peer search.
- **Engine.** A pure engine, `telegram/search.py`, owns continuation and coverage.
- **Authority.** `disclosure/search_authority.py` owns the search authority snapshot, delegated to from `CoordinatorAuthority`.
- **Reads.** `telegram/reads.py` shapes records.
- **Coordinator.** It refuses to sign coverage that disagrees with the result.

**Tech Stack:** Python 3.12, `telethon==1.45.0`, `mcp==2.2.0`, SQLite; no new dependency.

**Spec:** [`docs/superpowers/specs/2026-09-23-telegram-mcp-phase-4-design.md`](../specs/2026-09-23-telegram-mcp-phase-4-design.md) §4, §5 and **§4.7 (revision 5)**, which records every refinement that executing this plan's code forced. Frozen product spec: `telegram-mcp-v0.1.10-final-engineering-spec.md` (SHA-256 `36b67f488415f2ab1c44b8d906de7f192fbe0dc562a2aeac76938b24c4a61b0a`), especially §13.1–§13.5, §14.1A, §20, §21, §21A, §23, §23B.2, §23C.1, §23D and §27.

## How this plan was made

This plan is a transcription of code that already ran. Every task's code was first written and executed in a scratch copy of `main` at `cd1434e`. The tasks were then replayed one at a time onto a second fresh copy, running each task's own tests, ruff, mypy, the full suite and the smoke at every stage (see "Staged proof" below). Each implementation step is an exact unified diff against the file as the previous task leaves it, or a complete new file. Apply a diff with `git apply --unidiff-zero` from a file, or by hand. If it does not apply cleanly, the tree has drifted from the task order: stop and record a ruling.

## Global Constraints

- **Read-only, still.** The only new wire requests are `messages.SearchRequest` (per peer; `messages.SearchGlobalRequest` stays forbidden) and `messages.GetRepliesRequest`. Both join `OPERATIONS["mcp.retrieval"]` in the adapter, `REVIEWED_RPCS` in the architecture test, and `ALLOWED` in the recorder.
- **Only `src/telegram_mcp/telegram/telethon_adapter.py` imports `telethon`.** The engine and the search authority are Telethon-free.
- **Query text is never persisted.** It stays out of SQLite, logs, audit rows and cursors. A cursor binds it only through the keyed query HMAC (§21.5, §23.2).
- **§13.2 bounds hold per call:**
  - 20 Telegram RPCs;
  - 500 examined search hits;
  - a 15-second deadline;
  - a search limit of 50;
  - `before`/`after` of at most 50, and at most 101 messages;
  - the 48 KiB page cap from 4b.
- **Coverage (§23D):**
  - `complete=true` implies no cursor, `meta.partial=false`, no reasons, and every eligible peer scanned;
  - every non-null cursor carries `response_limit`;
  - Telegram uncertainty is never complete.
- **Cross-project peer cap.** Cross-project search searches at most the first 250 canonical peers, and reports `peer_budget` on every page; ordinary search has no universe cap.
- **Egress (§23B.2).** Each record takes the most restrictive grant among its own origin projects. The estimate uses the widest selected grant.
- **Budget attribution (§23C.1).** A cross-project record counts whole in every contributing project's bucket.
- **Carried from 4b.** One copy of each shared rule; no test-only branches in production paths; fail closed; commit only on a green full gate. Every commit message ends with `Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>`.

## Scope boundaries

- **Topic titles stay `null`.** `topic_title` needs `messages.GetForumTopics`, which is not reviewed (design §3.8, unchanged).
- **The Test DC run and the dedicated-account qualification are owner-run.** Task 8 extends the harness, compile-checked and skipped by default. The six `partial_reasons` are proven by deterministic fault injection (Task 6), as design §4.6 intends.
- **No schema migration.** Cursor state gains four keys and one value rule, inside the existing validator.

## Review Focus

1. **A deleted or edited message between search pages.** Expected: the walk continues below it; nothing repeats, nothing is lost. Owned by Task 4 (`test_exhaustively_every_hit_arrives_exactly_once_and_it_ends`, over every small universe, and `test_a_page_held_under_the_cap_loses_and_repeats_nothing`).
2. **An anchor inside a forum topic when Telegram's `add_offset` edge behaves either way.** Expected: the window never leaves the topic and never shifts by one. Owned by Task 6 (`test_an_ordinary_window_is_exact_under_either_edge_semantics`, `test_a_named_topic_window_uses_replies_and_never_history`, `test_general_never_includes_a_named_topic_message`).
3. **A shared peer's membership removed mid-flight in cross-project search.** Expected: step 8 refuses and there is no receipt. Owned by Task 5 (`test_a_shared_peer_removed_mid_flight_is_discarded`) and Task 7 (`test_a_shared_peer_removed_between_retrieve_and_commit_is_discarded`, plus a smoke row).
4. **Long full-text hits filling the 64 KiB response.** Expected: the page ends early with a cursor, and the next page resumes at the first hit that did not fit. Owned by Task 4 and Task 6 (`test_a_long_page_is_held_under_the_cap_without_losing_hits`).
5. **A peer the entity cache cannot reach, or a Telegram response naming another chat.** Expected: never searched or attributed, and never claimed complete. Owned by Task 2 (`test_messages_from_another_chat_are_never_attributed`, `test_dialogs_nobody_asked_for_are_not_returned`) and Task 6 (`test_an_unreachable_member_is_never_claimed_complete`).
6. **A continuation after policy, grant, egress or membership changes.** Expected: the frozen §23.3 hierarchy, then the universe digest. Owned by Task 5 (`test_the_cursor_hierarchy_then_the_universe_digest`).
7. **A coverage object that disagrees with the result it describes.** Expected: `PROOF_GENERATION_FAILED`, nothing signed. Owned by Task 7 (`test_dishonest_coverage_is_never_signed`).

## Defects the execution-first method caught before this plan existed

Each was found by running the code, fixed, and is pinned by a test named in its task:

1. **Newest-not-nearest neighbours.** The `get_context` newer-side walk kept the newest `after` messages, not the nearest, under anchor-inclusive semantics.
2. **Hit budget overshoot.** A single request could examine more hits than the hit budget allowed.
3. **First hit uncounted.** `accept()` was not consulted for a page's first hit, so the page-cap accounting undercounted.
4. **Sidecar measured as data.** The `_coverage` sidecar was attached before the page-cap check measured `data`.
5. **Unrequested dialogs admitted.** `GetPeerDialogs` results could admit a peer that was never requested.
6. **Wrong-chat attribution.** A message from another chat could be attributed to the searched peer.
7. **Loss after offsets advanced.** Search hits dropped by the page cap after their offsets advanced would have been lost; the design is now byte-accounted.
8. **Import-time test state.** A module-level `Deadline`/`WorkBudget` in a test expired or drained across a full run.
9. **Stale "unserved" pins.** Three existing tests pinned `get_context` as unserved.
10. **Search pinned unbounded.** A 4b unit test pinned search as having no bound.
11. **Order-dependent fixture import.** Importing a pytest fixture across modules tripped `F811` on every test; the reads-world fixture body is now a plain helper.

---

## File map

| File | Task | Responsibility |
|---|---|---|
| `src/telegram_mcp/authority/cursors.py` | 1 | one value rule per state key; the search window keys |
| `src/telegram_mcp/telegram/telethon_adapter.py` | 2 | by-id, replies, per-peer search, forum flag, response attribution checks |
| `src/telegram_mcp/disclosure/bounds.py` | 3 | worst-case shapes for the three 4c tools |
| `src/telegram_mcp/telegram/search.py` | 4 | the continuation engine and coverage (pure) |
| `src/telegram_mcp/authority/policy.py` | 5 | `readable_members`, one copy |
| `src/telegram_mcp/storage/refstore.py` | 5 | `peer_by_row` |
| `src/telegram_mcp/disclosure/search_authority.py` | 5 | search snapshot, estimate, drift, per-record egress, cursor |
| `src/telegram_mcp/disclosure/seams.py` | 5 | the `get_context` path; delegation to the search authority |
| `src/telegram_mcp/telegram/reads.py` | 6 | `get_context`, `search_messages`, `cross_project_search` |
| `src/telegram_mcp/disclosure/coordinator.py` | 7 | refuse to sign dishonest coverage |
| `src/telegram_mcp/consent/display.py` | 7 | the cross-project context-insertion warning |
| `src/telegram_mcp/runtime/composition.py` | 7 | route the three tools |
| `scripts/e2e_smoke.py` | 5, 7 | pin moves (5); four Phase-4c rows (7) |
| `tests/telegram/{fixture_builder,test_testdc}.py` | 8 | forum, topics and search exhaustion on Test DC (owner-run) |
| `docs/verification/{phase-4,telegram-rpc-review}.md` | 8 | 4c evidence and the two new RPC reviews |

---


### Task 1: Cursor state: one value rule per key, and the search window

Design §3.4 and §4.3. The search continuation stores `upper_date`, `universe_digest`, `window_start` and `next_unstarted_index`, plus a `per_peer` map whose entries hold only `offset_id`. Absence encodes exhaustion, so there is no flag to spoof. The validator's key-by-key if-chain becomes a table, `STATE_VALUE_RULES`, so "exactly one value rule per allowed key" is testable, and `ALLOWED_STATE_KEYS` derives from it.

**Files:**
- Create: `tests/unit/test_cursor_state_rules.py`
- Modify: `src/telegram_mcp/authority/cursors.py`

**Interfaces:**
- Produces:
  - `STATE_VALUE_RULES: dict[str, str]` (rules: `int`, `date`, `peer_ref`, `seen_ids`, `per_peer`, `hmac`); `ALLOWED_STATE_KEYS = frozenset(STATE_VALUE_RULES)`
  - `universe_digest` values must match `hmac-sha256:<64 lowercase hex>`; `per_peer` entries may hold only `offset_id`

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_cursor_state_rules.py`:

```python
"""Design §3.4/§4.3: every cursor-state key has exactly one value rule."""

import pytest

from telegram_mcp.authority.cursors import ALLOWED_STATE_KEYS, STATE_VALUE_RULES, _check_state

REF = "tgp_" + "a" * 26
DIGEST = "hmac-sha256:" + "0" * 64


def test_each_allowed_key_has_exactly_one_rule():
    assert set(STATE_VALUE_RULES) == ALLOWED_STATE_KEYS
    assert set(STATE_VALUE_RULES.values()) <= {
        "int",
        "date",
        "peer_ref",
        "seen_ids",
        "per_peer",
        "hmac",
    }
    for key in ("upper_date", "universe_digest", "window_start", "next_unstarted_index"):
        assert key in ALLOWED_STATE_KEYS


def test_a_search_window_state_validates():
    state = {
        "upper_date": "2026-09-23T00:00:00Z",
        "universe_digest": DIGEST,
        "window_start": 3,
        "next_unstarted_index": 67,
        "per_peer": {REF: {"offset_id": 99}},
    }
    assert _check_state(state) == state


@pytest.mark.parametrize(
    "state",
    [
        {"universe_digest": "0" * 64},  # unlabelled
        {"universe_digest": "hmac-sha256:" + "Z" * 64},
        {"window_start": -1},
        {"window_start": True},
        {"upper_date": "2026-09-23"},
        {"per_peer": {REF: {"offset_id": 1, "exhausted": True}}},  # absence encodes exhaustion
        {"per_peer": {REF: {"page": 1}}},
        {"per_peer": {"tgm_" + "a" * 26: {"offset_id": 1}}},
        {"query": "secret words"},  # never content
    ],
)
def test_out_of_rule_state_is_refused(state):
    with pytest.raises(ValueError):
        _check_state(state)
```

- [ ] **Step 2: Run them and watch them fail**

Run: `uv run pytest tests/unit/test_cursor_state_rules.py tests/unit/test_refs_cursors.py -q`
Expected: `ImportError: cannot import name 'STATE_VALUE_RULES'`.
Observed in the staged proof: 1 error in 1.43s; first error: `ImportError: cannot import name 'STATE_VALUE_RULES' from 'telegram_mcp.authority.cursors'`.

- [ ] **Step 3: Implement**

Apply to `src/telegram_mcp/authority/cursors.py`:

```diff
--- a/src/telegram_mcp/authority/cursors.py
+++ b/src/telegram_mcp/authority/cursors.py
@@ -102,23 +102,32 @@
 _HEX64_RE = re.compile(r"[0-9a-f]{64}\Z")
 _ISO_Z_RE = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z\Z")
 
-# spec §23.5: pagination identifiers/offsets, bounded seen-ID sets, anchors.
-ALLOWED_STATE_KEYS = frozenset(
-    {
-        "add_offset",
-        "anchor_date",
-        "anchor_id",
-        "max_id",
-        "min_id",
-        "offset_date",
-        "offset_id",
-        "offset_peer_ref",
-        "page",
-        "per_peer",
-        "remaining",
-        "seen_ids",
-    }
-)
+# spec §23.5: pagination identifiers/offsets, bounded seen-ID sets, anchors,
+# and (4c) the search continuation window of design §4.3. Every allowed key has
+# exactly one value rule; the table is the single source of both.
+STATE_VALUE_RULES: dict[str, str] = {
+    "add_offset": "int",
+    "anchor_date": "date",
+    "anchor_id": "int",
+    "max_id": "int",
+    "min_id": "int",
+    "next_unstarted_index": "int",
+    "offset_date": "date",
+    "offset_id": "int",
+    "offset_peer_ref": "peer_ref",
+    "page": "int",
+    "per_peer": "per_peer",
+    "remaining": "int",
+    "seen_ids": "seen_ids",
+    "universe_digest": "hmac",
+    "upper_date": "date",
+    "window_start": "int",
+}
+ALLOWED_STATE_KEYS = frozenset(STATE_VALUE_RULES)
+# A per_peer entry holds only its own search offset (design §4.3): exhaustion is
+# encoded by absence, so there is no flag to spoof.
+_PER_PEER_ENTRY_KEYS = frozenset({"offset_id"})
+_HMAC_RE = re.compile(r"hmac-sha256:[0-9a-f]{64}\Z")
 _INT_MAX = 2**63 - 1
 _SEEN_IDS_MAX = 1024
 _PER_PEER_MAX = 64
@@ -335,31 +344,36 @@
 
 
 def _check_state_value(key: str, value: Any, *, nested: bool = False) -> None:
-    if key == "seen_ids":
+    rule = STATE_VALUE_RULES[key]
+    if rule == "seen_ids":
         if not isinstance(value, list) or len(value) > _SEEN_IDS_MAX:
             raise ValueError("invalid cursor state: seen_ids")
         for item in value:
             if not isinstance(item, int) or isinstance(item, bool) or not 0 <= item <= _INT_MAX:
                 raise ValueError("invalid cursor state: seen_ids")
         return
-    if key == "per_peer":
+    if rule == "per_peer":
         if nested:
             raise ValueError("invalid cursor state: per_peer nesting")
         if not isinstance(value, dict) or len(value) > _PER_PEER_MAX:
             raise ValueError("invalid cursor state: per_peer")
         for peer_ref, sub in value.items():
             validate_ref_format(peer_ref, expect="tgp_")
-            if not isinstance(sub, dict):
-                raise ValueError("invalid cursor state: per_peer")  # noqa: TRY004 -- uniform ValueError on state validation
+            if not isinstance(sub, dict) or set(sub) - _PER_PEER_ENTRY_KEYS:
+                raise ValueError("invalid cursor state: per_peer")
             _check_state(sub, nested=True)
         return
-    if key.endswith("_date"):
+    if rule == "date":
         if not isinstance(value, str) or _ISO_Z_RE.fullmatch(value) is None:
             raise ValueError(f"invalid cursor state: {key}")
         return
-    if key == "offset_peer_ref":
+    if rule == "peer_ref":
         validate_ref_format(value, expect="tgp_")
         return
+    if rule == "hmac":
+        if not isinstance(value, str) or _HMAC_RE.fullmatch(value) is None:
+            raise ValueError(f"invalid cursor state: {key}")
+        return
     if not isinstance(value, int) or isinstance(value, bool) or not 0 <= value <= _INT_MAX:
         raise ValueError(f"invalid cursor state: {key}")
 
```

- [ ] **Step 4: Run them and watch them pass**

Run: `uv run pytest tests/unit/test_cursor_state_rules.py tests/unit/test_refs_cursors.py -q`
Expected: 40 passed (observed in the staged proof).

- [ ] **Step 5: Commit** (format, check, full gate first)

Full gate expected at this task: suite 873 passed, 10 skipped; smoke 49 passed, 0 failed, 0 skipped — 49 checks.

```bash
git add tests/unit/test_cursor_state_rules.py src/telegram_mcp/authority/cursors.py
git commit -m "feat: give every cursor-state key one rule and add the search window

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: Adapter: anchors by id, topic replies, per-peer search, response attribution

Design §4.1–§4.2 and §4.7. Three typed reads join the adapter under the `mcp.retrieval` allowlist:
- `fetch_by_ids`, which uses `messages.GetMessages` or `channels.GetMessages` and reports whether the chat is a forum;
- `fetch_replies`, which uses `messages.GetReplies`;
- `search_peer`, which uses `messages.Search` for one peer and never `SearchGlobal`. `None` date bounds go out as 0 (unbounded), and it returns `SearchPage(views, exhausted, inexact, next_offset)`.

`fetch_history` gains `add_offset` and `min_id`. `MessageView` gains the private topic fields `reply_to_top_id` and `topic_root`, and `DialogView` gains `is_forum`.

Two hardening rules close gaps that execution found:
- a message whose `peer_id` is not the requested chat is dropped;
- `peer_dialogs` returns only peers that were requested and resolved from the cache.

The reviewer's copies in the architecture test and the recorder gain the two request classes.

**Files:**
- Create: `tests/unit/test_adapter_4c.py`
- Modify: `tests/security/test_phase4_architecture.py`
- Modify: `tests/telegram/recorder.py`
- Modify: `src/telegram_mcp/telegram/telethon_adapter.py`

**Interfaces:**
- Produces:
  - `TelethonSession.fetch_by_ids(peer_type, peer_id, ids, *, client_ref, deadline, budget) -> tuple[list[MessageView], bool]`
  - `TelethonSession.fetch_replies(peer_type, peer_id, topic_id, *, offset_id, add_offset, limit, min_id, max_id, client_ref, deadline, budget) -> list[MessageView]`
  - `TelethonSession.search_peer(peer_type, peer_id, query, *, min_date, max_date, offset_id, limit, client_ref, deadline, budget) -> SearchPage`
  - `fetch_history(..., add_offset: int = 0, min_id: int = 0)`; `MessageView.reply_to_top_id: int | None`, `MessageView.topic_root: bool`; `DialogView.is_forum: bool`

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_adapter_4c.py`:

```python
"""4c adapter primitives: by-id anchors, topic replies, per-peer search."""

from datetime import UTC, datetime

import pytest
from telethon.tl import functions, types

from telegram_mcp.telegram.deadline import Deadline, WorkBudget
from telegram_mcp.telegram.errors import GatewayError
from telegram_mcp.telegram.telethon_adapter import TelegramConfig, TelethonSession
from tests.telegram.fake_client import FakeClient

WHEN = datetime(2026, 9, 21, 8, 0, 0, tzinfo=UTC)
ALI = types.User(id=100, access_hash=1, first_name="Ali")
FORUM = types.Channel(
    id=8,
    title="Forum",
    photo=types.ChatPhotoEmpty(),
    date=WHEN,
    megagroup=True,
    forum=True,
    access_hash=4,
)


def _kw():
    """Fresh per call: a module-level Deadline expires, and a shared budget drains."""
    return {"client_ref": "c", "deadline": Deadline(5), "budget": WorkBudget()}


async def _session(tmp_path, script, *cached):
    fake = FakeClient(script)
    for entity in cached:
        fake.session.remember(entity)
    session = TelethonSession(
        TelegramConfig(api_id=1, session_dir=tmp_path / "s"),
        api_hash="0" * 32,
        client_factory=lambda *a, **k: fake,
    )
    await session.start()
    fake.calls.clear()
    return session, fake


def _msgs(*messages, chats=(), users=()):
    return types.messages.Messages(
        messages=list(messages), topics=[], chats=list(chats), users=list(users)
    )


async def test_anchor_by_id_classifies_topics_and_the_forum(tmp_path):
    seen = []

    def by_id(request):
        seen.append(request)
        return types.messages.ChannelMessages(
            pts=1,
            count=3,
            topics=[],
            chats=[FORUM],
            users=[],
            messages=[
                types.Message(
                    id=50,
                    peer_id=types.PeerChannel(8),
                    date=WHEN,
                    message="in topic",
                    reply_to=types.MessageReplyHeader(
                        forum_topic=True, reply_to_msg_id=40, reply_to_top_id=12
                    ),
                ),
                types.MessageService(
                    id=12,
                    peer_id=types.PeerChannel(8),
                    date=WHEN,
                    action=types.MessageActionTopicCreate(title="T", icon_color=0),
                ),
                types.MessageEmpty(id=51, peer_id=types.PeerChannel(8)),
            ],
        )

    session, fake = await _session(tmp_path, {"channels.GetMessagesRequest": by_id}, FORUM)
    views, is_forum = await session.fetch_by_ids("channel", 8, [50, 12, 51], **_kw())
    assert is_forum is True and fake.calls == ["channels.GetMessagesRequest"]
    reply, root = views  # the deleted entry is gone
    assert (reply.reply_to_top_id, reply.forum_topic, reply.topic_root) == (12, True, False)
    assert root.topic_root is True
    assert [m.id for m in seen[0].id] == [50, 12, 51]


async def test_a_dm_anchor_uses_messages_get_messages(tmp_path):
    session, fake = await _session(
        tmp_path,
        {
            "messages.GetMessagesRequest": _msgs(
                types.Message(id=5, peer_id=types.PeerUser(100), date=WHEN, message="hi"),
                users=[ALI],
            )
        },
        ALI,
    )
    views, is_forum = await session.fetch_by_ids("user", 100, [5], **_kw())
    assert [v.message_id for v in views] == [5] and is_forum is False
    assert fake.calls == ["messages.GetMessagesRequest"]


async def test_replies_stay_inside_the_topic_request(tmp_path):
    seen = []
    session, _fake = await _session(
        tmp_path, {"messages.GetRepliesRequest": lambda r: seen.append(r) or _msgs()}, FORUM
    )
    await session.fetch_replies(
        "channel", 8, 12, offset_id=50, add_offset=-3, limit=3, min_id=50, max_id=0, **_kw()
    )
    request = seen[0]
    assert (
        request.msg_id,
        request.offset_id,
        request.add_offset,
        request.limit,
        request.min_id,
    ) == (12, 50, -3, 3, 50)


async def test_search_passes_absent_bounds_as_zero_and_reports_completeness(tmp_path):
    seen = []

    def search(request):
        seen.append(request)
        return types.messages.MessagesSlice(
            count=10,
            inexact=True,
            topics=[],
            chats=[],
            users=[ALI],
            messages=[
                types.Message(id=i, peer_id=types.PeerUser(100), date=WHEN, message="needle")
                for i in (9, 8)
            ],
        )

    session, _fake = await _session(tmp_path, {"messages.SearchRequest": search}, ALI)
    page = await session.search_peer(
        "user", 100, "needle", min_date=None, max_date=None, offset_id=0, limit=2, **_kw()
    )
    assert seen[0].min_date is None and seen[0].max_date is None  # Telethon sends 0: unbounded
    assert isinstance(seen[0].filter, types.InputMessagesFilterEmpty) and seen[0].q == "needle"
    assert (page.exhausted, page.inexact, page.next_offset) == (False, True, 8)
    last = await session.search_peer(
        "user", 100, "needle", min_date=None, max_date=None, offset_id=8, limit=5, **_kw()
    )
    assert last.exhausted is True and last.next_offset is None


async def test_global_search_is_never_sendable(tmp_path):
    session, fake = await _session(tmp_path, {})
    request = functions.messages.SearchGlobalRequest(
        q="x",
        filter=types.InputMessagesFilterEmpty(),
        min_date=None,
        max_date=None,
        offset_rate=0,
        offset_peer=types.InputPeerEmpty(),
        offset_id=0,
        limit=1,
    )
    with pytest.raises(GatewayError) as exc:
        await session._call_reviewed(request, operation="mcp.retrieval", **_kw())
    assert exc.value.code == "INTERNAL_ERROR" and fake.calls == []


async def test_messages_from_another_chat_are_never_attributed(tmp_path):
    session, _fake = await _session(
        tmp_path,
        {
            "messages.SearchRequest": _msgs(
                types.Message(id=1, peer_id=types.PeerUser(100), date=WHEN, message="mine"),
                types.Message(
                    id=2, peer_id=types.PeerUser(555), date=WHEN, message="someone else's"
                ),
            )
        },
        ALI,
    )
    page = await session.search_peer(
        "user", 100, "x", min_date=None, max_date=None, offset_id=0, limit=5, **_kw()
    )
    assert [v.text for v in page.views] == ["mine"]


async def test_dialogs_nobody_asked_for_are_not_returned(tmp_path):
    other = types.User(id=555, access_hash=2, first_name="Zed")
    dialogs = types.messages.PeerDialogs(
        dialogs=[
            types.Dialog(
                peer=types.PeerUser(u.id),
                top_message=1,
                read_inbox_max_id=0,
                read_outbox_max_id=0,
                unread_count=0,
                unread_mentions_count=0,
                unread_reactions_count=0,
                unread_poll_votes_count=0,
                notify_settings=types.PeerNotifySettings(),
            )
            for u in (ALI, other)
        ],
        messages=[],
        chats=[],
        users=[ALI, other],
        state=types.updates.State(pts=1, qts=0, date=WHEN, seq=0, unread_count=0),
    )
    session, _fake = await _session(tmp_path, {"messages.GetPeerDialogsRequest": dialogs}, ALI)
    got = await session.peer_dialogs([("user", 100), ("user", 555)], **_kw())  # 555 is not cached
    assert set(got) == {"user:100"}
```

Apply to `tests/security/test_phase4_architecture.py`:

```diff
--- a/tests/security/test_phase4_architecture.py
+++ b/tests/security/test_phase4_architecture.py
@@ -32,6 +32,8 @@
         "account.GetPasswordRequest",
         "auth.CheckPasswordRequest",
         "help.GetConfigRequest",
+        "messages.GetRepliesRequest",
+        "messages.SearchRequest",
     }
 )
 
```

Apply to `tests/telegram/recorder.py`:

```diff
--- a/tests/telegram/recorder.py
+++ b/tests/telegram/recorder.py
@@ -48,6 +48,8 @@
             "messages.GetHistoryRequest",
             "messages.GetMessagesRequest",
             "channels.GetMessagesRequest",
+            "messages.GetRepliesRequest",
+            "messages.SearchRequest",
         }
     ),
 }
```

- [ ] **Step 2: Run them and watch them fail**

Run: `uv run pytest tests/unit/test_adapter_4c.py tests/security/test_phase4_architecture.py tests/unit/test_recorder.py -q`
Expected: `AttributeError: 'TelethonSession' object has no attribute 'fetch_by_ids'` (and the architecture test's reviewed-set equality fails until the adapter lists the two new requests).
Observed in the staged proof: 7 failed, 13 passed in 7.43s; first error: `AttributeError: 'TelethonSession' object has no attribute 'fetch_by_ids'`.

- [ ] **Step 3: Implement**

Apply to `src/telegram_mcp/telegram/telethon_adapter.py`:

```diff
--- a/src/telegram_mcp/telegram/telethon_adapter.py
+++ b/src/telegram_mcp/telegram/telethon_adapter.py
@@ -41,6 +41,7 @@
     "REVIEWED_REQUESTS",
     "DialogView",
     "MessageView",
+    "SearchPage",
     "TelegramConfig",
     "TelethonSession",
     "qualified",
@@ -68,6 +69,8 @@
             "messages.GetHistoryRequest",
             "messages.GetMessagesRequest",
             "channels.GetMessagesRequest",
+            "messages.GetRepliesRequest",  # 4c: get_context inside a forum topic
+            "messages.SearchRequest",  # 4c: per-peer search, never SearchGlobal
         }
     ),
 }
@@ -542,9 +545,11 @@
     ) -> dict[str, DialogView]:
         """GetPeerDialogs for known peers, 100 at a time; cache misses are skipped."""
         wanted = []
+        requested: set[str] = set()
         for peer_type, peer_id in identities:
             try:
                 wanted.append(types.InputDialogPeer(peer=self.input_peer(peer_type, peer_id)))
+                requested.add(f"{peer_type}:{peer_id}")
             except GatewayError:
                 continue  # not in the entity cache: never looked up over the network
         out: dict[str, DialogView] = {}
@@ -557,8 +562,23 @@
                 budget=budget,
             )
             for view in _views(result):
-                out[view.identity] = view
+                if view.identity in requested:  # a dialog nobody asked for is never admitted
+                    out[view.identity] = view
         return out
+
+    def _message_views(
+        self, result: Any, chat: tuple[str, int]
+    ) -> tuple[list[MessageView], dict[tuple[str, int], Any]]:
+        entities = {
+            TelethonSession.identity_of(entity): entity for entity in [*result.users, *result.chats]
+        }
+        views = [
+            _message_view(message, chat, entities)
+            for message in result.messages
+            if not isinstance(message, types.MessageEmpty)
+            and _belongs(message, chat)  # never attribute another chat's message to this one
+        ]
+        return views, entities
 
     async def fetch_history(
         self,
@@ -571,6 +591,8 @@
         client_ref: str,
         deadline: Deadline,
         budget: WorkBudget,
+        add_offset: int = 0,
+        min_id: int = 0,
     ) -> tuple[list[MessageView], int | None]:
         """One GetHistory page, newest first; deleted entries dropped.
 
@@ -583,10 +605,10 @@
                 peer=peer,
                 offset_id=offset_id,
                 offset_date=None,
-                add_offset=0,
+                add_offset=add_offset,
                 limit=limit,
                 max_id=max_id,
-                min_id=0,
+                min_id=min_id,
                 hash=0,
             ),
             operation="mcp.retrieval",
@@ -594,19 +616,125 @@
             deadline=deadline,
             budget=budget,
         )
-        entities = {
-            TelethonSession.identity_of(entity): entity for entity in [*result.users, *result.chats]
-        }
-        chat = (peer_type, peer_id)
-        views = [
-            _message_view(message, chat, entities)
-            for message in result.messages
-            if not isinstance(message, types.MessageEmpty)
-        ]
+        views, _entities = self._message_views(result, (peer_type, peer_id))
         full = len(result.messages) >= limit and bool(result.messages)
         return views, (min(int(m.id) for m in result.messages) if full else None)
 
+    async def fetch_by_ids(
+        self,
+        peer_type: str,
+        peer_id: int,
+        ids: list[int],
+        *,
+        client_ref: str,
+        deadline: Deadline,
+        budget: WorkBudget,
+    ) -> tuple[list[MessageView], bool]:
+        """Messages by id (the get_context anchor), and whether the chat is a forum."""
+        peer = self.input_peer(peer_type, peer_id)
+        wanted = [types.InputMessageID(int(i)) for i in ids]
+        request = (
+            functions.channels.GetMessagesRequest(peer, wanted)
+            if peer_type == "channel"
+            else functions.messages.GetMessagesRequest(wanted)
+        )
+        result = await self._call_reviewed(
+            request,
+            operation="mcp.retrieval",
+            client_ref=client_ref,
+            deadline=deadline,
+            budget=budget,
+        )
+        chat = (peer_type, peer_id)
+        views, entities = self._message_views(result, chat)
+        return views, bool(getattr(entities.get(chat), "forum", False))
 
+    async def fetch_replies(
+        self,
+        peer_type: str,
+        peer_id: int,
+        topic_id: int,
+        *,
+        offset_id: int,
+        add_offset: int,
+        limit: int,
+        min_id: int,
+        max_id: int,
+        client_ref: str,
+        deadline: Deadline,
+        budget: WorkBudget,
+    ) -> list[MessageView]:
+        """messages.GetReplies inside one forum topic: it never crosses into another."""
+        peer = self.input_peer(peer_type, peer_id)
+        result = await self._call_reviewed(
+            functions.messages.GetRepliesRequest(
+                peer=peer,
+                msg_id=topic_id,
+                offset_id=offset_id,
+                offset_date=None,
+                add_offset=add_offset,
+                limit=limit,
+                max_id=max_id,
+                min_id=min_id,
+                hash=0,
+            ),
+            operation="mcp.retrieval",
+            client_ref=client_ref,
+            deadline=deadline,
+            budget=budget,
+        )
+        views, _entities = self._message_views(result, (peer_type, peer_id))
+        return views
+
+    async def search_peer(
+        self,
+        peer_type: str,
+        peer_id: int,
+        query: str,
+        *,
+        min_date: datetime | None,
+        max_date: datetime | None,
+        offset_id: int,
+        limit: int,
+        client_ref: str,
+        deadline: Deadline,
+        budget: WorkBudget,
+    ) -> SearchPage:
+        """One messages.Search page in one peer (never SearchGlobal).
+
+        ``None`` bounds are sent as 0, which Telegram reads as unbounded.
+        """
+        peer = self.input_peer(peer_type, peer_id)
+        result = await self._call_reviewed(
+            functions.messages.SearchRequest(
+                peer=peer,
+                q=query,
+                filter=types.InputMessagesFilterEmpty(),
+                min_date=min_date,
+                max_date=max_date,
+                offset_id=offset_id,
+                add_offset=0,
+                limit=limit,
+                max_id=0,
+                min_id=0,
+                hash=0,
+            ),
+            operation="mcp.retrieval",
+            client_ref=client_ref,
+            deadline=deadline,
+            budget=budget,
+        )
+        views, _entities = self._message_views(result, (peer_type, peer_id))
+        raw = list(result.messages)
+        exhausted = len(raw) < limit or not raw
+        return SearchPage(
+            views=views,
+            exhausted=exhausted,
+            inexact=bool(getattr(result, "inexact", False)),
+            next_offset=None if exhausted else min(int(m.id) for m in raw),
+        )
+
+
 @dataclass(frozen=True)
 class DialogView:
     peer_type: str
@@ -620,6 +748,7 @@
     last_message_at: str | None
     read_outbox_max_id: int
     top_message_id: int
+    is_forum: bool = False
 
     @property
     def identity(self) -> str:
@@ -679,6 +808,7 @@
                 last_message_at=_iso(getattr(top, "date", None)),
                 read_outbox_max_id=int(dialog.read_outbox_max_id or 0),
                 top_message_id=int(dialog.top_message or 0),
+                is_forum=bool(getattr(entity, "forum", False)),
             )
         )
     return views
@@ -699,6 +829,10 @@
     has_media: bool
     media_kind: str | None
     edited: bool
+    # Topic classification for get_context (design §4.1). Never exposed: the
+    # contract carries only forum_topic, and raw topic ids must not leave.
+    reply_to_top_id: int | None = None
+    topic_root: bool = False
 
 
 def _media_kind(media: Any) -> str | None:
@@ -737,11 +871,17 @@
     service = isinstance(message, types.MessageService)
     reply = getattr(message, "reply_to", None)
     reply_to_id = None
+    reply_to_top_id = None
     forum_topic = False
     if isinstance(reply, types.MessageReplyHeader):
         forum_topic = bool(reply.forum_topic)
         if reply.reply_to_peer_id is None and reply.reply_to_msg_id:
             reply_to_id = int(reply.reply_to_msg_id)
+        if reply.reply_to_top_id:
+            reply_to_top_id = int(reply.reply_to_top_id)
+    topic_root = service and isinstance(
+        getattr(message, "action", None), types.MessageActionTopicCreate
+    )
     media_kind = None if service else _media_kind(getattr(message, "media", None))
     entity = entities.get(sender) if sender is not None else None
     return MessageView(
@@ -758,4 +898,23 @@
         has_media=media_kind is not None,
         media_kind=media_kind,
         edited=bool(getattr(message, "edit_date", None)) and not bool(message.edit_hide),
+        reply_to_top_id=reply_to_top_id,
+        topic_root=topic_root,
     )
+
+
+@dataclass(frozen=True)
+class SearchPage:
+    """One per-peer search page: views plus Telegram's own completeness signals."""
+
+    views: list[MessageView]
+    exhausted: bool
+    inexact: bool
+    next_offset: int | None
+
+
+def _belongs(message: Any, chat: tuple[str, int]) -> bool:
+    try:
+        return TelethonSession.identity_of(message.peer_id) == chat
+    except GatewayError:
+        return False
```

- [ ] **Step 4: Run them and watch them pass**

Run: `uv run pytest tests/unit/test_adapter_4c.py tests/security/test_phase4_architecture.py tests/unit/test_recorder.py -q`
Expected: 20 passed (observed in the staged proof).

- [ ] **Step 5: Commit** (format, check, full gate first)

Full gate expected at this task: suite 880 passed, 10 skipped; smoke 49 passed, 0 failed, 0 skipped — 49 checks.

```bash
git add tests/unit/test_adapter_4c.py tests/security/test_phase4_architecture.py tests/telegram/recorder.py src/telegram_mcp/telegram/telethon_adapter.py
git commit -m "feat: read anchors, topic replies and per-peer search pages

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: Worst-case bounds for the three 4c tools

Phase-3 design §5.4 and 4b Task 12. `get_context` shares the message shape. A search hit gets its own shape; for cross-project search it lists every selected project in `origin_project_refs` and `matched_projects`. The estimate for cross-project search uses the widest selected grant, because each record takes its own intersection. The 4b test that pinned search as unbounded now pins a tool that still has no project bound.

**Files:**
- Create: `tests/unit/test_bounds_4c.py`
- Modify: `tests/unit/test_bounds.py`
- Modify: `src/telegram_mcp/disclosure/bounds.py`

**Interfaces:**
- Produces:
  - `worst_case(tool_name, *, limit, project_ref, project_display_name, egress_level, excerpt_limit, projects: Sequence[tuple[str, str]] = ())` for `telegram_get_context`, `telegram_search_messages` and `telegram_cross_project_search`

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_bounds_4c.py`:

```python
"""4c worst-case shapes dominate the widest real records."""

from telegram_mcp.consent.challenge import jcs_dumps
from telegram_mcp.disclosure.bounds import DATA_BYTES_MAX, NAME_MAX, TEXT_MAX, worst_case

P, Q = "tpr_" + "a" * 26, "tpr_" + "b" * 26
R, M = "tgp_" + "b" * 26, "tgm_" + "c" * 26
W = "\x01"


def _hit(origins, matched=None):
    hit = {
        "origin_project_refs": origins,
        "peer_ref": R,
        "peer_display_name": W * NAME_MAX,
        "message_ref": M,
        "sender_kind": "anonymous_admin",
        "sender_display_name": W * NAME_MAX,
        "sent_at": "2026-09-23T00:00:00Z",
        "text": W * 64,
        "text_truncated": True,
        "has_context": True,
    }
    if matched is not None:
        hit["matched_projects"] = matched
    return hit


def test_search_bound_dominates_a_real_hit():
    records, total, project = worst_case(
        "telegram_search_messages",
        limit=1,
        project_ref=P,
        project_display_name="Alpha",
        egress_level="excerpt",
        excerpt_limit=64,
    )
    data = {
        "project": {"project_ref": P, "display_name": "Alpha"},
        "results": [_hit([P])],
        "search_scope": "project",
    }
    assert records == 1 and project >= len(jcs_dumps(_hit([P]))) and total >= len(jcs_dumps(data))


def test_cross_bound_names_every_selected_project():
    projects = [(P, "Alpha"), (Q, "انجمن")]
    matched = [{"project_ref": r, "display_name": d} for r, d in projects]
    _records, total, project = worst_case(
        "telegram_cross_project_search",
        limit=1,
        project_ref="",
        project_display_name="",
        egress_level="excerpt",
        excerpt_limit=64,
        projects=projects,
    )
    data = {
        "projects": matched,
        "results": [_hit([P, Q], matched)],
        "search_scope": "cross_project",
    }
    assert project >= len(jcs_dumps(_hit([P, Q], matched))) and total >= len(jcs_dumps(data))


def test_context_bound_covers_101_full_messages_at_the_page_cap():
    records, total, project = worst_case(
        "telegram_get_context",
        limit=101,
        project_ref=P,
        project_display_name="Alpha",
        egress_level="full_text",
        excerpt_limit=None,
    )
    assert records == 101 and total == project == DATA_BYTES_MAX
    small = worst_case(
        "telegram_get_context",
        limit=1,
        project_ref=P,
        project_display_name="Alpha",
        egress_level="full_text",
        excerpt_limit=None,
    )
    assert small[2] >= TEXT_MAX * 6  # one full-length message fits in the bound
```

Apply to `tests/unit/test_bounds.py`:

```diff
--- a/tests/unit/test_bounds.py
+++ b/tests/unit/test_bounds.py
@@ -108,7 +108,7 @@
 def test_an_unbounded_tool_is_refused():
     with pytest.raises(ValueError):
         worst_case(
-            "telegram_search_messages",
+            "telegram_list_projects",
             limit=1,
             project_ref=P,
             project_display_name="A",
```

- [ ] **Step 2: Run them and watch them fail**

Run: `uv run pytest tests/unit/test_bounds_4c.py tests/unit/test_bounds.py -q`
Expected: `ValueError: no bound for this tool` from the three new-tool cases.
Observed in the staged proof: 3 failed, 10 passed in 0.40s; first error: `ValueError: no bound for this tool`.

- [ ] **Step 3: Implement**

Apply to `src/telegram_mcp/disclosure/bounds.py`:

```diff
--- a/src/telegram_mcp/disclosure/bounds.py
+++ b/src/telegram_mcp/disclosure/bounds.py
@@ -11,6 +11,7 @@
 
 from __future__ import annotations
 
+from collections.abc import Sequence
 from typing import Any
 
 from telegram_mcp.consent.challenge import jcs_dumps
@@ -79,7 +80,9 @@
     raise ValueError("unknown egress level")
 
 
-def _record(tool_name: str, project_ref: str, text: str | None) -> dict[str, Any]:
+def _record(
+    tool_name: str, project_ref: str, text: str | None, projects: Sequence[tuple[str, str]]
+) -> dict[str, Any]:
     name, user = _W * NAME_MAX, _W * USERNAME_MAX
     if tool_name == "telegram_list_chats":
         return {
@@ -110,7 +113,7 @@
             "is_muted": False,
             "last_message_at": _DATE,
         }
-    if tool_name == "telegram_get_messages":
+    if tool_name in ("telegram_get_messages", "telegram_get_context"):
         return {
             "message_ref": _MSG,
             "origin_project_refs": [project_ref],
@@ -129,6 +132,26 @@
             "media_kind": _W * MEDIA_KIND_MAX,
             "edited": False,
         }
+    if tool_name in ("telegram_search_messages", "telegram_cross_project_search"):
+        hit: dict[str, Any] = {
+            "origin_project_refs": [project_ref],
+            "peer_ref": _PEER,
+            "peer_display_name": name,
+            "message_ref": _MSG,
+            "sender_kind": "anonymous_admin",
+            "sender_display_name": name,
+            "sent_at": _DATE,
+            "text": text,
+            "text_truncated": False,
+            "has_context": False,
+        }
+        if tool_name == "telegram_cross_project_search":
+            # Every selected project may match: the widest record names them all.
+            hit["origin_project_refs"] = [ref for ref, _ in projects]
+            hit["matched_projects"] = [
+                {"project_ref": ref, "display_name": display} for ref, display in projects
+            ]
+        return hit
     raise ValueError("no bound for this tool")
 
 
@@ -137,6 +160,9 @@
     "telegram_resolve_peer": "matches",
     "telegram_get_unread": "chats",
     "telegram_get_messages": "messages",
+    "telegram_get_context": "messages",
+    "telegram_search_messages": "results",
+    "telegram_cross_project_search": "results",
 }
 
 
@@ -148,14 +174,29 @@
     project_display_name: str,
     egress_level: str,
     excerpt_limit: int | None,
+    projects: Sequence[tuple[str, str]] = (),
 ) -> tuple[int, int, int]:
-    """``(records, global_bytes, project_bytes)`` for a full page, capped at the page cap."""
-    record = _record(tool_name, project_ref, _text_bound(egress_level, excerpt_limit))
-    data: dict[str, Any] = {
-        "project": {"project_ref": project_ref, "display_name": project_display_name},
-        _ELEMENT[tool_name]: [record] * limit,
-    }
-    if tool_name == "telegram_get_messages":
+    """``(records, global_bytes, project_bytes)`` for a full page, capped at the page cap.
+
+    ``projects`` is the selected set for cross-project search, as
+    ``(project_ref, display_name)``; the egress bound must then be the
+    *widest* selected grant, since each record takes its own intersection.
+    """
+    if tool_name == "telegram_cross_project_search" and not projects:
+        raise ValueError("cross-project bounds need the selected projects")
+    record = _record(tool_name, project_ref, _text_bound(egress_level, excerpt_limit), projects)
+    data: dict[str, Any] = {_ELEMENT[tool_name]: [record] * limit}
+    if tool_name == "telegram_cross_project_search":
+        data["projects"] = [{"project_ref": ref, "display_name": d} for ref, d in projects]
+        data["search_scope"] = "cross_project"
+    else:
+        data["project"] = {"project_ref": project_ref, "display_name": project_display_name}
+    if tool_name == "telegram_search_messages":
+        data["search_scope"] = "project"
+    elif tool_name == "telegram_get_context":
+        data["peer"] = {"peer_ref": _PEER, "display_name": _W * NAME_MAX}
+        data["anchor_message_ref"] = _MSG
+    elif tool_name == "telegram_get_messages":
         data["peer"] = {"peer_ref": _PEER, "display_name": _W * NAME_MAX, "chat_type": "supergroup"}
     elif tool_name == "telegram_get_unread":
         data["total_unread_visible"] = _INT
```

- [ ] **Step 4: Run them and watch them pass**

Run: `uv run pytest tests/unit/test_bounds_4c.py tests/unit/test_bounds.py -q`
Expected: 13 passed (observed in the staged proof).

- [ ] **Step 5: Commit** (format, check, full gate first)

Full gate expected at this task: suite 883 passed, 10 skipped; smoke 49 passed, 0 failed, 0 skipped — 49 checks.

```bash
git add tests/unit/test_bounds_4c.py tests/unit/test_bounds.py src/telegram_mcp/disclosure/bounds.py
git commit -m "feat: bound get_context and search records for the exposure estimate

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: The continuation engine (pure)

Design §4.2–§4.4 and §5. `telegram/search.py` walks a canonical, digest-bound universe through a window of at most 64 active peers, round-robin, one bounded page per call, and reports §23D coverage. It imports neither Telethon nor SQLite.

The invariants, each covered by an exhaustive or seeded test:
- **No lost hits.** A page never collects more than `limit` hits.
- **No spoofable exhaustion.** A peer is exhausted exactly when it is absent from `per_peer` inside the window.
- **No skipped peers.** The window only advances past exhausted peers.
- **Bounded work.** Each request asks for at most the hits still needed and the remaining 500-hit budget.
- **No loss at the cap.** `accept()` holds the page under the response cap, and a refusal makes that peer resume *at* the refused hit.
- **Honest completeness.** `complete` implies no cursor and no reasons.

The tests enumerate every universe of up to three peers, each with 0, 1 or 3 hits, at three limits and three RPC budgets. On top of that they cover the 64→65 window transition, an ordinary universe of 300, and a cross-project universe of 251.

**Files:**
- Create: `tests/unit/test_search_engine.py`
- Create: `src/telegram_mcp/telegram/search.py`

**Interfaces:**
- Produces:
  - `WINDOW = 64`; `SearchStop(reason)`; `Hit(peer, view)`; `EngineState(upper_date, window_start=0, next_unstarted_index=0, per_peer={})`; `PageResult(hits, state, coverage, complete)`
  - `universe_digest(key, universe) -> str`; `rpc_bounds(since, upper) -> (datetime | None, datetime)`
  - `async run_page(universe, state, *, limit, fetch, since, projects, peer_cap=None, max_hits=500, accept=lambda peer, view: True) -> PageResult`

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_search_engine.py`:

```python
"""The continuation engine as a state machine (design §4.3-§4.4, §5).

Exhaustive over small universes, fixed-seed over large ones. Pure: a fake
per-peer searcher stands in for Telegram.
"""

import asyncio
import itertools
import random
from dataclasses import dataclass

import pytest

from telegram_mcp.disclosure.coverage import validate_coverage
from telegram_mcp.telegram.search import (
    WINDOW,
    EngineState,
    SearchStop,
    rpc_bounds,
    run_page,
    universe_digest,
)

UPPER = "2026-09-23T12:00:00Z"
P = "tpr_" + "a" * 26


@dataclass(frozen=True)
class View:
    message_id: int
    sent_at: str


@dataclass
class Page:
    views: list
    exhausted: bool
    inexact: bool
    next_offset: int | None


class FakeSearch:
    """Per peer, a descending list of message ids; minute = id % 60."""

    def __init__(self, hits_per_peer, *, rpc_budget=10**9, inexact=()):
        self.data = hits_per_peer
        self.rpc_budget = rpc_budget
        self.inexact = set(inexact)
        self.scanned: set[str] = set()
        self.rpcs = 0

    async def fetch(self, peer, offset_id, want):
        if self.rpcs >= self.rpc_budget:
            raise SearchStop("rpc_budget")
        self.rpcs += 1
        self.scanned.add(peer)
        ids = [i for i in self.data[peer] if not offset_id or i < offset_id][:want]
        views = [View(i, f"2026-09-23T10:{i % 60:02d}:00Z") for i in ids]
        exhausted = len(ids) < want
        return Page(views, exhausted, peer in self.inexact, None if exhausted else ids[-1])


def _run_all(universe, data, *, limit, rpc_budget=10**9, peer_cap=None, max_pages=10_000):
    """Page to the end. Returns (pages, all hits, fake)."""
    fake = FakeSearch(data, rpc_budget=rpc_budget)
    state = EngineState(upper_date=UPPER)
    pages, hits = [], []
    for _ in range(max_pages):
        fake.rpcs = 0  # a fresh work budget per call
        result = asyncio.run(
            run_page(
                universe,
                state,
                limit=limit,
                fetch=fake.fetch,
                since=None,
                projects={P: frozenset(universe)},
                peer_cap=peer_cap,
            )
        )
        pages.append(result)
        hits += [(h.peer, h.view.message_id) for h in result.hits]
        cursor = "tgc_" + "a" * 26 if result.state is not None else None
        validate_coverage(
            result.coverage, next_cursor=cursor, partial=not result.coverage["complete"]
        )
        assert len(result.hits) <= limit
        if result.state is None:
            return pages, hits, fake
        state = result.state
    raise AssertionError("continuation never ended")


@pytest.mark.parametrize(
    "peers,sizes,limit,budget",
    [
        (peers, sizes, limit, budget)
        for peers in (1, 2, 3)
        for sizes in itertools.product((0, 1, 3), repeat=peers)
        for limit in (1, 2, 5)
        for budget in (1, 2, 50)
    ],
)
def test_exhaustively_every_hit_arrives_exactly_once_and_it_ends(peers, sizes, limit, budget):
    universe = [f"user:{i}" for i in range(peers)]
    data = {
        p: list(range(100 * (k + 1) + n, 100 * (k + 1), -1))
        for k, (p, n) in enumerate(zip(universe, sizes, strict=True))
    }
    pages, hits, _fake = _run_all(universe, data, limit=limit, rpc_budget=budget)
    expected = {(p, i) for p, ids in data.items() for i in ids}
    assert sorted(hits) == sorted(expected) and len(hits) == len(set(hits))  # no loss, no duplicate
    assert pages[-1].complete is True and pages[-1].coverage["peers_scanned"] == peers
    for page in pages[:-1]:
        assert "response_limit" in page.coverage["partial_reasons"]


def test_the_window_holds_64_and_moves_to_peer_65():
    universe = [f"user:{i}" for i in range(70)]
    data = {p: [5, 4, 3, 2, 1] for p in universe}
    state = EngineState(upper_date=UPPER)
    fake = FakeSearch(data)
    asyncio.run(
        run_page(
            universe,
            state,
            limit=1,
            fetch=fake.fetch,
            since=None,
            projects={P: frozenset(universe)},
        )
    )
    assert state.next_unstarted_index == WINDOW and len(state.per_peer) == WINDOW
    _pages, hits, fake = _run_all(universe, data, limit=50)
    assert len(hits) == 70 * 5 and "user:64" in fake.scanned


def test_an_ordinary_universe_of_300_is_fully_searched():
    rng = random.Random(300)
    universe = [f"channel:{i}" for i in range(300)]
    data = {p: sorted(rng.sample(range(1, 400), rng.randint(0, 4)), reverse=True) for p in universe}
    pages, hits, fake = _run_all(universe, data, limit=50, rpc_budget=20)
    assert fake.scanned == set(universe) and pages[-1].complete is True
    assert len(hits) == sum(len(v) for v in data.values())


def test_a_cross_universe_of_251_never_searches_peer_251():
    universe = [f"user:{i}" for i in range(251)]
    data = {p: [1] for p in universe}
    pages, _hits, fake = _run_all(universe, data, limit=50, peer_cap=250)
    assert "user:250" not in fake.scanned and len(fake.scanned) == 250
    for page in pages:
        assert "peer_budget" in page.coverage["partial_reasons"] and page.complete is False
        assert page.coverage["eligible_peers"] == 251


def test_hits_above_the_upper_anchor_never_appear():
    universe = ["user:1"]
    state = EngineState(upper_date="2026-09-23T10:30:00Z")
    fake = FakeSearch({"user:1": [45, 31, 30, 29]})  # minutes 45, 31 are at/after the anchor
    result = asyncio.run(
        run_page(
            universe,
            state,
            limit=10,
            fetch=fake.fetch,
            since=None,
            projects={P: frozenset(universe)},
        )
    )
    assert [h.view.message_id for h in result.hits] == [29]


def test_since_is_inclusive_and_bounds_over_fetch_by_a_second():
    lower, upper = rpc_bounds("2026-09-23T10:00:00.500+00:00", "2026-09-23T11:00:00Z")
    assert lower.isoformat() == "2026-09-23T09:59:59+00:00"
    assert upper.isoformat() == "2026-09-23T11:00:01+00:00"
    assert rpc_bounds(None, UPPER)[0] is None  # absent is 0: unbounded
    assert rpc_bounds("1970-01-01T00:00:00Z", UPPER)[0].timestamp() == 1  # never an accidental 0


def test_a_hit_budget_stops_the_page_with_a_cursor():
    universe = ["user:1"]
    fake = FakeSearch({"user:1": list(range(1000, 0, -1))})
    state = EngineState(upper_date=UPPER)
    result = asyncio.run(
        run_page(
            universe,
            state,
            limit=50,
            fetch=fake.fetch,
            since=None,
            projects={P: frozenset(universe)},
            max_hits=40,
        )
    )
    assert result.coverage["partial_reasons"] == ["hit_budget", "response_limit"]
    assert result.state is not None and len(result.hits) == 40


def test_telegram_uncertainty_is_never_complete():
    universe = ["user:1"]
    fake = FakeSearch({"user:1": [3, 2]}, inexact={"user:1"})
    result = asyncio.run(
        run_page(
            universe,
            EngineState(upper_date=UPPER),
            limit=10,
            fetch=fake.fetch,
            since=None,
            projects={P: frozenset(universe)},
        )
    )
    assert result.complete is False and result.coverage["partial_reasons"] == ["telegram_partial"]
    assert result.state is None  # nothing left to continue, but still not claimed complete


def test_shared_peers_count_once_globally_and_once_per_project():
    universe = ["user:1", "user:2", "user:3"]
    projects = {
        P: frozenset({"user:1", "user:2"}),
        "tpr_" + "b" * 26: frozenset({"user:2", "user:3"}),
    }
    result = asyncio.run(
        run_page(
            universe,
            EngineState(upper_date=UPPER),
            limit=10,
            fetch=FakeSearch({p: [] for p in universe}).fetch,
            since=None,
            projects=projects,
        )
    )
    assert result.coverage["eligible_peers"] == 3
    assert [c["eligible_peers"] for c in result.coverage["project_coverage"]] == [2, 2]


def test_the_universe_digest_is_keyed_and_order_sensitive():
    key = b"\x01" * 32
    one = universe_digest(key, ["user:1", "user:2"])
    assert one.startswith("hmac-sha256:") and len(one) == 12 + 64
    assert (
        one
        != universe_digest(key, ["user:2", "user:1"])
        != universe_digest(b"\x02" * 32, ["user:1", "user:2"])
    )


def test_a_page_held_under_the_cap_loses_and_repeats_nothing():
    """The reads' accept() refuses hits past the response cap; the next page resumes at them."""
    universe = ["user:1", "user:2"]
    data = {"user:1": [9, 8, 7, 6], "user:2": [5, 4, 3]}
    fake = FakeSearch(data)
    state = EngineState(upper_date=UPPER)
    seen, pages = [], 0
    while True:
        taken = []

        def accept(peer, view, taken=taken):
            taken.append(view.message_id)
            return len(taken) < 3  # room for two hits per page

        result = asyncio.run(
            run_page(
                universe,
                state,
                limit=10,
                fetch=fake.fetch,
                since=None,
                projects={P: frozenset(universe)},
                accept=accept,
            )
        )
        pages += 1
        seen += [(h.peer, h.view.message_id) for h in result.hits]
        assert len(result.hits) <= 3
        if result.state is None:
            break
        assert "response_limit" in result.coverage["partial_reasons"]
        state = result.state
    assert sorted(seen) == sorted((p, i) for p, ids in data.items() for i in ids)
    assert len(seen) == len(set(seen)) and pages >= 3
```

- [ ] **Step 2: Run them and watch them fail**

Run: `uv run pytest tests/unit/test_search_engine.py -q`
Expected: `ModuleNotFoundError: No module named 'telegram_mcp.telegram.search'`.
Observed in the staged proof: 1 error in 1.11s; first error: `ModuleNotFoundError: No module named 'telegram_mcp.telegram.search'`.

- [ ] **Step 3: Implement**

Create `src/telegram_mcp/telegram/search.py`:

```python
"""The search continuation engine (spec §21, §21A, §23D; design §4.2-§4.4, §5).

Pure: no Telethon, no SQLite. It walks a canonical, digest-bound peer
universe through a window of at most 64 active peers, round-robin, one
bounded page per call, and reports §23D coverage. The caller supplies a
``fetch(identity, offset_id, want)`` coroutine returning a page with
``views`` (each with ``message_id`` and ``sent_at``), ``exhausted``,
``inexact`` and ``next_offset``, and raises :class:`SearchStop` when a
work bound fires.

The invariants, each tested exhaustively in ``test_search_engine.py``:

- a page never collects more than ``limit`` hits, so nothing examined is lost;
- a peer is exhausted exactly when it is absent from ``per_peer`` inside the
  window (no flag to spoof), and the window only advances past exhausted peers;
- across a full continuation the union of scanned peers is the universe
  (or its first ``peer_cap`` peers, with ``peer_budget`` on every page);
- ``complete`` implies no cursor, no partial reasons, every peer scanned; any
  cursor carries ``response_limit``.
"""

from __future__ import annotations

import hashlib
import hmac
import math
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from telegram_mcp.consent.challenge import jcs_dumps
from telegram_mcp.disclosure.coverage import build_coverage

__all__ = [
    "WINDOW",
    "EngineState",
    "Hit",
    "PageResult",
    "SearchStop",
    "rpc_bounds",
    "run_page",
    "universe_digest",
]

WINDOW = 64  # design §4.3: the reviewed _PER_PEER_MAX
MAX_PEER_PAGE = 100  # Telegram's messages.search page ceiling
_UNIVERSE_DOMAIN = b"telegram-mcp-universe/v1\0"
_EPOCH_FLOOR = datetime(1970, 1, 1, 0, 0, 1, tzinfo=UTC)


class SearchStop(Exception):
    """A §23D work bound fired: deadline, rpc_budget or hit_budget."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class Hit:
    peer: str  # canonical identity, "<type>:<id>"
    view: Any  # a MessageView; the engine reads only message_id and sent_at


@dataclass
class EngineState:
    upper_date: str
    window_start: int = 0
    next_unstarted_index: int = 0
    per_peer: dict[str, int] = field(default_factory=dict)  # identity -> offset_id


@dataclass(frozen=True)
class PageResult:
    hits: list[Hit]
    state: EngineState | None  # None: nothing left to continue
    coverage: dict[str, Any]
    complete: bool


def universe_digest(key: bytes, universe: Sequence[str]) -> str:
    """HMAC of the full ordered universe; the cursor stores this, never the list."""
    mac = hmac.new(key, _UNIVERSE_DOMAIN + jcs_dumps(list(universe)), hashlib.sha256)
    return "hmac-sha256:" + mac.hexdigest()


def _iso(moment: datetime) -> str:
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse(text: str) -> datetime:
    return datetime.fromisoformat(text).astimezone(UTC)


def rpc_bounds(since: str | None, upper: str) -> tuple[datetime | None, datetime]:
    """Telegram's min/max_date are strict and 0 means unbounded (design §4.2).

    Over-fetch by a second on each side and post-filter. An absent ``since``
    is sent as 0 on purpose; a present one is clamped to at least 1 so an
    epoch-era ``since`` can never become an accidental 0.
    """
    upper_rpc = datetime.fromtimestamp(math.ceil(_parse(upper).timestamp()), UTC) + timedelta(
        seconds=1
    )
    if since is None:
        return None, upper_rpc
    lower = datetime.fromtimestamp(math.floor(_parse(since).timestamp()), UTC) - timedelta(
        seconds=1
    )
    return max(lower, _EPOCH_FLOOR), upper_rpc


def _in_range(sent_at: str, since: str | None, upper: str) -> bool:
    moment = _parse(sent_at)
    return (since is None or moment >= _parse(since)) and moment < _parse(upper)


async def run_page(
    universe: Sequence[str],
    state: EngineState,
    *,
    limit: int,
    fetch: Callable[[str, int, int], Awaitable[Any]],
    since: str | None,
    projects: Mapping[str, frozenset[str]],
    peer_cap: int | None = None,
    max_hits: int = 500,
    accept: Callable[[str, Any], bool] = lambda peer, view: True,
) -> PageResult:
    """One bounded page. ``state.upper_date`` is the frozen upper anchor.

    ``accept(peer, view)`` is asked before each in-range hit is taken; the
    reads use it to hold a page under the response cap. A refusal ends the
    page with that peer resuming *at* the refused hit, so nothing examined
    is ever lost or repeated.
    """
    searchable = list(universe[:peer_cap]) if peer_cap is not None else list(universe)
    capped = peer_cap is not None and len(universe) > peer_cap
    hits: list[Hit] = []
    reasons: list[str] = []
    rpcs = examined = 0
    stopped = False

    def active() -> list[str]:
        return [
            searchable[i]
            for i in range(state.window_start, state.next_unstarted_index)
            if searchable[i] in state.per_peer
        ]

    def refill() -> None:
        while len(active()) < WINDOW and state.next_unstarted_index < len(searchable):
            state.per_peer[searchable[state.next_unstarted_index]] = 0
            state.next_unstarted_index += 1

    def advance() -> None:
        # The window only moves past exhausted peers: nothing is ever skipped.
        while (
            state.window_start < state.next_unstarted_index
            and searchable[state.window_start] not in state.per_peer
        ):
            state.window_start += 1

    refill()
    while len(hits) < limit and not stopped:
        peers = active()
        if not peers:
            break
        for peer in peers:
            if len(hits) >= limit:
                break
            # Never ask for more than the page, Telegram's ceiling, or the
            # examined-hit budget still allows: nothing is examined and lost.
            want = min(limit - len(hits), MAX_PEER_PAGE, max_hits - examined)
            try:
                page = await fetch(peer, state.per_peer[peer], want)
            except SearchStop as stop:
                reasons.append(stop.reason)
                stopped = True
                break
            rpcs += 1
            examined += len(page.views)
            if page.inexact and "telegram_partial" not in reasons:
                reasons.append("telegram_partial")
            full = False
            for view in page.views[:want]:
                if not _in_range(view.sent_at, since, state.upper_date):
                    continue
                # Always asked (so the caller counts every hit); a refusal is honoured
                # once the page holds one hit, which the response bounds guarantee fits.
                if not accept(peer, view) and hits:
                    state.per_peer[peer] = int(view.message_id) + 1  # offset_id is exclusive
                    full = True
                    break
                hits.append(Hit(peer, view))
            if full:
                stopped = True
                break
            if page.exhausted:
                del state.per_peer[peer]
            else:
                state.per_peer[peer] = int(page.next_offset)
            if examined >= max_hits:  # spec §13.2: at most 500 examined hits per call
                reasons.append("hit_budget")
                stopped = True
                break
        advance()
        refill()

    advance()
    remaining = bool(active()) or state.next_unstarted_index < len(searchable)
    if capped:
        reasons.append("peer_budget")
    if remaining:
        reasons.append("response_limit")
    complete = not reasons
    hits.sort(key=lambda h: (h.view.sent_at, h.peer, h.view.message_id), reverse=True)
    scanned = set(searchable[: state.next_unstarted_index])
    project_coverage = [
        {
            "project_ref": ref,
            "eligible_peers": sum(1 for p in universe if p in members),
            "peers_scanned": sum(1 for p in scanned if p in members),
        }
        for ref, members in projects.items()
    ]
    coverage = build_coverage(
        complete=complete,
        eligible_peers=len(universe),
        peers_scanned=len(scanned),
        telegram_rpcs=rpcs,
        hits_examined=examined,
        hits_returned=len(hits),
        partial_reasons=_ordered(reasons),
        project_coverage=project_coverage,
    )
    return PageResult(
        hits=hits, state=state if remaining else None, coverage=coverage, complete=complete
    )


def _ordered(reasons: Sequence[str]) -> list[str]:
    order = (
        "deadline",
        "rpc_budget",
        "hit_budget",
        "peer_budget",
        "response_limit",
        "telegram_partial",
    )
    return [r for r in order if r in set(reasons)]
```

- [ ] **Step 4: Run them and watch them pass**

Run: `uv run pytest tests/unit/test_search_engine.py -q`
Expected: 361 passed (observed in the staged proof).

- [ ] **Step 5: Commit** (format, check, full gate first)

Full gate expected at this task: suite 1244 passed, 10 skipped; smoke 49 passed, 0 failed, 0 skipped — 49 checks.

```bash
git add tests/unit/test_search_engine.py src/telegram_mcp/telegram/search.py
git commit -m "feat: add the search continuation engine with honest coverage

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 5: Authority: the get_context path and the search snapshot

Spec §20.4, §21.4, §21A.4, §23.1–§23.7; design §4.1–§4.5.

**`get_context`.** It resolves `message_ref` through `message_refs` to its canonical peer, which must pass *current* owner policy and membership (`REF_NOT_FOUND` or `NOT_ACCESSIBLE`), and its limit is `before + after + 1`.

**`SearchAuthority`.** Both searches share one snapshot:
- **Universe.** It is the canonical-sorted readable members of the project (optionally narrowed by `peer_ref`), or the de-duplicated union for cross-project search, which requires `can_read` and `can_cross_search` on every selected project.
- **Cursor.** The frozen hierarchy runs first, then the universe digest (`CURSOR_PROJECT_CHANGED`).
- **Upper anchor.** It is the earlier of `until` and now, frozen across pages.
- **Estimate.** It uses the widest grant, charged to every selected project's bucket.
- **Revalidation.** It compares every selected project's readable set.
- **Egress.** Each record takes the intersection of its own projects' grants.

`readable_members` moves into `authority/policy.py` so the seams and the search authority import one copy.

Serving `get_context` moves three pins: a snapshot test, the 4a end-to-end test and a smoke row. The last two now prove the stable property that without a session a Telegram tool refuses with `AUTH_REQUIRED` and no prompt.

**Files:**
- Modify: `tests/authority_fixtures.py`
- Create: `tests/integration/test_search_snapshot.py`
- Modify: `tests/integration/test_project_snapshot.py`
- Modify: `tests/integration/test_phase4a_end_to_end.py`
- Modify: `scripts/e2e_smoke.py`
- Modify: `src/telegram_mcp/authority/policy.py`
- Modify: `src/telegram_mcp/storage/refstore.py`
- Create: `src/telegram_mcp/disclosure/search_authority.py`
- Modify: `src/telegram_mcp/disclosure/seams.py`

**Interfaces:**
- Produces:
  - `readable_members(view, client_ref, project_ref) -> frozenset[str]` in `authority/policy.py`; `RefStore.peer_by_row(row_id) -> PeerRow | None`
  - `ProjectSnapshot.anchor_message_id: int | None`; `PROJECT_TOOLS` includes `telegram_get_context`; `SERVED_TOOLS` includes both searches
  - `SEARCH_TOOLS`, `CROSS_PEER_CAP = 250`, `SelectedProject`, `SearchSnapshot` (fields as in the file; `project_count`, `project_names`, `egress_level` = widest), `SearchAuthority`
  - `CoordinatorAuthority.mint_search_cursor(snapshot, arguments, state) -> str`
  - `seed_second_project(conn)` and `BETA_REF` in `tests/authority_fixtures.py`

- [ ] **Step 1: Write the failing tests**

Apply to `tests/authority_fixtures.py`:

```diff
--- a/tests/authority_fixtures.py
+++ b/tests/authority_fixtures.py
@@ -101,3 +101,39 @@
             )
         conn.commit()  # RefStore opens BEGIN IMMEDIATE: no implicit transaction may be open
     return out
+
+
+BETA_REF = "tpr_" + "b" * 26
+
+
+def seed_second_project(conn) -> None:
+    """Project Beta: channel:7 shared with Alpha, plus Bob. Both grants allow cross search.
+
+    Alpha is full_text; Beta is metadata_only, so a record from the shared
+    channel must come out as metadata only (spec §23B.2 intersection).
+    """
+    now = "2026-09-23T00:00:00Z"
+    conn.execute(
+        "INSERT INTO projects (account_id, project_ref, slug, display_name, enabled,"
+        " project_epoch, created_at, updated_at) VALUES (1, ?, 'beta', 'Beta', 1, 1, ?, ?)",
+        (BETA_REF, now, now),
+    )
+    conn.execute("UPDATE client_projects SET can_cross_search = 1")
+    conn.execute(
+        "INSERT INTO client_projects (client_id, project_id, can_read, can_cross_search,"
+        " egress_level, excerpt_max_codepoints, created_at, updated_at)"
+        " VALUES (1, 2, 1, 1, 'metadata_only', NULL, ?, ?)",
+        (now, now),
+    )
+    for identity in ("channel:7", "user:101"):
+        peer_type, _, raw = identity.partition(":")
+        row = conn.execute(
+            "SELECT id FROM peers WHERE telegram_peer_type = ? AND telegram_peer_id = ?",
+            (peer_type, int(raw)),
+        ).fetchone()
+        conn.execute(
+            "INSERT INTO project_peers (project_id, peer_id, membership_kind, created_at,"
+            " updated_at) VALUES (2, ?, 'shared', ?, ?)",
+            (row[0], now, now),
+        )
+    conn.commit()
```

Create `tests/integration/test_search_snapshot.py`:

```python
"""Search authority (spec §21, §21A, §23; design §4.2-§4.5)."""

import pytest

from telegram_mcp.disclosure.budget import GLOBAL, PROJECT
from telegram_mcp.disclosure.coordinator import AuthorityRefusal
from telegram_mcp.disclosure.seams import CoordinatorAuthority
from telegram_mcp.disclosure.search_authority import SearchSnapshot
from telegram_mcp.keys.store import load_key, provision_missing
from telegram_mcp.runtime.identity import resolve_principal
from telegram_mcp.storage.db import bind_cursor_store, open_db
from tests.authority_fixtures import (
    BETA_REF,
    PROJECT_REF,
    seed_authority_rows,
    seed_project_world,
    seed_second_project,
)

CLIENT = "tcl_" + "a" * 26
NOW = 1_790_000_000.0  # 2026-09-21T14:13:20Z


@pytest.fixture
def world(tmp_path):
    provision_missing(tmp_path / "keys", phases=(2, 3))
    conn = open_db(tmp_path / "meta.db")
    seed_authority_rows(conn)
    refs = seed_project_world(conn)
    seed_second_project(conn)
    authority = CoordinatorAuthority(
        conn,
        privacy_key=load_key("privacy-key"),
        cursor_key=load_key("cursor-key"),
        cursor_store=bind_cursor_store(conn),
        runtime_id=b"\x05" * 16,
        clock=lambda: NOW,
    )
    principal = resolve_principal(conn, CLIENT)

    def snap(tool, **args):
        args.setdefault("limit", 20)
        request = authority.freeze_arguments(tool, args, principal=principal)
        return request.validated_args, authority.snapshot(tool, request)

    return conn, authority, snap, refs, tmp_path


def _bump(conn, sql):
    conn.execute(sql)
    conn.commit()


def test_an_ordinary_search_universe_is_the_project_in_canonical_order(world):
    _conn, authority, snap, _refs, _tmp = world
    _args, s = snap("telegram_search_messages", project_ref=PROJECT_REF, query="needle")
    assert isinstance(s, SearchSnapshot)
    assert s.universe == ("channel:7", "chat:9", "user:100") and s.peer_cap is None
    assert s.universe_digest.startswith("hmac-sha256:") and s.project_count == 1
    kinds = {key.kind for key in authority.worst_case_buckets("telegram_search_messages", s)}
    assert kinds == {GLOBAL, PROJECT}


def test_peer_ref_narrows_to_one_member(world):
    _conn, _authority, snap, refs, _tmp = world
    _args, s = snap(
        "telegram_search_messages", project_ref=PROJECT_REF, query="x", peer_ref=refs["chat:9"]
    )
    assert s.universe == ("chat:9",)
    with pytest.raises(AuthorityRefusal) as exc:
        snap(
            "telegram_search_messages",
            project_ref=PROJECT_REF,
            query="x",
            peer_ref=refs["user:101"],
        )
    assert exc.value.code == "NOT_ACCESSIBLE"
    with pytest.raises(AuthorityRefusal) as exc:
        snap(
            "telegram_search_messages",
            project_ref=PROJECT_REF,
            query="x",
            peer_ref="tgp_" + "z" * 26,
        )
    assert exc.value.code == "REF_NOT_FOUND"


def test_cross_search_unions_and_dedupes_and_charges_every_project(world):
    _conn, authority, snap, _refs, _tmp = world
    _args, s = snap(
        "telegram_cross_project_search", project_refs=[BETA_REF, PROJECT_REF], query="x"
    )
    assert s.universe == ("channel:7", "chat:9", "user:100", "user:101")  # shared channel once
    assert [p.project_ref for p in s.projects] == sorted([BETA_REF, PROJECT_REF])
    assert (
        s.peer_cap == 250 and s.project_names == ("Alpha", "Beta") and s.egress_level == "full_text"
    )
    buckets = authority.worst_case_buckets("telegram_cross_project_search", s)
    assert sum(1 for key in buckets if key.kind == PROJECT) == 2


def test_cross_search_needs_cross_permission_on_every_project(world):
    conn, _authority, snap, _refs, _tmp = world
    _bump(conn, "UPDATE client_projects SET can_cross_search = 0 WHERE project_id = 2")
    with pytest.raises(AuthorityRefusal) as exc:
        snap("telegram_cross_project_search", project_refs=[PROJECT_REF, BETA_REF], query="x")
    assert exc.value.code == "NOT_ACCESSIBLE"


def test_each_record_takes_the_most_restrictive_grant_of_its_own_projects(world):
    _conn, authority, snap, _refs, _tmp = world
    _args, s = snap(
        "telegram_cross_project_search", project_refs=[PROJECT_REF, BETA_REF], query="x"
    )
    raw = {
        "results": [
            {
                "origin_project_refs": [PROJECT_REF, BETA_REF],
                "text": "shared",
                "text_truncated": False,
            },
            {"origin_project_refs": [PROJECT_REF], "text": "alpha only", "text_truncated": False},
        ]
    }
    out = authority.apply_egress(raw, s)
    assert [r["text"] for r in out["results"]] == [None, "alpha only"]


def _continue(snap, args, cursor):
    return snap("telegram_search_messages", **{**args, "cursor": cursor})


def _first(world):
    _conn, authority, snap, _refs, _tmp = world
    args = {"project_ref": PROJECT_REF, "query": "needle"}
    validated, s = snap("telegram_search_messages", **args)
    state = {
        "upper_date": s.upper_date,
        "universe_digest": s.universe_digest,
        "window_start": 0,
        "next_unstarted_index": 1,
    }
    return args, authority.mint_search_cursor(s, validated, state), s


def test_a_cursor_resumes_with_its_state_and_frozen_anchor(world):
    args, cursor, first = _first(world)
    _validated, again = _continue(world[2], args, cursor)
    assert again.state["next_unstarted_index"] == 1 and again.upper_date == first.upper_date


@pytest.mark.parametrize(
    "mutation,code",
    [
        ("UPDATE policy_state SET policy_epoch = policy_epoch + 1", "CURSOR_POLICY_CHANGED"),
        (
            "UPDATE projects SET project_epoch = project_epoch + 1 WHERE id = 1",
            "CURSOR_PROJECT_CHANGED",
        ),
        (
            "UPDATE client_projects SET egress_level = 'metadata_only' WHERE project_id = 1",
            "CURSOR_PROJECT_CHANGED",
        ),
        # membership moved with no epoch-visible cause: the universe digest catches it
        (
            "DELETE FROM project_peers WHERE project_id = 1 AND peer_id = (SELECT id FROM peers WHERE telegram_peer_id = 9)",
            "CURSOR_PROJECT_CHANGED",
        ),
    ],
)
def test_the_cursor_hierarchy_then_the_universe_digest(world, mutation, code):
    conn = world[0]
    args, cursor, _first_snap = _first(world)
    _bump(conn, mutation)
    with pytest.raises(AuthorityRefusal) as exc:
        _continue(world[2], args, cursor)
    assert exc.value.code == code


def test_a_shared_peer_removed_mid_flight_is_discarded(world):
    """Design §4.5 race test, at the authority seam: revalidation refuses."""
    conn, authority, snap, _refs, _tmp = world
    _args, s = snap(
        "telegram_cross_project_search", project_refs=[PROJECT_REF, BETA_REF], query="x"
    )
    _bump(
        conn,
        "DELETE FROM project_peers WHERE project_id = 2 AND peer_id = (SELECT id FROM peers WHERE telegram_peer_id = 7)",
    )
    assert authority.revalidate(s) == "POLICY_CHANGED"


def test_the_upper_anchor_is_the_earlier_of_until_and_now(world):
    _conn, _authority, snap, _refs, _tmp = world
    _a, future = snap(
        "telegram_search_messages", project_ref=PROJECT_REF, query="x", until="2030-01-01T00:00:00Z"
    )
    _b, past = snap(
        "telegram_search_messages",
        project_ref=PROJECT_REF,
        query="x",
        until="2026-01-01T00:00:00+01:00",
    )
    assert future.upper_date == "2026-09-21T14:13:20Z"
    assert past.upper_date == "2025-12-31T23:00:00Z"


def test_query_text_never_reaches_the_database(world):
    conn, _authority, _snap, _refs, tmp = world
    _args, _cursor, _s = _first(world)
    conn.commit()
    for path in tmp.glob("meta.db*"):
        assert b"needle" not in path.read_bytes()
```

Apply to `tests/integration/test_project_snapshot.py`:

```diff
--- a/tests/integration/test_project_snapshot.py
+++ b/tests/integration/test_project_snapshot.py
@@ -115,8 +115,8 @@
     assert exc.value.code == "CURSOR_POLICY_CHANGED"
 
 
-def test_4c_tools_still_refuse_before_any_prompt(world):
+def test_a_tool_the_coordinator_does_not_serve_refuses_before_any_prompt(world):
     _conn, authority, principal, _refs = world
     with pytest.raises(AuthorityRefusal) as exc:
-        authority.freeze_arguments("telegram_get_context", {}, principal=principal)
+        authority.freeze_arguments("telegram_status", {}, principal=principal)
     assert exc.value.code == "POLICY_UNCONFIGURED"
```

Apply to `tests/integration/test_phase4a_end_to_end.py`:

```diff
--- a/tests/integration/test_phase4a_end_to_end.py
+++ b/tests/integration/test_phase4a_end_to_end.py
@@ -311,14 +311,14 @@
         raise AssertionError("retrieval ran after a cancelled consent")
 
 
-async def test_the_4c_tools_still_refuse_honestly(world):
+async def test_without_a_session_telegram_tools_refuse_before_any_prompt(world):
     body = await call(
         world,
         CODEX,
         "telegram_get_context",
         {"project_ref": world["ops"], "message_ref": "tgm_" + "a" * 26},
     )
-    assert body["error"]["code"] == "POLICY_UNCONFIGURED"
+    assert body["error"]["code"] == "AUTH_REQUIRED"  # this world has no Telegram session
     assert world["agent"].prompts == 0
 
 
```

Apply to `scripts/e2e_smoke.py`:

```diff
--- a/scripts/e2e_smoke.py
+++ b/scripts/e2e_smoke.py
@@ -1358,8 +1358,8 @@
 
     def others_refuse():
         out = drive()
-        assert out["other"]["error"]["code"] == "POLICY_UNCONFIGURED", out["other"]
-        return "telegram_get_context -> POLICY_UNCONFIGURED"
+        assert out["other"]["error"]["code"] == "AUTH_REQUIRED", out["other"]
+        return "telegram_get_context without a session -> AUTH_REQUIRED, no prompt"
 
     def demo_still_refuses():
         from mcp.types import CLIENT_CAPABILITIES_META_KEY, PROTOCOL_VERSION_META_KEY
@@ -1390,7 +1390,7 @@
 
     ledger.run(area, "catalogue success through ingress + real agent", real_success)
     ledger.run(area, "bad bearers are 401, byte-identical", bad_bearers)
-    ledger.run(area, "4c tools still refuse", others_refuse)
+    ledger.run(area, "Telegram tools refuse without a session", others_refuse)
     ledger.run(area, "demo server still has no sensitive route", demo_still_refuses)
 
 
```

- [ ] **Step 2: Run them and watch them fail**

Run: `uv run pytest tests/integration/test_search_snapshot.py tests/integration/test_project_snapshot.py tests/integration/test_phase4a_end_to_end.py -q`
Expected: `ImportError: cannot import name 'SearchSnapshot'` (collection of `test_search_snapshot.py`); the moved pins fail until the seams serve `get_context`.
Observed in the staged proof: 1 error in 8.16s; first error: `ModuleNotFoundError: No module named 'telegram_mcp.disclosure.search_authority'`.

- [ ] **Step 3: Implement**

Apply to `src/telegram_mcp/authority/policy.py`:

```diff
--- a/src/telegram_mcp/authority/policy.py
+++ b/src/telegram_mcp/authority/policy.py
@@ -27,6 +27,7 @@
     "check_pre_serialize",
     "evaluate",
     "make_view",
+    "readable_members",
 ]
 
 OPERATIONS = ("read", "cross_search", "discover")
@@ -259,3 +260,17 @@
         raise AuthorityChanged(POLICY_CHANGED, "grant changed")
     if snapshot.effective_egress != fresh.effective_egress:
         raise AuthorityChanged(POLICY_CHANGED, "effective egress changed")
+
+
+def readable_members(view: AuthorityView, client_ref: str, project_ref: str) -> frozenset[str]:
+    """A project's members that pass every layer for ``read``: owner mode, deny, membership."""
+    return frozenset(
+        identity
+        for identity in view.memberships.get(project_ref, frozenset())
+        if not isinstance(
+            evaluate(
+                view, AuthorityRequest("read", client_ref, (project_ref,), peer_identity=identity)
+            ),
+            Denial,
+        )
+    )
```

Apply to `src/telegram_mcp/storage/refstore.py`:

```diff
--- a/src/telegram_mcp/storage/refstore.py
+++ b/src/telegram_mcp/storage/refstore.py
@@ -94,6 +94,14 @@
             ).fetchone()
         )
 
+    def peer_by_row(self, row_id: int) -> PeerRow | None:
+        return _row(
+            self._conn.execute(
+                f"SELECT {_COLUMNS} FROM peers WHERE account_id = ? AND id = ?",
+                (self._account, row_id),
+            ).fetchone()
+        )
+
     def peer_by_identity(self, identity: str) -> PeerRow | None:
         peer_type, _, raw = identity.partition(":")
         if peer_type not in PEER_TYPES or not raw.lstrip("-").isdigit():
```

Create `src/telegram_mcp/disclosure/search_authority.py`:

```python
"""Authority for the two search tools (spec §21, §21A, §23; design §4.2-§4.5).

Both searches share one snapshot shape. The universe is the canonical-sorted
set of owner-authorised member peers: of the one selected project, or the
de-duplicated union of 2-8 selected projects for cross-project search. The
cursor binds it by digest, never by list. Owner chat-kind switches need
dialog data, so the reads enforce them at retrieval; a peer they exclude is
never searched.
"""

from __future__ import annotations

import sqlite3
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from telegram_mcp.authority.cursors import (
    CursorError,
    CursorStore,
    ProjectScopeEntry,
    check_cursor,
    mint_cursor,
    project_scope_digest,
    scope_entries_from_view,
)
from telegram_mcp.authority.policy import AuthorityRequest, Denial, evaluate, readable_members
from telegram_mcp.disclosure.bounds import worst_case
from telegram_mcp.disclosure.budget import GLOBAL, PROJECT, BucketKey, Usage, subject_digest
from telegram_mcp.disclosure.coordinator import AuthorityRefusal
from telegram_mcp.disclosure.egress import intersect_profiles, transform_record
from telegram_mcp.runtime.identity import PrincipalContext
from telegram_mcp.storage.authority_view import (
    load_security,
    load_view,
    project_labels,
)
from telegram_mcp.storage.refstore import RefStore
from telegram_mcp.telegram.search import universe_digest

__all__ = ["CROSS_PEER_CAP", "SEARCH_TOOLS", "SearchAuthority", "SearchSnapshot", "SelectedProject"]

SEARCH_TOOLS = frozenset({"telegram_search_messages", "telegram_cross_project_search"})
CROSS_PEER_CAP = 250  # design D9: max_cross_project_peers, cross-project only
_RANK = {"metadata_only": 0, "excerpt": 1, "full_text": 2}


@dataclass(frozen=True)
class SelectedProject:
    project_ref: str
    display_name: str
    egress_level: str
    excerpt_limit: int | None
    readable: frozenset[str]


@dataclass(frozen=True)
class SearchSnapshot:
    principal_id: int
    client_id: int
    account_id: int
    principal_ref: str
    client_ref: str
    account_ref: str
    client_kind: str
    security_epoch: int
    policy_epoch: int
    scope_hex: str
    scope_entries: tuple[ProjectScopeEntry, ...]
    tool_name: str
    projects: tuple[SelectedProject, ...]
    owner_scope: Any  # seams.OwnerScope
    universe: tuple[str, ...]
    peer_cap: int | None
    limit: int
    query: str  # memory only: never persisted, logged or put in a cursor
    since: str | None
    upper_date: str
    universe_digest: str
    state: Mapping[str, Any] = field(default_factory=dict)
    peer_ref: str | None = None
    peer_name: str | None = None
    partial: bool = False

    @property
    def project_count(self) -> int:
        return len(self.projects)

    @property
    def project_scope_digest(self) -> str:
        return "hmac-sha256:" + self.scope_hex

    @property
    def project_names(self) -> tuple[str, ...]:
        return tuple(p.display_name for p in self.projects)

    @property
    def project_ref(self) -> str | None:
        return self.projects[0].project_ref if len(self.projects) == 1 else None

    @property
    def egress_level(self) -> str:
        """The widest selected grant: what the prompt must warn about."""
        return max((p.egress_level for p in self.projects), key=lambda level: _RANK[level])

    @property
    def excerpt_limit(self) -> int | None:
        limits = [
            p.excerpt_limit
            for p in self.projects
            if p.egress_level == "excerpt" and p.excerpt_limit is not None
        ]
        return max(limits) if limits else None


def _canonical(identity: str) -> tuple[str, int]:
    peer_type, _, raw = identity.partition(":")
    return peer_type, int(raw)


def _iso(moment: datetime) -> str:
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


class SearchAuthority:
    def __init__(
        self,
        conn: sqlite3.Connection,
        *,
        privacy_key: bytes,
        cursor_key: bytes,
        cursor_store: CursorStore,
        runtime_id: bytes,
        clock: Callable[[], float] = time.time,
        telegram_gate: Callable[[], str | None] = lambda: None,
        presenter: Callable[..., Any],
        owner_scope: Callable[[int, int], Any],
    ) -> None:
        self._conn = conn
        self._privacy_key = privacy_key
        self._cursor_key = cursor_key
        self._cursors = cursor_store
        self._runtime_id = runtime_id
        self._clock = clock
        self._gate = telegram_gate
        self._presenter = presenter
        self._owner_scope = owner_scope

    # -- step 2 -------------------------------------------------------------

    def _selected(
        self, view: Any, client_ref: str, refs: list[str], cross: bool, labels: Any
    ) -> tuple[SelectedProject, ...]:
        verdict = evaluate(view, AuthorityRequest("discover", client_ref, tuple(refs)))
        if isinstance(verdict, Denial):
            raise AuthorityRefusal(verdict.code)
        out = []
        for ref in sorted(refs):
            grant = view.grants[(client_ref, ref)]
            if not grant.can_read or (cross and not grant.can_cross_search):
                raise AuthorityRefusal("NOT_ACCESSIBLE")
            out.append(
                SelectedProject(
                    project_ref=ref,
                    display_name=labels[ref][1],
                    egress_level=grant.egress_level,
                    excerpt_limit=grant.excerpt_limit,
                    readable=readable_members(view, client_ref, ref),
                )
            )
        return tuple(out)

    def snapshot(self, tool_name: str, request: Any) -> SearchSnapshot:
        principal = request.principal
        if principal.account_id is None or principal.account_ref is None:
            raise AuthorityRefusal("POLICY_UNCONFIGURED")
        gated = self._gate()
        if gated is not None:
            raise AuthorityRefusal(gated)  # before consent
        security_epoch, locked = load_security(self._conn)
        if locked:
            raise AuthorityRefusal("SECURITY_LOCKED")
        view = load_view(
            self._conn, principal_id=principal.principal_id, account_id=principal.account_id
        )
        args = request.validated_args
        cross = tool_name == "telegram_cross_project_search"
        refs = list(args["project_refs"]) if cross else [args["project_ref"]]
        labels = project_labels(self._conn, account_id=principal.account_id)
        projects = self._selected(view, principal.client_ref, refs, cross, labels)
        union = frozenset().union(*(p.readable for p in projects))
        peer_ref = peer_name = None
        if not cross and args.get("peer_ref") is not None:
            row = RefStore(self._conn, account_id=principal.account_id).peer_by_ref(
                args["peer_ref"]
            )
            if row is None:
                raise AuthorityRefusal("REF_NOT_FOUND")
            if row.identity not in union:
                raise AuthorityRefusal("NOT_ACCESSIBLE")
            union = frozenset({row.identity})
            peer_ref, peer_name = row.peer_ref, row.display_name
        universe = tuple(sorted(union, key=_canonical))
        digest = universe_digest(self._privacy_key, universe)
        entries = scope_entries_from_view(
            view, principal.client_ref, [p.project_ref for p in projects]
        )
        state: dict[str, Any] = {}
        cursor = args.get("cursor")
        if cursor is not None:
            presenter = self._presenter(
                principal,
                tool_name,
                args,
                view.policy_epoch,
                security_epoch,
                entries,
                variant="selected",
            )
            try:
                record = check_cursor(
                    self._cursors,
                    cursor_key=self._cursor_key,
                    privacy_key=self._privacy_key,
                    ref=cursor,
                    presenter=presenter,
                    now=self._clock(),
                    runtime_id=self._runtime_id,
                )
            except CursorError as exc:
                raise AuthorityRefusal(exc.code) from None
            state = dict(record.state)
            # Design §4.3: after the frozen hierarchy, the universe must be the
            # one the walk started over, or an index would name another peer.
            if state.get("universe_digest") != digest:
                raise AuthorityRefusal("CURSOR_PROJECT_CHANGED")
        now = datetime.fromtimestamp(self._clock(), UTC)
        until = args.get("until")
        upper = state.get("upper_date") or _iso(
            min(now, datetime.fromisoformat(until)) if until else now
        )
        return SearchSnapshot(
            principal_id=principal.principal_id,
            client_id=principal.client_id,
            account_id=principal.account_id,
            principal_ref=principal.principal_ref,
            client_ref=principal.client_ref,
            account_ref=principal.account_ref,
            client_kind=principal.client_kind,
            security_epoch=security_epoch,
            policy_epoch=view.policy_epoch,
            scope_hex=project_scope_digest(self._privacy_key, entries, variant="selected"),
            scope_entries=entries,
            tool_name=tool_name,
            projects=projects,
            owner_scope=self._owner_scope(principal.principal_id, principal.account_id),
            universe=universe,
            peer_cap=CROSS_PEER_CAP if cross else None,
            limit=int(args["limit"]),
            query=args["query"],
            since=args.get("since"),
            upper_date=upper,
            universe_digest=digest,
            state=state,
            peer_ref=peer_ref,
            peer_name=peer_name,
        )

    def mint_cursor(
        self, snapshot: SearchSnapshot, arguments: Mapping[str, Any], state: Mapping[str, Any]
    ) -> str:
        principal = PrincipalContext(
            principal_id=snapshot.principal_id,
            principal_ref=snapshot.principal_ref,
            client_id=snapshot.client_id,
            client_ref=snapshot.client_ref,
            client_kind=snapshot.client_kind,
            account_id=snapshot.account_id,
            account_ref=snapshot.account_ref,
        )
        presenter = self._presenter(
            principal,
            snapshot.tool_name,
            arguments,
            snapshot.policy_epoch,
            snapshot.security_epoch,
            snapshot.scope_entries,
            variant="selected",
        )
        return mint_cursor(
            self._cursors,
            cursor_key=self._cursor_key,
            privacy_key=self._privacy_key,
            presenter=presenter,
            state=state,
            now=self._clock(),
            runtime_id=self._runtime_id,
        )

    # -- step 3 -------------------------------------------------------------

    def worst_case_buckets(self, snapshot: SearchSnapshot) -> dict[BucketKey, Usage]:
        cross = snapshot.tool_name == "telegram_cross_project_search"
        first = snapshot.projects[0]
        records, total, project_bytes = worst_case(
            snapshot.tool_name,
            limit=snapshot.limit,
            project_ref=first.project_ref,
            project_display_name=first.display_name,
            egress_level=snapshot.egress_level,
            excerpt_limit=snapshot.excerpt_limit,
            projects=[(p.project_ref, p.display_name) for p in snapshot.projects] if cross else (),
        )
        buckets = {
            BucketKey(snapshot.client_id, GLOBAL, subject_digest(GLOBAL)): Usage(records, total)
        }
        for project in snapshot.projects:
            # §23C.1: a cross record counts whole in every contributing project.
            key = BucketKey(
                snapshot.client_id, PROJECT, subject_digest(PROJECT, project.project_ref)
            )
            buckets[key] = Usage(records, project_bytes)
        return buckets

    # -- step 8 -------------------------------------------------------------

    def revalidate(self, snapshot: SearchSnapshot) -> str | None:
        security_epoch, locked = load_security(self._conn)
        if locked or security_epoch != snapshot.security_epoch:
            return "SECURITY_LOCKED"
        view = load_view(
            self._conn, principal_id=snapshot.principal_id, account_id=snapshot.account_id
        )
        refs = [p.project_ref for p in snapshot.projects]
        verdict = evaluate(view, AuthorityRequest("discover", snapshot.client_ref, tuple(refs)))
        if isinstance(verdict, Denial):
            return "CLIENT_REVOKED" if verdict.code == "CLIENT_REVOKED" else "NOT_ACCESSIBLE"
        if view.policy_epoch != snapshot.policy_epoch:
            return "POLICY_CHANGED"
        entries = scope_entries_from_view(view, snapshot.client_ref, refs)
        if (
            project_scope_digest(self._privacy_key, entries, variant="selected")
            != snapshot.scope_hex
        ):
            return "POLICY_CHANGED"
        for project in snapshot.projects:
            # §23.7: membership of every peer the result may represent.
            if readable_members(view, snapshot.client_ref, project.project_ref) != project.readable:
                return "POLICY_CHANGED"
        return None

    # -- step 9 -------------------------------------------------------------

    def apply_egress(self, raw: Mapping[str, Any], snapshot: SearchSnapshot) -> dict[str, Any]:
        """§23B.2: each record takes the most restrictive grant among its own projects."""
        grants = {p.project_ref: (p.egress_level, p.excerpt_limit) for p in snapshot.projects}
        out = dict(raw)
        results = []
        for record in out.get("results", []):
            level, limit = intersect_profiles(
                [grants[ref] for ref in record["origin_project_refs"]]
            )
            results.append(transform_record(record, level, limit))
        out["results"] = results
        return out
```

Apply to `src/telegram_mcp/disclosure/seams.py`:

```diff
--- a/src/telegram_mcp/disclosure/seams.py
+++ b/src/telegram_mcp/disclosure/seams.py
@@ -34,7 +34,7 @@
     project_scope_digest,
     scope_entries_from_view,
 )
-from telegram_mcp.authority.policy import AuthorityRequest, Denial, evaluate
+from telegram_mcp.authority.policy import AuthorityRequest, Denial, evaluate, readable_members
 from telegram_mcp.consent.broker import ConsentBroker, ConsentError, ConsumedChallenge
 from telegram_mcp.consent.challenge import display_digest, jcs_dumps
 from telegram_mcp.consent.display import build_display
@@ -44,6 +44,7 @@
 from telegram_mcp.disclosure.coordinator import AuthorityRefusal, ConsentRefusal
 from telegram_mcp.disclosure.egress import transform_record
 from telegram_mcp.disclosure.exposure import exposure_digest
+from telegram_mcp.disclosure.search_authority import SEARCH_TOOLS, SearchAuthority, SearchSnapshot
 from telegram_mcp.runtime.identity import PrincipalContext
 from telegram_mcp.storage.authority_view import (
     load_owner_scope,
@@ -74,11 +75,14 @@
         "telegram_resolve_peer",
         "telegram_get_messages",
         "telegram_get_unread",
+        "telegram_get_context",
     }
 )
-SERVED_TOOLS = CATALOGUE_TOOLS | PROJECT_TOOLS
+SERVED_TOOLS = CATALOGUE_TOOLS | PROJECT_TOOLS | SEARCH_TOOLS
 # Design §3.8: only these two contracts let a record carry origin_project_refs.
-_PROJECT_BUCKET_TOOLS = frozenset({"telegram_list_chats", "telegram_get_messages"})
+_PROJECT_BUCKET_TOOLS = frozenset(
+    {"telegram_list_chats", "telegram_get_messages", "telegram_get_context"}
+)
 _REQUEST_DOMAIN = b"telegram-mcp-request/v1\0"
 # The longest match_kind value in E.12, used by the catalogue upper bound.
 _LONGEST_MATCH_KIND = "substring_display_name"
@@ -186,6 +190,7 @@
     peer_ref: str | None = None
     peer_identity: str | None = None
     peer_name: str | None = None
+    anchor_message_id: int | None = None  # get_context only; never leaves the daemon
     state: Mapping[str, Any] = field(default_factory=dict)
     project_count: int = 1
     partial: bool = False
@@ -220,6 +225,19 @@
         self._cursors = cursor_store
         self._runtime_id = runtime_id
         self._clock = clock
+        self._search = SearchAuthority(
+            conn,
+            privacy_key=privacy_key,
+            cursor_key=cursor_key,
+            cursor_store=cursor_store,
+            runtime_id=runtime_id,
+            clock=clock,
+            telegram_gate=lambda: self._telegram_gate(),
+            presenter=self._presenter,
+            owner_scope=lambda principal_id, account_id: OwnerScope(
+                *load_owner_scope(conn, principal_id=principal_id, account_id=account_id)
+            ),
+        )
 
     # -- step 1 -------------------------------------------------------------
 
@@ -276,6 +294,8 @@
     def snapshot(
         self, tool_name: str, request: FrozenRequest
     ) -> CatalogueSnapshot | ProjectSnapshot:
+        if tool_name in SEARCH_TOOLS:
+            return self._search.snapshot(tool_name, request)  # type: ignore[return-value]
         if tool_name in PROJECT_TOOLS:
             return self._project_snapshot(tool_name, request)
         principal = request.principal
@@ -384,18 +404,7 @@
 
     @staticmethod
     def _readable(view: Any, client_ref: str, project_ref: str) -> frozenset[str]:
-        """Members that pass every layer for ``read``: owner mode, deny, membership."""
-        return frozenset(
-            identity
-            for identity in view.memberships.get(project_ref, frozenset())
-            if not isinstance(
-                evaluate(
-                    view,
-                    AuthorityRequest("read", client_ref, (project_ref,), peer_identity=identity),
-                ),
-                Denial,
-            )
-        )
+        return readable_members(view, client_ref, project_ref)
 
     def _project_snapshot(self, tool_name: str, request: FrozenRequest) -> ProjectSnapshot:
         principal = request.principal
@@ -431,6 +440,20 @@
                 raise AuthorityRefusal("NOT_ACCESSIBLE")
             peer_ref, peer_identity = row.peer_ref, row.identity
             peer_name = clamp(row.display_name, NAME_MAX)[0]
+        anchor_message_id = None
+        if tool_name == "telegram_get_context":
+            # §20.4: a historical ref resolves to its canonical peer, which must
+            # pass *current* owner policy and membership; the ref grants nothing.
+            refs = RefStore(self._conn, account_id=principal.account_id)
+            located = refs.message_by_ref(args["message_ref"])
+            row = refs.peer_by_row(located[0]) if located is not None else None
+            if located is None or row is None:
+                raise AuthorityRefusal("REF_NOT_FOUND")
+            if row.identity not in readable:
+                raise AuthorityRefusal("NOT_ACCESSIBLE")
+            peer_ref, peer_identity = row.peer_ref, row.identity
+            peer_name = clamp(row.display_name, NAME_MAX)[0]
+            anchor_message_id = located[1]
         state: dict[str, Any] = {}
         cursor = args.get("cursor")
         if cursor is not None:
@@ -481,14 +504,24 @@
                     account_id=principal.account_id,
                 )
             ),
-            limit=int(args["limit"]),
+            limit=(
+                int(args["before"]) + int(args["after"]) + 1
+                if tool_name == "telegram_get_context"
+                else int(args["limit"])
+            ),
             tool_name=tool_name,
             peer_ref=peer_ref,
             peer_identity=peer_identity,
             peer_name=peer_name,
+            anchor_message_id=anchor_message_id,
             state=state,
         )
 
+    def mint_search_cursor(
+        self, snapshot: SearchSnapshot, arguments: Mapping[str, Any], state: Mapping[str, Any]
+    ) -> str:
+        return self._search.mint_cursor(snapshot, arguments, state)
+
     def mint_project_cursor(
         self, snapshot: ProjectSnapshot, arguments: Mapping[str, Any], state: Mapping[str, Any]
     ) -> str:
@@ -554,6 +587,8 @@
         longer than its counterpart here, and the longest ``match_kind`` is
         assumed for each. So the bound holds for both tools, for any page.
         """
+        if isinstance(snapshot, SearchSnapshot):
+            return self._search.worst_case_buckets(snapshot)
         if isinstance(snapshot, ProjectSnapshot):
             records, total, project_bytes = worst_case(
                 tool_name,
@@ -584,6 +619,8 @@
     # -- step 8 -------------------------------------------------------------
 
     def revalidate(self, snapshot: CatalogueSnapshot | ProjectSnapshot) -> str | None:
+        if isinstance(snapshot, SearchSnapshot):
+            return self._search.revalidate(snapshot)
         if isinstance(snapshot, ProjectSnapshot):
             return self._revalidate_project(snapshot)
         security_epoch, locked = load_security(self._conn)
@@ -608,6 +645,8 @@
     # -- step 9 -------------------------------------------------------------
 
     def apply_egress(self, raw: Mapping[str, Any], snapshot: Any) -> dict[str, Any]:
+        if isinstance(snapshot, SearchSnapshot):
+            return self._search.apply_egress(raw, snapshot)
         out = dict(raw)
         if isinstance(snapshot, ProjectSnapshot) and "messages" in out:
             out["messages"] = [
```

- [ ] **Step 4: Run them and watch them pass**

Run: `uv run pytest tests/integration/test_search_snapshot.py tests/integration/test_project_snapshot.py tests/integration/test_phase4a_end_to_end.py -q`
Expected: 30 passed (observed in the staged proof).

- [ ] **Step 5: Commit** (format, check, full gate first)

Full gate expected at this task: suite 1257 passed, 10 skipped; smoke 49 passed, 0 failed, 0 skipped — 49 checks.

```bash
git add tests/authority_fixtures.py tests/integration/test_search_snapshot.py tests/integration/test_project_snapshot.py tests/integration/test_phase4a_end_to_end.py scripts/e2e_smoke.py src/telegram_mcp/authority/policy.py src/telegram_mcp/storage/refstore.py src/telegram_mcp/disclosure/search_authority.py src/telegram_mcp/disclosure/seams.py
git commit -m "feat: authorise get_context by ref and snapshot both searches

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 6: Reads: get_context and both searches

Spec §13.5, §20, §21, §21A; design §4.1–§4.5 and §4.7.

**`get_context`:**
- It fetches the anchor by id (`MESSAGE_NOT_FOUND` if it is deleted) and classifies its topic per §4.1.
- **Window.** Each side is read in its own request, bounded by `min_id`/`max_id` and sorted nearest-first. The result is exact under either `add_offset` edge semantics, and the tests serve both.
- **Topics.** A named topic reads through `GetReplies` and never through history. General pages outward, drops named-topic messages, and is partial only when a budget fires.
- **Page cap.** The cap keeps the anchor first.

**The searches:**
- They drive the engine.
- **Owner switches.** A batched dialog check before each peer is first searched applies the owner's chat-kind switches. Excluded peers are never searched; unreachable ones make the result `telegram_partial`.
- **Ordering.** Records are sorted newest-first with `message_ref` as the tie-break.
- **Bytes.** Pages are byte-accounted through `accept()`.
- **Sidecars.** The `_coverage`, `_partial` and `_next_cursor` sidecars are set honestly.
- **Fault injection.** It proves all six `partial_reasons`.

The 4b reads-world fixture body becomes the plain helper `make_reads_world`, so other test modules can reuse it lint-clean.

**Files:**
- Modify: `tests/integration/test_telegram_reads.py`
- Create: `tests/integration/test_get_context.py`
- Create: `tests/integration/test_search_reads.py`
- Create: `tests/integration/test_exposure_bound_invariant_4c.py`
- Modify: `src/telegram_mcp/telegram/reads.py`

**Interfaces:**
- Produces:
  - `TelegramReads(..., mint_search_cursor=None)`; `get_context`, `search_messages` and `cross_project_search`, each `(arguments, snapshot)`
  - `make_reads_world(tmp_path)` in `tests/integration/test_telegram_reads.py`

- [ ] **Step 1: Write the failing tests**

Apply to `tests/integration/test_telegram_reads.py`:

```diff
--- a/tests/integration/test_telegram_reads.py
+++ b/tests/integration/test_telegram_reads.py
@@ -66,8 +66,8 @@
 ]
 
 
-@pytest.fixture
-async def world(tmp_path):
+async def make_reads_world(tmp_path):
+    """Shared by the 4b and 4c read tests (a plain helper, so importing it is lint-clean)."""
     provision_missing(tmp_path / "keys", phases=(2, 3))
     conn = open_db(tmp_path / "meta.db")
     seed_authority_rows(conn)
@@ -99,6 +99,11 @@
     return conn, fake, reads, snap, refs
 
 
+@pytest.fixture
+async def world(tmp_path):
+    return await make_reads_world(tmp_path)
+
+
 async def test_list_chats_is_the_project_newest_first_without_archived(world):
     _conn, fake, reads, snap, refs = world
     args, s = snap("telegram_list_chats", limit=20, chat_type="any", archived="exclude")
```

Create `tests/integration/test_get_context.py`:

```python
"""get_context (spec §20, design §4.1): windows, topics, General, refs, caps."""

import pytest
from telethon import errors
from telethon.tl import types

from telegram_mcp.disclosure.coordinator import AuthorityRefusal, RetrievalRefusal
from telegram_mcp.storage.refstore import RefStore
from tests.integration.test_telegram_reads import ALI, WHEN, _peer_dialogs, make_reads_world


@pytest.fixture
async def world(tmp_path):
    return await make_reads_world(tmp_path)


FORUM = types.Channel(
    id=7,
    title="Forum",
    photo=types.ChatPhotoEmpty(),
    date=WHEN,
    megagroup=True,
    forum=True,
    access_hash=3,
)


def _msg(i, peer, *, topic=None, root=False, text=None):
    if root:
        return types.MessageService(
            id=i,
            peer_id=peer,
            date=WHEN,
            action=types.MessageActionTopicCreate(title="T", icon_color=0),
        )
    reply = types.MessageReplyHeader(forum_topic=True, reply_to_msg_id=topic) if topic else None
    return types.Message(id=i, peer_id=peer, date=WHEN, message=text or f"m{i}", reply_to=reply)


def _server(messages, *, anchor_inclusive, users=(ALI,), chats=()):
    """A Telegram-like history server. ``anchor_inclusive`` flips the one edge
    Telegram leaves undocumented: whether a negative add_offset window
    includes ``offset_id`` itself."""
    ordered = sorted(messages, key=lambda m: -m.id)
    calls = []

    def history(request):
        calls.append(request)
        ids = [m.id for m in ordered]
        start = (
            next((k for k, i in enumerate(ids) if i < request.offset_id), len(ids))
            if request.offset_id
            else 0
        )
        if request.add_offset < 0 and anchor_inclusive and request.offset_id in ids:
            start = ids.index(request.offset_id)
        start = max(0, start + request.add_offset)
        window = ordered[start : start + request.limit]
        window = [
            m
            for m in window
            if (not request.min_id or m.id > request.min_id)
            and (not request.max_id or m.id < request.max_id)
        ]
        return types.messages.Messages(
            messages=window, topics=[], chats=list(chats), users=list(users)
        )

    return history, calls


def _by_ids(messages, chats=(), users=(ALI,)):
    index = {m.id: m for m in messages}

    def lookup(request):
        found = [
            index.get(i.id, types.MessageEmpty(id=i.id, peer_id=messages[0].peer_id))
            for i in request.id
        ]
        return types.messages.Messages(
            messages=found, topics=[], chats=list(chats), users=list(users)
        )

    return lookup


def _anchor_ref(conn, identity, message_id):
    refs = RefStore(conn, account_id=1)
    return refs.message_ref(refs.peer_by_identity(identity).row_id, message_id)


@pytest.mark.parametrize("anchor_inclusive", [True, False])
async def test_an_ordinary_window_is_exact_under_either_edge_semantics(world, anchor_inclusive):
    conn, fake, reads, snap, _refs = world
    chat = [_msg(i, types.PeerUser(100)) for i in range(40, 61)]
    history, calls = _server(chat, anchor_inclusive=anchor_inclusive)
    fake.script.update(
        {"messages.GetHistoryRequest": history, "messages.GetMessagesRequest": _by_ids(chat)}
    )
    args, s = snap(
        "telegram_get_context", message_ref=_anchor_ref(conn, "user:100", 50), before=3, after=2
    )
    out = await reads.get_context(args, s)
    assert [m["text"] for m in out["messages"]] == ["m47", "m48", "m49", "m50", "m51", "m52"]
    assert out["anchor_message_ref"] == out["messages"][3]["message_ref"]
    assert out["peer"] == {"peer_ref": s.peer_ref, "display_name": "Ali"}
    assert all(r.limit <= 100 for r in calls) and len(calls) == 2


async def test_zero_neighbours_is_just_the_anchor_and_no_history(world):
    conn, fake, reads, snap, _refs = world
    chat = [_msg(50, types.PeerUser(100))]
    fake.script["messages.GetMessagesRequest"] = _by_ids(chat)
    args, s = snap(
        "telegram_get_context", message_ref=_anchor_ref(conn, "user:100", 50), before=0, after=0
    )
    out = await reads.get_context(args, s)
    assert [m["text"] for m in out["messages"]] == ["m50"]
    assert "messages.GetHistoryRequest" not in fake.calls


async def test_a_deleted_anchor_is_message_not_found(world):
    conn, fake, reads, snap, _refs = world
    fake.script["messages.GetMessagesRequest"] = _by_ids([_msg(49, types.PeerUser(100))])
    args, s = snap(
        "telegram_get_context", message_ref=_anchor_ref(conn, "user:100", 50), before=1, after=1
    )
    with pytest.raises(RetrievalRefusal) as exc:
        await reads.get_context(args, s)
    assert exc.value.code == "MESSAGE_NOT_FOUND"


def _forum_world(fake, messages):
    fake.session.remember(FORUM)
    fake.script["messages.GetPeerDialogsRequest"] = _peer_dialogs(
        [(types.PeerChannel(7), {"unread": 0, "minute": 1})], chats=(FORUM,)
    )
    fake.script["channels.GetMessagesRequest"] = _by_ids(messages, chats=(FORUM,))


async def test_a_named_topic_window_uses_replies_and_never_history(world):
    conn, fake, reads, snap, _refs = world
    peer = types.PeerChannel(7)
    thread = [_msg(12, peer, root=True)] + [_msg(i, peer, topic=12) for i in (20, 22, 24, 26)]
    _forum_world(fake, thread)
    seen = []

    def replies(request):
        seen.append(request)
        rows = [
            m
            for m in thread[1:]
            if (not request.min_id or m.id > request.min_id)
            and (not request.max_id or m.id < request.max_id)
        ]
        rows.sort(key=lambda m: -m.id)
        return types.messages.Messages(
            messages=rows[: request.limit], topics=[], chats=[FORUM], users=[]
        )

    fake.script["messages.GetRepliesRequest"] = replies
    args, s = snap(
        "telegram_get_context", message_ref=_anchor_ref(conn, "channel:7", 22), before=5, after=1
    )
    out = await reads.get_context(args, s)
    assert [m["text"] for m in out["messages"]] == ["m20", "m22", "m24"]
    assert {r.msg_id for r in seen} == {12} and "messages.GetHistoryRequest" not in fake.calls


async def test_a_topic_root_has_nothing_before_it(world):
    conn, fake, reads, snap, _refs = world
    peer = types.PeerChannel(7)
    thread = [_msg(12, peer, root=True), _msg(20, peer, topic=12)]
    _forum_world(fake, thread)
    fake.script["messages.GetRepliesRequest"] = lambda r: types.messages.Messages(
        messages=[thread[1]] if r.min_id == 12 else [], topics=[], chats=[FORUM], users=[]
    )
    args, s = snap(
        "telegram_get_context", message_ref=_anchor_ref(conn, "channel:7", 12), before=5, after=5
    )
    out = await reads.get_context(args, s)
    assert out["messages"][0]["message_ref"] == out["anchor_message_ref"]
    assert len(out["messages"]) == 2 and fake.calls.count("messages.GetRepliesRequest") == 1


async def test_general_never_includes_a_named_topic_message(world):
    conn, fake, reads, snap, _refs = world
    peer = types.PeerChannel(7)
    chat = [
        _msg(i, peer, topic=(12 if i % 2 else None)) for i in range(30, 71)
    ]  # odd ids are in topic 12
    _forum_world(fake, chat)
    history, _calls = _server(chat, anchor_inclusive=False, users=(), chats=(FORUM,))
    fake.script["messages.GetHistoryRequest"] = history
    args, s = snap(
        "telegram_get_context", message_ref=_anchor_ref(conn, "channel:7", 50), before=4, after=4
    )
    out = await reads.get_context(args, s)
    assert [m["text"] for m in out["messages"]] == [f"m{i}" for i in range(42, 60, 2)]
    assert all(m["forum_topic"] is False for m in out["messages"])


async def test_general_short_of_budget_is_partial_not_padded(world):
    conn, fake, reads, snap, _refs = world
    peer = types.PeerChannel(7)
    chat = [_msg(50, peer)] + [
        _msg(i, peer, topic=12) for i in range(1, 50)
    ]  # nothing General below
    _forum_world(fake, chat)
    history, _calls = _server(chat, anchor_inclusive=False, users=(), chats=(FORUM,))
    fake.script["messages.GetHistoryRequest"] = history
    reads._max_rpcs = 4  # dialogs + anchor + two pages, then the budget fires
    args, s = snap(
        "telegram_get_context", message_ref=_anchor_ref(conn, "channel:7", 50), before=10, after=0
    )
    out = await reads.get_context(args, s)
    assert out["_partial"] is True and [m["text"] for m in out["messages"]] == ["m50"]


async def test_a_historical_ref_does_not_survive_a_later_deny(world):
    conn, _fake, _reads, snap, _refs = world
    ref = _anchor_ref(conn, "user:100", 50)
    conn.execute("UPDATE peer_policy SET decision = 'deny' WHERE telegram_peer_id = 100")
    conn.execute("UPDATE policy_state SET policy_epoch = policy_epoch + 1")
    conn.commit()
    with pytest.raises(AuthorityRefusal) as exc:
        snap("telegram_get_context", message_ref=ref, before=1, after=1)
    assert exc.value.code == "NOT_ACCESSIBLE"


async def test_an_unknown_message_ref_is_ref_not_found(world):
    _conn, _fake, _reads, snap, _refs = world
    with pytest.raises(AuthorityRefusal) as exc:
        snap("telegram_get_context", message_ref="tgm_" + "z" * 26, before=1, after=1)
    assert exc.value.code == "REF_NOT_FOUND"


async def test_the_page_cap_never_drops_the_anchor(world):
    conn, fake, reads, snap, _refs = world
    chat = [_msg(i, types.PeerUser(100), text="\x01" * 4096) for i in range(1, 102)]
    history, _calls = _server(chat, anchor_inclusive=False)
    fake.script.update(
        {"messages.GetHistoryRequest": history, "messages.GetMessagesRequest": _by_ids(chat)}
    )
    args, s = snap(
        "telegram_get_context", message_ref=_anchor_ref(conn, "user:100", 51), before=50, after=50
    )
    out = await reads.get_context(args, s)
    refs = [m["message_ref"] for m in out["messages"]]
    assert out["anchor_message_ref"] in refs and out["_partial"] is True and 1 <= len(refs) <= 101


async def test_a_flood_wait_on_the_window_is_flood_wait(world):
    conn, fake, reads, snap, _refs = world
    chat = [_msg(50, types.PeerUser(100))]
    fake.script.update(
        {
            "messages.GetMessagesRequest": _by_ids(chat),
            "messages.GetHistoryRequest": errors.FloodWaitError(request=None, capture=9),
        }
    )
    args, s = snap(
        "telegram_get_context", message_ref=_anchor_ref(conn, "user:100", 50), before=1, after=0
    )
    with pytest.raises(RetrievalRefusal) as exc:
        await reads.get_context(args, s)
    assert (exc.value.code, exc.value.retry_after) == ("FLOOD_WAIT", 9)
```

Create `tests/integration/test_search_reads.py`:

```python
"""search_messages and cross_project_search over a fake Telegram (spec §21, §21A, §23D)."""

import asyncio
import dataclasses
from datetime import UTC, datetime, timedelta

import pytest
from telethon import errors
from telethon.tl import types

from telegram_mcp.disclosure.coordinator import RetrievalRefusal, _split_sidecar
from telegram_mcp.disclosure.coverage import validate_coverage
from telegram_mcp.telegram.reads import TelegramReads
from tests.authority_fixtures import BETA_REF, PROJECT_REF, seed_second_project
from tests.integration.test_telegram_reads import make_reads_world

BASE = datetime(2026, 9, 21, 8, 0, 0, tzinfo=UTC)


def _peer_key(peer):
    for attr, kind in (("user_id", "user"), ("chat_id", "chat"), ("channel_id", "channel")):
        if hasattr(peer, attr):
            return f"{kind}:{getattr(peer, attr)}"
    raise AssertionError(peer)


def _telegram_peer(identity):
    kind, _, raw = identity.partition(":")
    return {"user": types.PeerUser, "chat": types.PeerChat, "channel": types.PeerChannel}[kind](
        int(raw)
    )


def _search_server(corpus, *, inexact=()):
    """corpus: identity -> list of (message_id, minutes after BASE). Newest first, offset exclusive."""
    seen = []

    def search(request):
        identity = _peer_key(request.peer)
        seen.append((identity, request))
        rows = sorted(corpus.get(identity, []), key=lambda r: -r[0])
        rows = [r for r in rows if not request.offset_id or r[0] < request.offset_id]
        page = rows[: request.limit]
        messages = [
            types.Message(
                id=i,
                peer_id=_telegram_peer(identity),
                date=BASE + timedelta(minutes=m),
                message=f"needle {identity} {i}",
            )
            for i, m in page
        ]
        if identity in inexact:
            return types.messages.MessagesSlice(
                count=99, inexact=True, messages=messages, topics=[], chats=[], users=[]
            )
        return types.messages.Messages(messages=messages, topics=[], chats=[], users=[])

    return search, seen


@pytest.fixture
async def world(tmp_path):
    conn, fake, reads, snap, refs = await make_reads_world(tmp_path)
    seed_second_project(conn)
    authority = reads._mint.__self__
    reads = TelegramReads(
        reads._session,
        conn,
        mint_cursor=authority.mint_project_cursor,
        mint_search_cursor=authority.mint_search_cursor,
    )
    fake.calls.clear()  # start() probes authorisation once; tests count what follows
    return conn, fake, reads, snap, refs


def _check(out):
    data, side = _split_sidecar(out)
    validate_coverage(
        side["_coverage"], next_cursor=side.get("_next_cursor"), partial=bool(side.get("_partial"))
    )
    return data, side


async def test_a_project_search_is_per_peer_newest_first_and_complete(world):
    _conn, fake, reads, snap, _refs = world
    search, seen = _search_server(
        {"user:100": [(10, 5), (11, 9)], "chat:9": [(20, 7)], "channel:7": [(30, 8)]}
    )
    fake.script["messages.SearchRequest"] = search
    args, s = snap("telegram_search_messages", project_ref=PROJECT_REF, query="needle", limit=20)
    data, side = _check(await reads.search_messages(args, s))
    assert [r["text"] for r in data["results"]] == [
        "needle user:100 11",
        "needle chat:9 20",
        "needle user:100 10",
    ]
    assert {identity for identity, _ in seen} == {
        "user:100",
        "chat:9",
    }  # archived channel: owner excludes it
    assert side["_coverage"]["complete"] is True and "_next_cursor" not in side
    assert data["search_scope"] == "project" and all(
        r["origin_project_refs"] == [PROJECT_REF] for r in data["results"]
    )
    assert set(fake.calls) <= {"messages.GetPeerDialogsRequest", "messages.SearchRequest"}


async def test_since_is_inclusive_until_exclusive_with_a_second_of_over_fetch(world):
    _conn, fake, reads, snap, _refs = world
    search, seen = _search_server({"user:100": [(1, 0), (2, 10), (3, 20)]})
    fake.script["messages.SearchRequest"] = search
    args, s = snap(
        "telegram_search_messages",
        project_ref=PROJECT_REF,
        query="needle",
        since="2026-09-21T08:00:00Z",
        until="2026-09-21T08:20:00Z",
        limit=20,
    )
    data, _side = _check(await reads.search_messages(args, s))
    assert [r["text"] for r in data["results"]] == ["needle user:100 2", "needle user:100 1"]
    request = next(r for identity, r in seen if identity == "user:100")
    assert request.min_date == BASE - timedelta(seconds=1)
    assert request.max_date == BASE + timedelta(minutes=20, seconds=1)


async def test_continuation_delivers_every_hit_exactly_once(world):
    _conn, fake, reads, snap, _refs = world
    search, _seen = _search_server(
        {"user:100": [(i, i) for i in range(1, 6)], "chat:9": [(i, i) for i in range(10, 13)]}
    )
    fake.script["messages.SearchRequest"] = search
    got, cursor = [], None
    for _page in range(20):
        extra = {"cursor": cursor} if cursor else {}
        args, s = snap(
            "telegram_search_messages", project_ref=PROJECT_REF, query="needle", limit=2, **extra
        )
        data, side = _check(await reads.search_messages(args, s))
        got += [r["message_ref"] for r in data["results"]]
        cursor = side.get("_next_cursor")
        if cursor is None:
            assert side["_coverage"]["complete"] is True
            break
        assert "response_limit" in side["_coverage"]["partial_reasons"]
    assert len(got) == 8 and len(set(got)) == 8


async def test_cross_search_attributes_shared_hits_and_intersects_egress(world):
    conn, fake, reads, snap, _refs = world
    conn.execute("UPDATE policy_state SET include_archived = 1, policy_epoch = policy_epoch + 1")
    conn.commit()
    search, _seen = _search_server({"channel:7": [(30, 8)], "user:101": [(40, 9)]})
    fake.script["messages.SearchRequest"] = search
    args, s = snap(
        "telegram_cross_project_search",
        project_refs=[PROJECT_REF, BETA_REF],
        query="needle",
        limit=20,
    )
    raw = await reads.cross_project_search(args, s)
    data, side = _check(reads._mint.__self__.apply_egress(raw, s))
    shared = next(
        r
        for r in data["results"]
        if "channel" in (r["peer_display_name"] or "").lower()
        or r["origin_project_refs"] == sorted([PROJECT_REF, BETA_REF])
    )
    assert shared["origin_project_refs"] == sorted([PROJECT_REF, BETA_REF])
    assert [m["project_ref"] for m in shared["matched_projects"]] == sorted([PROJECT_REF, BETA_REF])
    assert shared["text"] is None  # Beta is metadata_only: the most restrictive grant wins
    assert data["search_scope"] == "cross_project" and len(data["projects"]) == 2
    assert [c["eligible_peers"] for c in side["_coverage"]["project_coverage"]] == [3, 2]


@pytest.mark.parametrize(
    "reason",
    ["rpc_budget", "deadline", "hit_budget", "peer_budget", "telegram_partial", "response_limit"],
)
async def test_every_partial_reason_is_reported_honestly(world, reason):
    _conn, fake, reads, snap, _refs = world
    corpus = {
        "user:100": [(i, i % 50) for i in range(1, 30)],
        "chat:9": [(i, i % 50) for i in range(100, 130)],
    }
    search, _seen = _search_server(
        corpus, inexact={"chat:9"} if reason == "telegram_partial" else ()
    )
    fake.script["messages.SearchRequest"] = search
    limit = 50
    if reason == "rpc_budget":
        reads._max_rpcs = 2  # dialogs + one search
    if reason == "response_limit":
        limit = 3
    if reason == "deadline":

        async def slow(request):
            await asyncio.sleep(0.3)
            return search(request)

        fake.script["messages.SearchRequest"] = lambda r: slow(r)
        fake.__class__ = type("Awaiting", (type(fake),), {"__call__": _awaiting_call})
        reads._deadline_s = 0.4
    args, s = snap("telegram_search_messages", project_ref=PROJECT_REF, query="needle", limit=limit)
    if reason == "peer_budget":
        s = dataclasses.replace(s, peer_cap=1)
    if reason == "hit_budget":
        from telegram_mcp.telegram import search as engine

        original = engine.run_page

        async def small_budget(*a, **k):
            return await original(*a, **{**k, "max_hits": 5})

        engine_patch = pytest.MonkeyPatch()
        engine_patch.setattr("telegram_mcp.telegram.reads.run_page", small_budget)
    try:
        _data, side = _check(await reads.search_messages(args, s))
    finally:
        if reason == "hit_budget":
            engine_patch.undo()
    assert reason in side["_coverage"]["partial_reasons"]
    assert side["_coverage"]["complete"] is False and side["_partial"] is True


async def _awaiting_call(self, request):
    from telegram_mcp.telegram.telethon_adapter import qualified

    name = qualified(request)
    self.calls.append(name)
    outcome = self.script.get(name)
    if outcome is None:
        result = self._default(name, request)
    elif callable(outcome):
        result = outcome(request)
    else:
        result = outcome
    if asyncio.iscoroutine(result):
        result = await result
    for entity in [*getattr(result, "users", []), *getattr(result, "chats", [])]:
        self.session.remember(entity)
    return result


async def test_an_unreachable_member_is_never_claimed_complete(world):
    _conn, fake, reads, snap, _refs = world
    del fake.session.entities[-9]  # Team is not in the entity cache
    search, _seen = _search_server({"user:100": [(1, 1)]})
    fake.script["messages.SearchRequest"] = search
    args, s = snap("telegram_search_messages", project_ref=PROJECT_REF, query="needle", limit=20)
    _data, side = _check(await reads.search_messages(args, s))
    assert side["_coverage"]["complete"] is False
    assert side["_coverage"]["partial_reasons"] == ["telegram_partial"]


async def test_a_long_page_is_held_under_the_cap_without_losing_hits(world):
    _conn, fake, reads, snap, _refs = world
    corpus = {"user:100": [(i, i) for i in range(1, 11)]}

    def search(request):
        if _peer_key(request.peer) != "user:100":
            return types.messages.Messages(messages=[], topics=[], chats=[], users=[])
        rows = sorted(
            (r for r in corpus["user:100"] if not request.offset_id or r[0] < request.offset_id),
            key=lambda r: -r[0],
        )[: request.limit]
        return types.messages.Messages(
            messages=[
                types.Message(
                    id=i,
                    peer_id=types.PeerUser(100),
                    date=BASE + timedelta(minutes=m),
                    message="\x01" * 4096,
                )
                for i, m in rows
            ],
            topics=[],
            chats=[],
            users=[],
        )

    fake.script["messages.SearchRequest"] = search
    got, cursor = [], None
    for _page in range(20):
        extra = {"cursor": cursor} if cursor else {}
        args, s = snap(
            "telegram_search_messages", project_ref=PROJECT_REF, query="needle", limit=10, **extra
        )
        data, side = _check(await reads.search_messages(args, s))
        got += [r["message_ref"] for r in data["results"]]
        cursor = side.get("_next_cursor")
        if cursor is None:
            break
    assert len(got) == 10 and len(set(got)) == 10


async def test_a_flood_wait_fails_the_call(world):
    _conn, fake, reads, snap, _refs = world
    fake.script["messages.SearchRequest"] = errors.FloodWaitError(request=None, capture=4)
    args, s = snap("telegram_search_messages", project_ref=PROJECT_REF, query="needle", limit=20)
    with pytest.raises(RetrievalRefusal) as exc:
        await reads.search_messages(args, s)
    assert (exc.value.code, exc.value.retry_after) == ("FLOOD_WAIT", 4)
```

Create `tests/integration/test_exposure_bound_invariant_4c.py`:

```python
"""Gauntlet G-8 for 4c: the reservation dominates the Phase-3 measurement.

get_context, search_messages and cross_project_search, every egress mode,
seeded adversarial strings (the generators of the 4b invariant).
"""

import random

import pytest
from telethon.tl import types

from telegram_mcp.disclosure.budget import buckets_for
from telegram_mcp.disclosure.coordinator import RetrievalRefusal, _split_sidecar
from telegram_mcp.storage.refstore import RefStore
from telegram_mcp.telegram.reads import TelegramReads
from tests.authority_fixtures import BETA_REF, PROJECT_REF, seed_second_project
from tests.integration.test_exposure_bound_invariant import _script
from tests.integration.test_telegram_reads import make_reads_world

ROUNDS = 8


@pytest.fixture
async def world(tmp_path):
    conn, fake, reads, snap, _refs = await make_reads_world(tmp_path)
    seed_second_project(conn)
    authority = reads._mint.__self__
    reads = TelegramReads(
        reads._session,
        conn,
        mint_cursor=authority.mint_project_cursor,
        mint_search_cursor=authority.mint_search_cursor,
    )
    return conn, fake, reads, snap, authority


def _rehome(history, request):
    """Serve the adversarial page as if it came from the searched peer."""
    peer = request.peer
    if hasattr(peer, "user_id"):
        target = types.PeerUser(peer.user_id)
    elif hasattr(peer, "chat_id"):
        target = types.PeerChat(peer.chat_id)
    else:
        target = types.PeerChannel(peer.channel_id)
    for message in history.messages:
        message.peer_id = target
    history.messages = history.messages[: request.limit]
    return history


@pytest.mark.parametrize("egress", [("metadata_only", None), ("excerpt", 64), ("full_text", None)])
async def test_actual_charge_never_exceeds_the_reservation(world, egress):
    conn, fake, reads, snap, authority = world
    conn.execute("UPDATE client_projects SET egress_level = ?, excerpt_max_codepoints = ?", egress)
    conn.execute("UPDATE policy_state SET include_archived = 1")
    conn.commit()
    rng = random.Random(f"g8-4c-{egress}")
    anchor = RefStore(conn, account_id=1).message_ref(
        RefStore(conn, account_id=1).peer_by_identity("chat:9").row_id, 1
    )
    conn.commit()
    for _round in range(ROUNDS):
        limit = rng.randint(1, 50)
        script = _script(rng, max(limit, 3))
        history = script["messages.GetHistoryRequest"]
        fake.script.update(script)
        fake.script["messages.SearchRequest"] = lambda r, h=history: _rehome(h, r)
        fake.script["messages.GetMessagesRequest"] = types.messages.Messages(
            messages=[m for m in history.messages if m.id == 1] or history.messages[-1:],
            topics=[],
            chats=history.chats,
            users=history.users,
        )
        cases = [
            (
                "telegram_get_context",
                {
                    "message_ref": anchor,
                    "before": rng.randint(0, 50),
                    "after": rng.randint(0, 50),
                    "project_ref": PROJECT_REF,
                },
            ),
            (
                "telegram_search_messages",
                {"project_ref": PROJECT_REF, "query": "q", "limit": limit},
            ),
            (
                "telegram_cross_project_search",
                {"project_refs": [PROJECT_REF, BETA_REF], "query": "q", "limit": limit},
            ),
        ]
        for tool, args in cases:
            validated, snapshot = snap(tool, **args)
            method = {
                "telegram_get_context": reads.get_context,
                "telegram_search_messages": reads.search_messages,
                "telegram_cross_project_search": reads.cross_project_search,
            }[tool]
            try:
                raw = await method(validated, snapshot)
            except RetrievalRefusal as exc:  # the generated page may lack the anchor
                assert exc.code == "MESSAGE_NOT_FOUND", exc
                continue
            data, _side = _split_sidecar(authority.apply_egress(raw, snapshot))
            actual = buckets_for(tool, data, client_id=snapshot.client_id)
            reserved = authority.worst_case_buckets(tool, snapshot)
            for key, usage in actual.items():
                assert key in reserved, (tool, key)
                assert usage.records <= reserved[key].records, (tool, egress, usage, reserved[key])
                assert usage.bytes <= reserved[key].bytes, (tool, egress, usage, reserved[key])
```

- [ ] **Step 2: Run them and watch them fail**

Run: `uv run pytest tests/integration/test_telegram_reads.py tests/integration/test_get_context.py tests/integration/test_search_reads.py tests/integration/test_exposure_bound_invariant_4c.py -q`
Expected: `AttributeError: 'TelegramReads' object has no attribute 'get_context'`, and `TypeError: ... unexpected keyword argument 'mint_search_cursor'`.
Observed in the staged proof: 10 failed, 17 passed, 16 errors in 10.65s; first error: `TypeError: TelegramReads.__init__() got an unexpected keyword argument 'mint_search_cursor'`.

- [ ] **Step 3: Implement**

Apply to `src/telegram_mcp/telegram/reads.py`:

```diff
--- a/src/telegram_mcp/telegram/reads.py
+++ b/src/telegram_mcp/telegram/reads.py
@@ -12,9 +12,12 @@
 
 import sqlite3
 from collections.abc import Callable, Mapping
+from dataclasses import dataclass
 from typing import Any
 
+from telegram_mcp.consent.challenge import jcs_dumps
 from telegram_mcp.disclosure.bounds import (
+    DATA_BYTES_MAX,
     MEDIA_KIND_MAX,
     NAME_MAX,
     TEXT_MAX,
@@ -24,12 +27,32 @@
 )
 from telegram_mcp.disclosure.coordinator import RetrievalRefusal
 from telegram_mcp.disclosure.seams import ProjectSnapshot, normalise
+from telegram_mcp.disclosure.search_authority import SearchSnapshot
 from telegram_mcp.storage.refstore import PeerRow, RefStore
 from telegram_mcp.telegram.deadline import Deadline, WorkBudget
 from telegram_mcp.telegram.errors import GatewayError
+from telegram_mcp.telegram.search import EngineState, SearchStop, rpc_bounds, run_page
 
 __all__ = ["TelegramReads"]
 
+_GENERAL = 1  # the forum's General topic (design §4.1)
+_PAGE = 100  # Telegram's history page ceiling
+_CONTEXT_MAX = 101  # spec §20.4: before + after + anchor
+
+
+def _topic_of(anchor: Any, is_forum: bool) -> int | None:
+    """Design §4.1 step 2. ``None`` is an ordinary chat; ``1`` is General."""
+    if not is_forum:
+        return None
+    if anchor.topic_root:
+        return int(anchor.message_id)
+    if anchor.forum_topic and anchor.reply_to_top_id:
+        return int(anchor.reply_to_top_id)
+    if anchor.forum_topic and anchor.reply_to_id:
+        return int(anchor.reply_to_id)
+    return _GENERAL
+
+
 _RANK = {
     "exact_display_name": 0,
     "exact_username": 1,
@@ -65,12 +88,15 @@
         conn: sqlite3.Connection,
         *,
         mint_cursor: Callable[[ProjectSnapshot, Mapping[str, Any], Mapping[str, Any]], str],
+        mint_search_cursor: Callable[[SearchSnapshot, Mapping[str, Any], Mapping[str, Any]], str]
+        | None = None,
         deadline_s: float = 15.0,
         max_rpcs: int = 20,
     ) -> None:
         self._session = session
         self._conn = conn
         self._mint = mint_cursor
+        self._mint_search = mint_search_cursor
         self._deadline_s = deadline_s
         self._max_rpcs = max_rpcs
 
@@ -157,6 +183,55 @@
                 nxt["anchor_date"] = first_newest
             data["_next_cursor"] = self._mint(snapshot, arguments, nxt)
         return data
+
+    def _message_record(
+        self, view: Any, chat: PeerRow, snapshot: ProjectSnapshot, refs: RefStore
+    ) -> dict[str, Any]:
+        """One contract message (get_messages, get_context). Egress runs later."""
+        text, cut = clamp(view.text, TEXT_MAX)
+        sender_ref = None
+        if view.sender is not None:
+            identity = f"{view.sender[0]}:{view.sender[1]}"
+            if identity in snapshot.readable:  # §19.4: existing refs of readable peers only
+                row = refs.peer_by_identity(identity)
+                sender_ref = row.peer_ref if row is not None else None
+        return {
+            "message_ref": refs.message_ref(chat.row_id, view.message_id),
+            "origin_project_refs": [snapshot.project_ref],
+            "sender_kind": view.sender_kind,
+            "sender_display_name": clamp(view.sender_display_name, NAME_MAX)[0],
+            "sender_peer_ref": sender_ref,
+            "post_author": clamp(view.post_author, NAME_MAX)[0],
+            "forum_topic": view.forum_topic,
+            "topic_title": None,  # design §3.8: needs an unreviewed RPC
+            "sent_at": view.sent_at,
+            "outgoing": view.outgoing,
+            "text": text,
+            "text_truncated": cut,
+            "reply_to_message_ref": (
+                refs.message_ref(chat.row_id, view.reply_to_id) if view.reply_to_id else None
+            ),
+            "has_media": view.has_media,
+            "media_kind": clamp(view.media_kind, MEDIA_KIND_MAX)[0],
+            "edited": view.edited,
+        }
+
+    async def _readable_dialog(
+        self, snapshot: ProjectSnapshot, deadline: Deadline, budget: WorkBudget
+    ) -> Any:
+        """The selected peer's dialog, admitted by the owner's chat-kind switches."""
+        assert snapshot.peer_identity is not None  # the snapshot refused otherwise
+        peer_type, peer_id = _split(snapshot.peer_identity)
+        dialogs = await self._session.peer_dialogs(
+            [(peer_type, peer_id)],
+            client_ref=snapshot.client_ref,
+            deadline=deadline,
+            budget=budget,
+        )
+        dialog = dialogs.get(snapshot.peer_identity)
+        if dialog is None or not snapshot.owner_scope.admits(dialog.chat_type, dialog.is_archived):
+            raise GatewayError("NOT_ACCESSIBLE")
+        return dialog
 
     def _project(self, snapshot: ProjectSnapshot) -> dict[str, str]:
         return {"project_ref": snapshot.project_ref, "display_name": snapshot.project_display_name}
@@ -332,39 +407,7 @@
             username=clamp(dialog.username, USERNAME_MAX)[0],
         )
         anchor = anchor or (views[0].message_id if views else 0)
-        messages = []
-        for view in views:
-            text, cut = clamp(view.text, TEXT_MAX)
-            sender_ref = None
-            if view.sender is not None:
-                identity = f"{view.sender[0]}:{view.sender[1]}"
-                if identity in snapshot.readable:  # §19.4: existing refs of readable peers only
-                    row = refs.peer_by_identity(identity)
-                    sender_ref = row.peer_ref if row is not None else None
-            messages.append(
-                {
-                    "message_ref": refs.message_ref(chat.row_id, view.message_id),
-                    "origin_project_refs": [snapshot.project_ref],
-                    "sender_kind": view.sender_kind,
-                    "sender_display_name": clamp(view.sender_display_name, NAME_MAX)[0],
-                    "sender_peer_ref": sender_ref,
-                    "post_author": clamp(view.post_author, NAME_MAX)[0],
-                    "forum_topic": view.forum_topic,
-                    "topic_title": None,  # design §3.8: needs an unreviewed RPC
-                    "sent_at": view.sent_at,
-                    "outgoing": view.outgoing,
-                    "text": text,
-                    "text_truncated": cut,
-                    "reply_to_message_ref": (
-                        refs.message_ref(chat.row_id, view.reply_to_id)
-                        if view.reply_to_id
-                        else None
-                    ),
-                    "has_media": view.has_media,
-                    "media_kind": clamp(view.media_kind, MEDIA_KIND_MAX)[0],
-                    "edited": view.edited,
-                }
-            )
+        messages = [self._message_record(view, chat, snapshot, refs) for view in views]
         data: dict[str, Any] = {
             "project": self._project(snapshot),
             "peer": {
@@ -383,5 +426,378 @@
         if offset:
             data["_next_cursor"] = self._mint(
                 snapshot, arguments, {"anchor_id": anchor, "offset_id": offset}
+            )
+        return data
+
+    async def get_context(
+        self, arguments: Mapping[str, Any], snapshot: ProjectSnapshot
+    ) -> dict[str, Any]:
+        """Neighbours of one anchor, never crossing a forum topic (spec §20, design §4.1).
+
+        Telegram caps history pages at 100 and its ``add_offset`` edge
+        semantics are undocumented, so each side is its own request bounded
+        by ``min_id``/``max_id``: the older side reads strictly below the
+        anchor, the newer side strictly above it. Neither depends on whether
+        Telegram's window includes ``offset_id``.
+        """
+        assert snapshot.peer_identity is not None and snapshot.anchor_message_id is not None
+        before, after = int(arguments["before"]), int(arguments["after"])
+        anchor_id = snapshot.anchor_message_id
+        deadline, budget = self._bounds()
+        peer_type, peer_id = _split(snapshot.peer_identity)
+        refs = RefStore(self._conn, account_id=snapshot.account_id)
+        partial = False
+        try:
+            dialog = await self._readable_dialog(snapshot, deadline, budget)
+            found, is_forum = await self._session.fetch_by_ids(
+                peer_type,
+                peer_id,
+                [anchor_id],
+                client_ref=snapshot.client_ref,
+                deadline=deadline,
+                budget=budget,
+            )
+            anchor = next((v for v in found if v.message_id == anchor_id), None)
+            if anchor is None:
+                raise GatewayError("MESSAGE_NOT_FOUND")
+            topic = _topic_of(anchor, is_forum)
+            older, newer, partial = await self._window(
+                snapshot, peer_type, peer_id, anchor, topic, before, after, deadline, budget
+            )
+        except GatewayError as exc:
+            raise RetrievalRefusal(exc.code, exc.retry_after) from None
+        chat = refs.ensure_peer(
+            peer_type,
+            peer_id,
+            display_name=_name(dialog.display_name),
+            username=clamp(dialog.username, USERNAME_MAX)[0],
+        )
+        # Priority order for the page cap: the anchor first, then nearest
+        # neighbours alternately, so a cut never drops the anchor.
+        ordered_older = sorted(older, key=lambda v: -v.message_id)[:before]
+        ordered_newer = sorted(newer, key=lambda v: v.message_id)[:after]
+        priority = [anchor]
+        for index in range(max(len(ordered_older), len(ordered_newer))):
+            priority += ordered_newer[index : index + 1] + ordered_older[index : index + 1]
+        records = [self._message_record(v, chat, snapshot, refs) for v in priority]
+        data: dict[str, Any] = {
+            "project": self._project(snapshot),
+            "peer": {"peer_ref": chat.peer_ref, "display_name": chat.display_name or ""},
+            "anchor_message_ref": records[0]["message_ref"],
+            "messages": records,
+        }
+        if fit(data, "messages"):
+            partial = True
+        kept_ids = {v.message_id for v in priority[: len(data["messages"])]}
+        by_ref = {r["message_ref"]: r for r in data["messages"]}
+        data["messages"] = [
+            by_ref[refs.message_ref(chat.row_id, v.message_id)]
+            for v in sorted(priority, key=lambda v: v.message_id)
+            if v.message_id in kept_ids
+        ][:_CONTEXT_MAX]
+        if partial:
+            data["_partial"] = True
+        return data
+
+    async def _window(
+        self,
+        snapshot: ProjectSnapshot,
+        peer_type: str,
+        peer_id: int,
+        anchor: Any,
+        topic: int | None,
+        before: int,
+        after: int,
+        deadline: Deadline,
+        budget: WorkBudget,
+    ) -> tuple[list[Any], list[Any], bool]:
+        anchor_id = anchor.message_id
+        kw = {"client_ref": snapshot.client_ref, "deadline": deadline, "budget": budget}
+        if topic is None or topic == _GENERAL:
+            general = topic == _GENERAL
+            older, older_partial = await self._walk(
+                peer_type, peer_id, anchor_id, before, "older", general, kw
+            )
+            newer, newer_partial = await self._walk(
+                peer_type, peer_id, anchor_id, after, "newer", general, kw
+            )
+            return older, newer, older_partial or newer_partial
+        older = []
+        if before and not anchor.topic_root:  # nothing precedes a root inside its topic
+            older = await self._session.fetch_replies(
+                peer_type,
+                peer_id,
+                topic,
+                offset_id=anchor_id,
+                add_offset=0,
+                limit=before,
+                min_id=0,
+                max_id=anchor_id,
+                **kw,
+            )
+        newer = []
+        if after:
+            newer = await self._session.fetch_replies(
+                peer_type,
+                peer_id,
+                topic,
+                offset_id=anchor_id,
+                add_offset=-(after + 1),
+                limit=after + 1,
+                min_id=anchor_id,
+                max_id=0,
+                **kw,
+            )
+        older = [v for v in older if v.message_id < anchor_id]
+        newer = [v for v in newer if v.message_id > anchor_id]
+        return older, newer, False
+
+    async def _walk(
+        self,
+        peer_type: str,
+        peer_id: int,
+        anchor_id: int,
+        want: int,
+        side: str,
+        general: bool,
+        kw: dict[str, Any],
+    ) -> tuple[list[Any], bool]:
+        """Collect ``want`` neighbours on one side. In General, named-topic
+        messages are dropped and the walk pages further out until filled, the
+        chat ends, or a budget fires (then partial, design §4.1 step 5)."""
+        found: list[Any] = []
+        edge = anchor_id
+        while len(found) < want:
+            need = min(want - len(found), _PAGE)
+            try:
+                if side == "older":
+                    views, _below = await self._session.fetch_history(
+                        peer_type, peer_id, offset_id=edge, max_id=edge, limit=need, **kw
+                    )
+                else:
+                    views, _below = await self._session.fetch_history(
+                        peer_type,
+                        peer_id,
+                        offset_id=edge,
+                        add_offset=-(need + 1),
+                        limit=need + 1,
+                        max_id=0,
+                        min_id=edge,
+                        **kw,
+                    )
+            except GatewayError as exc:
+                if exc.code in ("WORK_BUDGET_EXCEEDED", "DEADLINE_EXCEEDED") and general:
+                    return found, True
+                raise
+            # Nearest first, whatever order or window edge Telegram used.
+            if side == "older":
+                views = sorted(
+                    (v for v in views if v.message_id < edge), key=lambda v: -v.message_id
+                )
+            else:
+                views = sorted(
+                    (v for v in views if v.message_id > edge), key=lambda v: v.message_id
+                )
+            if not views:
+                return found, False  # the chat ends on this side
+            edge = (
+                min(v.message_id for v in views)
+                if side == "older"
+                else max(v.message_id for v in views)
             )
+            found += [v for v in views if not (general and v.forum_topic)]
+            if not general:
+                return found[:want], False
+        return found[:want], False
+
+    # -- search (4c) ---------------------------------------------------------
+
+    async def search_messages(
+        self, arguments: Mapping[str, Any], snapshot: SearchSnapshot
+    ) -> dict[str, Any]:
+        return await self._search(arguments, snapshot)
+
+    async def cross_project_search(
+        self, arguments: Mapping[str, Any], snapshot: SearchSnapshot
+    ) -> dict[str, Any]:
+        return await self._search(arguments, snapshot)
+
+    async def _search(
+        self, arguments: Mapping[str, Any], snapshot: SearchSnapshot
+    ) -> dict[str, Any]:
+        """Per-peer search over the snapshot's universe (spec §21.4, §21A.4).
+
+        Never global search. The engine owns continuation and coverage; this
+        owns Telegram access, the owner's chat-kind switches (checked from
+        dialog data before a peer is first searched) and record shaping.
+        """
+        assert self._mint_search is not None
+        deadline, budget = self._bounds()
+        refs = RefStore(self._conn, account_id=snapshot.account_id)
+        cross = snapshot.tool_name == "telegram_cross_project_search"
+        rows = refs.peers_by_identities(snapshot.universe)
+        by_ref = {row.peer_ref: identity for identity, row in rows.items()}
+        per_peer = {
+            by_ref[ref]: int(entry["offset_id"])
+            for ref, entry in snapshot.state.get("per_peer", {}).items()
+            if ref in by_ref
+        }
+        state = EngineState(
+            upper_date=snapshot.upper_date,
+            window_start=int(snapshot.state.get("window_start", 0)),
+            next_unstarted_index=int(snapshot.state.get("next_unstarted_index", 0)),
+            per_peer=per_peer,
+        )
+        min_date, max_date = rpc_bounds(snapshot.since, snapshot.upper_date)
+        admitted: dict[str, bool | None] = {}  # None: not reachable in the entity cache
+        names: dict[str, str] = {}
+
+        async def check_new_peers(first: str) -> None:
+            fresh = [p for p, off in state.per_peer.items() if off == 0 and p not in admitted]
+            fresh = [first, *[p for p in fresh if p != first]][:100]
+            views = await self._session.peer_dialogs(
+                [_split(p) for p in fresh],
+                client_ref=snapshot.client_ref,
+                deadline=deadline,
+                budget=budget,
+            )
+            for peer in fresh:
+                view = views.get(peer)
+                if view is None:
+                    admitted[peer] = None
+                    continue
+                admitted[peer] = snapshot.owner_scope.admits(view.chat_type, view.is_archived)
+                names[peer] = _name(view.display_name)
+
+        async def fetch(peer: str, offset_id: int, want: int) -> Any:
+            try:
+                if peer not in admitted:
+                    await check_new_peers(peer)
+                verdict = admitted[peer]
+                if not verdict:  # excluded by owner scope, or unreachable: never searched
+                    return _Nothing(inexact=verdict is None)
+                peer_type, peer_id = _split(peer)
+                return await self._session.search_peer(
+                    peer_type,
+                    peer_id,
+                    snapshot.query,
+                    min_date=min_date,
+                    max_date=max_date,
+                    offset_id=offset_id,
+                    limit=want,
+                    client_ref=snapshot.client_ref,
+                    deadline=deadline,
+                    budget=budget,
+                )
+            except GatewayError as exc:
+                if exc.code == "WORK_BUDGET_EXCEEDED":
+                    raise SearchStop("rpc_budget") from None
+                if exc.code == "DEADLINE_EXCEEDED":
+                    raise SearchStop("deadline") from None
+                raise RetrievalRefusal(exc.code, exc.retry_after) from None
+
+        projects_of = {
+            peer: sorted(p.project_ref for p in snapshot.projects if peer in p.readable)
+            for peer in snapshot.universe
+        }
+        display = {p.project_ref: p.display_name for p in snapshot.projects}
+        container: dict[str, Any] = (
+            {
+                "projects": [
+                    {"project_ref": p.project_ref, "display_name": p.display_name}
+                    for p in snapshot.projects
+                ],
+                "search_scope": "cross_project",
+            }
+            if cross
+            else {
+                "project": {
+                    "project_ref": snapshot.projects[0].project_ref,
+                    "display_name": snapshot.projects[0].display_name,
+                },
+                "search_scope": "peer" if snapshot.peer_ref else "project",
+            }
+        )
+        room = DATA_BYTES_MAX - len(jcs_dumps({**container, "results": []}))
+        used = [0]
+
+        def record(peer: str, view: Any, message_ref: str) -> dict[str, Any]:
+            text, cut = clamp(view.text, TEXT_MAX)
+            origins = projects_of[peer]
+            hit: dict[str, Any] = {
+                "origin_project_refs": origins,
+                "peer_ref": rows[peer].peer_ref,
+                "peer_display_name": names.get(peer) or _name(rows[peer].display_name),
+                "message_ref": message_ref,
+                "sender_kind": view.sender_kind,
+                "sender_display_name": clamp(view.sender_display_name, NAME_MAX)[0],
+                "sent_at": view.sent_at,
+                "text": text,
+                "text_truncated": cut,
+                "has_context": True,  # the ref resolves through get_context in any origin project
+            }
+            if cross:
+                hit["matched_projects"] = [
+                    {"project_ref": ref, "display_name": display[ref]} for ref in origins
+                ]
+            return hit
+
+        def accept(peer: str, view: Any) -> bool:
+            size = len(jcs_dumps(record(peer, view, "tgm_" + "a" * 26))) + 1  # refs are fixed width
+            if used[0] + size > room:
+                return False
+            used[0] += size
+            return True
+
+        result = await run_page(
+            list(snapshot.universe),
+            state,
+            limit=snapshot.limit,
+            fetch=fetch,
+            since=snapshot.since,
+            peer_cap=snapshot.peer_cap,
+            accept=accept,
+            projects={p.project_ref: p.readable for p in snapshot.projects},
+        )
+        results = []
+        for hit in result.hits:
+            peer_row = rows[hit.peer]
+            results.append(
+                record(hit.peer, hit.view, refs.message_ref(peer_row.row_id, hit.view.message_id))
+            )
+        results.sort(key=lambda r: r["message_ref"])
+        results.sort(
+            key=lambda r: r["sent_at"], reverse=True
+        )  # newest first, ref tie-break (§21A.4)
+        data: dict[str, Any] = {**container, "results": results}
+        if fit(data, "results"):  # accept() held the page under the cap; this is a backstop
+            raise RetrievalRefusal("INTERNAL_ERROR")
+        data["_coverage"] = result.coverage  # sidecar: never part of the measured data
+        if not result.complete:
+            data["_partial"] = True
+        if result.state is not None:
+            data["_next_cursor"] = self._mint_search(
+                snapshot,
+                arguments,
+                {
+                    "upper_date": snapshot.upper_date,
+                    "universe_digest": snapshot.universe_digest,
+                    "window_start": result.state.window_start,
+                    "next_unstarted_index": result.state.next_unstarted_index,
+                    "per_peer": {
+                        rows[peer].peer_ref: {"offset_id": offset}
+                        for peer, offset in result.state.per_peer.items()
+                    },
+                },
+            )
         return data
+
+
+@dataclass(frozen=True)
+class _Nothing:
+    """A peer the owner's switches exclude (or the cache cannot reach): not searched."""
+
+    inexact: bool
+    views: tuple[()] = ()
+    exhausted: bool = True
+    next_offset: None = None
```

- [ ] **Step 4: Run them and watch them pass**

Run: `uv run pytest tests/integration/test_telegram_reads.py tests/integration/test_get_context.py tests/integration/test_search_reads.py tests/integration/test_exposure_bound_invariant_4c.py -q`
Expected: 43 passed (observed in the staged proof).

**Control run (record it in the ledger):** Change `_W = "\x01"` in `bounds.py` to `_W = "a"`: `test_exposure_bound_invariant_4c.py` must fail all three cases. Restore it. The dry run observed exactly that: 3 failed, then 3 passed.

- [ ] **Step 5: Commit** (format, check, full gate first)

Full gate expected at this task: suite 1285 passed, 10 skipped; smoke 49 passed, 0 failed, 0 skipped — 49 checks.

```bash
git add tests/integration/test_telegram_reads.py tests/integration/test_get_context.py tests/integration/test_search_reads.py tests/integration/test_exposure_bound_invariant_4c.py src/telegram_mcp/telegram/reads.py
git commit -m "feat: serve get_context, search_messages and cross_project_search

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 7: Wiring: honest coverage at the coordinator, the cross-project warning, routes

Spec §14.1A, §21A.4, §23D; design §4.4–§4.5 and §4.7.

**Coverage gate.** For both searches, the coordinator refuses with `PROOF_GENERATION_FAILED` unless all of the following hold, in which case nothing is signed:
- the coverage object exists;
- it satisfies the §23D chain against the cursor and `meta.partial`;
- its `hits_returned` equals the records disclosed.

**Consent.** The cross-project prompt's risk line begins with the context-insertion warning.

**Routes.** Composition routes the three tools.

**Proof.** An end-to-end run through the real ingress proves:
- a signed coverage digest;
- `get_context`;
- the shared-peer race, discarded at step 8 with no receipt.

The smoke gains four Phase-4c rows.

**Files:**
- Create: `tests/integration/test_coordinator_coverage.py`
- Modify: `tests/unit/test_exposure_and_display.py`
- Create: `tests/integration/test_phase4c_end_to_end.py`
- Modify: `scripts/e2e_smoke.py`
- Modify: `src/telegram_mcp/disclosure/coordinator.py`
- Modify: `src/telegram_mcp/consent/display.py`
- Modify: `src/telegram_mcp/runtime/composition.py`

**Interfaces:**
- Produces:
  - coordinator coverage gate; `build_display` cross-project warning; routes for the three tools

- [ ] **Step 1: Write the failing tests**

Create `tests/integration/test_coordinator_coverage.py`:

```python
"""§23D/§14.1A at the coordinator: no search result is signed without honest coverage."""

import hashlib

import pytest

from telegram_mcp.consent.challenge import jcs_dumps
from telegram_mcp.disclosure.budget import Usage, buckets_for
from telegram_mcp.disclosure.coverage import build_coverage
from tests.authority_fixtures import PROJECT_REF
from tests.coordinator_fixtures import build_coordinator

HIT = {
    "origin_project_refs": [PROJECT_REF],
    "peer_ref": "tgp_" + "b" * 26,
    "peer_display_name": "Ali",
    "message_ref": "tgm_" + "c" * 26,
    "sender_kind": "user",
    "sender_display_name": "Ali",
    "sent_at": "2026-09-23T00:00:00Z",
    "text": "needle",
    "text_truncated": False,
    "has_context": True,
}


def _coverage(**over):
    base = {
        "complete": True,
        "eligible_peers": 1,
        "peers_scanned": 1,
        "telegram_rpcs": 1,
        "hits_examined": 1,
        "hits_returned": 1,
        "partial_reasons": [],
        "project_coverage": [{"project_ref": PROJECT_REF, "eligible_peers": 1, "peers_scanned": 1}],
    }
    base.update(over)
    return build_coverage(**base)


class _Adapter:
    def __init__(self, side):
        self.side = side

    async def retrieve(self, *, tool_name, arguments, snapshot=None):
        return {
            "project": {"project_ref": PROJECT_REF, "display_name": "Alpha"},
            "results": [dict(HIT)],
            "search_scope": "project",
            **self.side,
        }


def _coordinator(tmp_path):
    coordinator, conn, _adapter = build_coordinator(tmp_path)
    data = {
        "project": {"project_ref": PROJECT_REF, "display_name": "Alpha"},
        "results": [HIT],
        "search_scope": "project",
    }
    coordinator._authority._worst_case = {
        key: Usage(usage.records + 5, usage.bytes + 2000)
        for key, usage in buckets_for("telegram_search_messages", data, client_id=1).items()
    }
    return coordinator, conn


async def test_honest_coverage_is_signed_into_the_receipt(tmp_path):
    coordinator, conn = _coordinator(tmp_path)
    coverage = _coverage()
    outcome = await coordinator.disclose(
        tool_name="telegram_search_messages",
        arguments={},
        adapter=_Adapter({"_coverage": coverage}),
    )
    assert outcome.released and outcome.meta["coverage"] == coverage
    digest = conn.execute("SELECT canonical_coverage_digest FROM disclosure_receipts").fetchone()[0]
    assert digest == hashlib.sha256(jcs_dumps(coverage)).hexdigest()


@pytest.mark.parametrize(
    "side",
    [
        {},  # no coverage at all
        {"_coverage": _coverage(hits_returned=2)},  # does not describe the result
        {
            "_coverage": _coverage(complete=False, partial_reasons=["rpc_budget"]),
            "_next_cursor": "tgc_" + "a" * 26,
            "_partial": True,
        },  # a cursor without response_limit
        {"_coverage": _coverage(), "_partial": True},  # complete yet partial
    ],
)
async def test_dishonest_coverage_is_never_signed(tmp_path, side):
    coordinator, conn = _coordinator(tmp_path)
    outcome = await coordinator.disclose(
        tool_name="telegram_search_messages", arguments={}, adapter=_Adapter(side)
    )
    assert (outcome.released, outcome.error_code) == (False, "PROOF_GENERATION_FAILED")
    assert conn.execute("SELECT count(*) FROM disclosure_receipts").fetchone()[0] == 0
```

Apply to `tests/unit/test_exposure_and_display.py`:

```diff
--- a/tests/unit/test_exposure_and_display.py
+++ b/tests/unit/test_exposure_and_display.py
@@ -68,3 +68,22 @@
             current=Usage(0, 0),
             projected=Usage(1, 10),
         )
+
+
+def test_a_cross_project_prompt_warns_about_context_insertion():
+    from telegram_mcp.consent.display import build_display
+    from telegram_mcp.disclosure.budget import Usage
+
+    display = build_display(
+        tool_name="telegram_cross_project_search",
+        client_kind="codex_local",
+        project_names=["Alpha", "Beta"],
+        peer_name=None,
+        egress_level="full_text",
+        tier="elevated",
+        current=Usage(1200, 1_400_000),
+        projected=Usage(1250, 1_450_000),
+    )
+    assert display["risk_class"].startswith("results may enter this AI session's context")
+    assert "ELEVATED" in display["risk_class"] and len(display["risk_class"]) <= 160
+    assert display["project_display"] == ["Alpha", "Beta"]
```

Create `tests/integration/test_phase4c_end_to_end.py`:

```python
"""The three 4c tools through ingress, consent and coordinator on a fake Telegram."""

import hashlib
from datetime import UTC, datetime, timedelta

from telethon.tl import types

from telegram_mcp.consent.challenge import jcs_dumps
from telegram_mcp.storage.refstore import RefStore
from tests.authority_fixtures import BETA_REF, PROJECT_REF, seed_second_project
from tests.integration.test_phase4a_end_to_end import CODEX, call
from tests.integration.test_phase4b_end_to_end import _close, _world

BASE = datetime(2026, 9, 21, 8, 0, 0, tzinfo=UTC)


def _hits(request, identity_to_rows):
    peer = request.peer
    key = (
        ("user", peer.user_id)
        if hasattr(peer, "user_id")
        else (("chat", peer.chat_id) if hasattr(peer, "chat_id") else ("channel", peer.channel_id))
    )
    make = {"user": types.PeerUser, "chat": types.PeerChat, "channel": types.PeerChannel}[key[0]]
    rows = identity_to_rows.get(f"{key[0]}:{key[1]}", [])
    rows = [r for r in rows if not request.offset_id or r < request.offset_id][: request.limit]
    return types.messages.Messages(
        messages=[
            types.Message(
                id=i, peer_id=make(key[1]), date=BASE + timedelta(minutes=i), message=f"needle {i}"
            )
            for i in rows
        ],
        topics=[],
        chats=[],
        users=[],
    )


def _receipts(world):
    return world["conn"].execute("SELECT count(*) FROM disclosure_receipts").fetchone()[0]


async def test_search_carries_coverage_bound_into_the_signed_proof(tmp_path, monkeypatch):
    world = await _world(tmp_path, monkeypatch)
    try:
        world["fake"].script["messages.SearchRequest"] = lambda r: _hits(r, {"user:100": [3, 2, 1]})
        body = await call(
            world,
            CODEX,
            "telegram_search_messages",
            {"project_ref": PROJECT_REF, "query": "needle"},
        )
        assert body["ok"] is True, body
        coverage = body["meta"]["coverage"]
        assert coverage["complete"] is True and body["meta"]["partial"] is False
        proof = body["meta"]["disclosure"]["proof_payload"]
        assert proof["canonical_coverage_digest"] == hashlib.sha256(jcs_dumps(coverage)).hexdigest()
        assert [r["text"] for r in body["data"]["results"]] == ["needle 3", "needle 2", "needle 1"]
        assert "messages.SearchGlobalRequest" not in world["fake"].calls
    finally:
        await _close(world)


async def test_get_context_through_ingress(tmp_path, monkeypatch):
    world = await _world(tmp_path, monkeypatch)
    try:
        conn = world["conn"]
        refs = RefStore(conn, account_id=1)
        anchor = refs.message_ref(refs.peer_by_identity("user:100").row_id, 1)
        conn.commit()
        world["fake"].script["messages.GetMessagesRequest"] = types.messages.Messages(
            messages=[
                types.Message(id=1, peer_id=types.PeerUser(100), date=BASE, message="anchor")
            ],
            topics=[],
            chats=[],
            users=[],
        )
        body = await call(
            world,
            CODEX,
            "telegram_get_context",
            {"project_ref": PROJECT_REF, "message_ref": anchor, "before": 2, "after": 2},
        )
        assert body["ok"] is True, body
        assert body["data"]["anchor_message_ref"] == anchor and body["meta"]["coverage"] is None
    finally:
        await _close(world)


async def test_a_shared_peer_removed_between_retrieve_and_commit_is_discarded(
    tmp_path, monkeypatch
):
    """Design §4.5 race test: no stale matched_projects is ever emitted."""
    world = await _world(tmp_path, monkeypatch)
    try:
        conn = world["conn"]
        seed_second_project(conn)
        conn.execute("UPDATE policy_state SET include_archived = 1")
        conn.commit()

        def search_then_unshare(request):
            conn.execute(
                "DELETE FROM project_peers WHERE project_id = 2 AND peer_id ="
                " (SELECT id FROM peers WHERE telegram_peer_id = 7)"
            )
            conn.commit()
            return _hits(request, {"channel:7": [5]})

        world["fake"].script["messages.SearchRequest"] = search_then_unshare
        before = _receipts(world)
        body = await call(
            world,
            CODEX,
            "telegram_cross_project_search",
            {"project_refs": [PROJECT_REF, BETA_REF], "query": "needle"},
        )
        assert body["ok"] is False and body["error"]["code"] == "POLICY_CHANGED", body
        assert _receipts(world) == before
    finally:
        await _close(world)
```

Apply to `scripts/e2e_smoke.py`:

```diff
--- a/scripts/e2e_smoke.py
+++ b/scripts/e2e_smoke.py
@@ -1469,8 +1469,141 @@
     ledger.run(area, "get_unread total is exact over the project", unread_exact)
     ledger.run(area, "no message body at rest", no_body_at_rest)
     ledger.run(area, "only reviewed RPCs reached the transport", only_reviewed_calls)
+
+
+def phase4c_reads(ledger: Ledger) -> None:
+    """get_context and both searches through real ingress + coordinator, fake transport."""
+    area = "Phase 4c — context and search (fake transport)"
+    results: dict[str, Any] = {}
+
+    def drive() -> dict[str, Any]:
+        if results:
+            return results
+        import asyncio
+        import hashlib
+        import tempfile
+
+        sys.path.insert(0, str(REPO))
+        from telethon.tl import types
+
+        from telegram_mcp.consent.challenge import jcs_dumps
+        from telegram_mcp.storage.refstore import RefStore
+        from tests.authority_fixtures import BETA_REF, PROJECT_REF, seed_second_project
+        from tests.integration.test_phase4a_end_to_end import CODEX, call
+        from tests.integration.test_phase4b_end_to_end import _close, _world
+        from tests.integration.test_phase4c_end_to_end import BASE, _hits
+
+        class _Patch:  # _world only needs chdir from monkeypatch
+            def chdir(self, path: Any) -> None:
+                os.chdir(path)
+
+        async def main() -> dict[str, Any]:
+            here = os.getcwd()
+            out: dict[str, Any] = {}
+            with tempfile.TemporaryDirectory(dir="/tmp") as short:
+                world = await _world(Path(short), _Patch())
+                try:
+                    conn, fake = world["conn"], world["fake"]
+                    fake.script["messages.SearchRequest"] = lambda r: _hits(
+                        r, {"user:100": [3, 2, 1]}
+                    )
+                    body = await call(
+                        world,
+                        CODEX,
+                        "telegram_search_messages",
+                        {"project_ref": PROJECT_REF, "query": "needle"},
+                    )
+                    out["search"] = body
+                    if body.get("ok"):
+                        out["digest_ok"] = (
+                            body["meta"]["disclosure"]["proof_payload"]["canonical_coverage_digest"]
+                            == hashlib.sha256(jcs_dumps(body["meta"]["coverage"])).hexdigest()
+                        )
+                    refs = RefStore(conn, account_id=1)
+                    anchor = refs.message_ref(refs.peer_by_identity("user:100").row_id, 1)
+                    conn.commit()
+                    fake.script["messages.GetMessagesRequest"] = types.messages.Messages(
+                        messages=[
+                            types.Message(
+                                id=1, peer_id=types.PeerUser(100), date=BASE, message="anchor"
+                            )
+                        ],
+                        topics=[],
+                        chats=[],
+                        users=[],
+                    )
+                    out["context"] = await call(
+                        world,
+                        CODEX,
+                        "telegram_get_context",
+                        {
+                            "project_ref": PROJECT_REF,
+                            "message_ref": anchor,
+                            "before": 1,
+                            "after": 1,
+                        },
+                    )
+                    seed_second_project(conn)
+                    conn.execute("UPDATE policy_state SET include_archived = 1")
+                    conn.commit()
 
+                    def search_then_unshare(request):
+                        conn.execute(
+                            "DELETE FROM project_peers WHERE project_id = 2 AND peer_id ="
+                            " (SELECT id FROM peers WHERE telegram_peer_id = 7)"
+                        )
+                        conn.commit()
+                        return _hits(request, {"channel:7": [5]})
 
+                    fake.script["messages.SearchRequest"] = search_then_unshare
+                    before = conn.execute("SELECT count(*) FROM disclosure_receipts").fetchone()[0]
+                    out["race"] = await call(
+                        world,
+                        CODEX,
+                        "telegram_cross_project_search",
+                        {"project_refs": [PROJECT_REF, BETA_REF], "query": "needle"},
+                    )
+                    out["race_receipts"] = (
+                        conn.execute("SELECT count(*) FROM disclosure_receipts").fetchone()[0]
+                        - before
+                    )
+                    out["calls"] = set(fake.calls)
+                finally:
+                    await _close(world)
+                    os.chdir(here)
+            return out
+
+        results.update(asyncio.run(main()))
+        return results
+
+    def search_with_coverage():
+        out = drive()
+        body = out["search"]
+        assert body["ok"] and body["meta"]["coverage"]["complete"] is True, body
+        assert out["digest_ok"] is True
+        return f"{len(body['data']['results'])} hits, coverage complete, digest signed"
+
+    def context_window():
+        body = drive()["context"]
+        assert body["ok"] and body["meta"]["coverage"] is None, body
+        return f"anchor + {len(body['data']['messages']) - 1} neighbours, receipt"
+
+    def race_discarded():
+        out = drive()
+        assert out["race"]["error"]["code"] == "POLICY_CHANGED" and out["race_receipts"] == 0
+        return "shared peer removed mid-flight -> POLICY_CHANGED, no receipt"
+
+    def never_global():
+        calls = drive()["calls"]
+        assert "messages.SearchGlobalRequest" not in calls and "messages.SearchRequest" in calls
+        return "per-peer messages.Search only"
+
+    ledger.run(area, "search_messages with signed coverage", search_with_coverage)
+    ledger.run(area, "get_context through ingress", context_window)
+    ledger.run(area, "cross-project race discarded at step 8", race_discarded)
+    ledger.run(area, "never a global search", never_global)
+
+
 def main() -> int:
     parser = argparse.ArgumentParser(description="Phase 1 + Phase 2 end-to-end smoke")
     parser.add_argument("--verbose", action="store_true", help="print tracebacks for failures")
@@ -1490,6 +1623,7 @@
         phase2_consent(ledger)
         phase4a_catalogue(ledger)
         phase4b_reads(ledger)
+        phase4c_reads(ledger)
         conn = state.get("conn")
         if conn is not None:
             conn.close()
```

- [ ] **Step 2: Run them and watch them fail**

Run: `uv run pytest tests/integration/test_coordinator_coverage.py tests/unit/test_exposure_and_display.py tests/integration/test_phase4c_end_to_end.py -q`
Expected: the dishonest-coverage cases are released (the gate is missing); the display test finds no warning; the end-to-end calls answer `INTERNAL_ERROR` because the tools are not routed.
Observed in the staged proof: 8 failed, 6 passed in 7.29s; first error: `telegram_mcp.disclosure.receipts.ReceiptError: search tools require a coverage digest`.

- [ ] **Step 3: Implement**

Apply to `src/telegram_mcp/disclosure/coordinator.py`:

```diff
--- a/src/telegram_mcp/disclosure/coordinator.py
+++ b/src/telegram_mcp/disclosure/coordinator.py
@@ -27,7 +27,7 @@
     mint_event_id,
 )
 from telegram_mcp.disclosure.budget import BudgetError, buckets_for
-from telegram_mcp.disclosure.coverage import coverage_digest
+from telegram_mcp.disclosure.coverage import CoverageError, coverage_digest, validate_coverage
 from telegram_mcp.disclosure.egress import effective_egress_level
 from telegram_mcp.disclosure.measure import (
     RECORD_ELEMENT,
@@ -449,6 +449,20 @@
             coverage = side.get("_coverage") if tool_name in _SEARCH_TOOLS else None
             next_cursor = side.get("_next_cursor")
             partial = bool(snapshot.partial) or bool(side.get("_partial"))
+            if tool_name in _SEARCH_TOOLS:
+                # §23D and §14.1A: a search result must carry a coverage object
+                # that agrees with itself, with the cursor, with meta.partial and
+                # with the records actually disclosed; otherwise nothing is signed.
+                try:
+                    if coverage is None or coverage["hits_returned"] != records_disclosed(
+                        tool_name, data
+                    ):
+                        raise CoverageError("coverage does not describe this result")
+                    validate_coverage(coverage, next_cursor=next_cursor, partial=partial)
+                except (CoverageError, KeyError, TypeError):
+                    return DisclosureOutcome(
+                        released=False, error_code="PROOF_GENERATION_FAILED", retryable=False
+                    )
 
             self._checkpoint("measure_and_prepare_proof")
             prepared = self._prepare_proof(tool_name, data, snapshot, approval, coverage, partial)
```

Apply to `src/telegram_mcp/consent/display.py`:

```diff
--- a/src/telegram_mcp/consent/display.py
+++ b/src/telegram_mcp/consent/display.py
@@ -59,9 +59,15 @@
     if client_kind not in CLIENT_DISPLAY:
         raise ValueError("unknown client kind")
     warning = "ELEVATED " if tier == "elevated" else ""
+    # §21A.4: a cross-project prompt warns that results may enter the AI's context.
+    crossing = (
+        "results may enter this AI session's context; "
+        if tool_name == "telegram_cross_project_search"
+        else ""
+    )
     risk = (
-        f"{egress_level}; {warning}budget {current.records}->{projected.records} records,"
-        f" {current.bytes}->{projected.bytes} bytes"
+        f"{crossing}{egress_level}; {warning}budget {current.records}->{projected.records}"
+        f" records, {current.bytes}->{projected.bytes} bytes"
     )
     return {
         "action_display": ACTION_DISPLAY[tool_name],
```

Apply to `src/telegram_mcp/runtime/composition.py`:

```diff
--- a/src/telegram_mcp/runtime/composition.py
+++ b/src/telegram_mcp/runtime/composition.py
@@ -143,13 +143,21 @@
         "telegram_resolve_project": metadata.resolve_project,
     }
     if telegram is not None:
-        reads = TelegramReads(telegram, conn, mint_cursor=authority.mint_project_cursor)
+        reads = TelegramReads(
+            telegram,
+            conn,
+            mint_cursor=authority.mint_project_cursor,
+            mint_search_cursor=authority.mint_search_cursor,
+        )
         routes.update(
             {
                 "telegram_list_chats": reads.list_chats,
                 "telegram_resolve_peer": reads.resolve_peer,
                 "telegram_get_messages": reads.get_messages,
                 "telegram_get_unread": reads.get_unread,
+                "telegram_get_context": reads.get_context,
+                "telegram_search_messages": reads.search_messages,
+                "telegram_cross_project_search": reads.cross_project_search,
             }
         )
     routed = RoutedRetrieval(routes)
```

- [ ] **Step 4: Run them and watch them pass**

Run: `uv run pytest tests/integration/test_coordinator_coverage.py tests/unit/test_exposure_and_display.py tests/integration/test_phase4c_end_to_end.py -q`
Expected: 14 passed (observed in the staged proof).

- [ ] **Step 5: Commit** (format, check, full gate first)

Full gate expected at this task: suite 1294 passed, 10 skipped; smoke 53 passed, 0 failed, 0 skipped — 53 checks.

```bash
git add tests/integration/test_coordinator_coverage.py tests/unit/test_exposure_and_display.py tests/integration/test_phase4c_end_to_end.py scripts/e2e_smoke.py src/telegram_mcp/disclosure/coordinator.py src/telegram_mcp/consent/display.py src/telegram_mcp/runtime/composition.py
git commit -m "feat: refuse dishonest coverage and route the three 4c tools

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 8: Test DC harness for 4c, evidence and audit trail

Design §4.6.

**Harness.** The opt-in harness gains a forum with two named topics and General messages. The run then proves on the real server:
- topic isolation;
- General filtering;
- the `before=0` and `after=0` edges;
- real search exhaustion across the project.

All of that happens before the independent witness is re-read, so it covers the 4c tools too. The run needs the owner's credentials; here it is compile-checked and skipped.

**Evidence.** It records what ran, what is owner-pending, and the refinements.

**Files:**
- Modify: `tests/telegram/fixture_builder.py`
- Modify: `tests/telegram/test_testdc.py`

**Interfaces:**
- Produces:
  - the 4c evidence in `docs/verification/phase-4.md` and `telegram-rpc-review.md`

- [ ] **Step 1: Write the failing tests**

Apply to `tests/telegram/fixture_builder.py`:

```diff
--- a/tests/telegram/fixture_builder.py
+++ b/tests/telegram/fixture_builder.py
@@ -62,4 +62,30 @@
     post = await b.send_message(channel, f"post {marker}")
     await b(functions.channels.InviteToChannelRequest(channel, [a]))
     await b(functions.messages.EditChatTitleRequest(chat_id=group.id, title="4b group renamed"))
-    return {"a_id": a.id, "group_id": group.id, "channel_id": channel.id, "post_id": post.id}
+    forum = (
+        await b(
+            functions.channels.CreateChannelRequest(
+                title="4c forum", about="", megagroup=True, forum=True
+            )
+        )
+    ).chats[0]
+    await b(functions.channels.InviteToChannelRequest(forum, [a]))
+    topics = {}
+    for name in ("alpha", "beta"):
+        created = await b(
+            functions.messages.CreateForumTopicRequest(peer=forum, title=f"topic {name}")
+        )
+        topic_id = next(u.id for u in created.updates if hasattr(u, "id"))
+        topics[name] = topic_id
+        for i in range(3):
+            await b.send_message(forum, f"topic-{name} {i} {marker}", reply_to=topic_id)
+    for i in range(3):
+        await b.send_message(forum, f"general {i} {marker}")  # no reply_to: the General topic
+    return {
+        "a_id": a.id,
+        "group_id": group.id,
+        "channel_id": channel.id,
+        "post_id": post.id,
+        "forum_id": forum.id,
+        "topics": topics,
+    }
```

Apply to `tests/telegram/test_testdc.py`:

```diff
--- a/tests/telegram/test_testdc.py
+++ b/tests/telegram/test_testdc.py
@@ -90,7 +90,7 @@
         "project_ref"
     ]
     # allow + add every chat kind the fixture made: each is touched by the read tools
-    for kind in ("private", "group", "channel"):
+    for kind in ("private", "group", "channel", "supergroup"):  # supergroup: the 4c forum
         found = await scope["scope discover"]({})
         handle = next(s["handle"] for s in found["selections"] if s["chat_type"] == kind)
         scope["scope allow"]({"handle": handle})
@@ -116,7 +116,12 @@
         cursor_store=bind_cursor_store(conn),
         runtime_id=b"\x05" * 16,
     )
-    reads = TelegramReads(session, conn, mint_cursor=authority.mint_project_cursor)
+    reads = TelegramReads(
+        session,
+        conn,
+        mint_cursor=authority.mint_project_cursor,
+        mint_search_cursor=authority.mint_search_cursor,
+    )
     principal = resolve_principal(conn, client_ref)
 
     def snap(tool, **args):
@@ -146,6 +151,53 @@
         if chat["chat_type"] == "private":
             assert any(marker in (m["text"] or "") for m in page["messages"])
 
+    # ---- 4c: forum isolation, real search exhaustion, the before=0 window ----
+    forum = next(c for c in chats["chats"] if c["chat_type"] == "supergroup")
+    page = await reads.get_messages(
+        *snap("telegram_get_messages", peer_ref=forum["peer_ref"], limit=30)
+    )
+    by_text = {m["text"]: m["message_ref"] for m in page["messages"] if m["text"]}
+    for name, other in (("alpha", "beta"), ("beta", "alpha")):
+        anchor = by_text[f"topic-{name} 1 {marker}"]
+        ctx = await reads.get_context(
+            *snap("telegram_get_context", message_ref=anchor, before=5, after=5)
+        )
+        texts = [m["text"] or "" for m in ctx["messages"]]
+        assert all(f"topic-{other}" not in t and "general" not in t for t in texts), texts
+        assert f"topic-{name} 0 {marker}" in texts and f"topic-{name} 2 {marker}" in texts
+    general = await reads.get_context(
+        *snap("telegram_get_context", message_ref=by_text[f"general 1 {marker}"], before=5, after=5)
+    )
+    assert all("topic-" not in (m["text"] or "") for m in general["messages"])
+    for before, after in (
+        (0, 0),
+        (0, 2),
+        (2, 0),
+    ):  # design §4.1: the edge cases, on the real server
+        ctx = await reads.get_context(
+            *snap(
+                "telegram_get_context",
+                message_ref=by_text[f"general 1 {marker}"],
+                before=before,
+                after=after,
+            )
+        )
+        assert ctx["anchor_message_ref"] in [m["message_ref"] for m in ctx["messages"]]
+    found, cursor = [], None
+    for _page in range(20):  # real search exhaustion across the whole project
+        extra = {"cursor": cursor} if cursor else {}
+        out = await reads.search_messages(
+            *snap("telegram_search_messages", query=marker, limit=50, **extra)
+        )
+        found += [r["message_ref"] for r in out["results"]]
+        cursor = out.get("_next_cursor")
+        if cursor is None:
+            assert out["_coverage"]["complete"] is True, out["_coverage"]
+            break
+    assert (
+        len(found) == len(set(found)) and len(found) >= 12
+    )  # 3+3 topic, 3 General, DM, group, post
+
     # Independent: markers Telegram reports from B's side (DM, group, channel views).
     assert witness() == before, "a read tool moved a read marker or a view counter"
     # Not independent (read through the gateway itself), recorded as such: A's unread counts.
```

- [ ] **Step 2: Run them and watch them fail**

Run: `uv run pytest tests/telegram -q`
Expected: not applicable: the Test DC test stays skipped without `--run-telegram-testdc`.
Observed in the staged proof: 1 skipped in 1.61s.

- [ ] **Step 4: Run them and watch them pass**

Run: `uv run pytest tests/telegram -q`
Expected: 1 skipped (observed in the staged proof).

- [ ] **Step 5: Commit** (format, check, full gate first)

Full gate expected at this task: suite 1294 passed, 10 skipped; smoke 53 passed, 0 failed, 0 skipped — 53 checks.

```bash
git add tests/telegram/fixture_builder.py tests/telegram/test_testdc.py
git commit -m "test: extend the Test DC harness for 4c and record the evidence

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 8 (continued): evidence and audit trail

After the harness commit above, write the evidence. None of it is code, but all of it is checked by the final gate.

- [ ] **Step 6: The two new RPC reviews**

Add these rows to the table in `docs/verification/telegram-rpc-review.md` (operation `mcp.retrieval`). `messages.SearchGlobalRequest` stays in its "Absent in every phase" list:

```markdown
| `mcp.retrieval` | `messages.SearchRequest` | https://core.telegram.org/method/messages.search | none | One peer per request, `InputMessagesFilterEmpty`; `min_date`/`max_date` strict, 0 = unbounded (design §4.2). Never `messages.searchGlobal`. |
| `mcp.retrieval` | `messages.GetRepliesRequest` | https://core.telegram.org/method/messages.getReplies | none | The thread of one forum topic for `get_context`; it cannot cross into another topic (design §4.1). |
```

- [ ] **Step 7: Phase 4c evidence**

In `docs/verification/phase-4.md`, update the status paragraph to say that 4c is implemented on branch `phase-4c`. Then append a **Phase 4c** section with the same structure as 4b's:

- **4c.1 Scope, as delivered.** The three tools; the revision-5 refinements (design §4.7), each named.
- **4c.2 Evidence.** The test table from each task's Review Focus lines, the smoke's four Phase-4c rows, and the staged proof below.
- **4c.3 Control runs.** The estimator weakening, run in Task 6.
- **4c.4 Owner-pending.** The Test DC run, now with forum isolation, the `before=0` edges and real search exhaustion; the dedicated-account re-run (§4.6); and everything carried from 4b's §4b.4.
- **4c.5 Gate contributions, all still PARTIAL:**
  - **E:** cross-project isolation and per-record egress.
  - **O:** signed coverage.
  - **Q:** the continuation state machine.
  - **R:** two more reviewed reads, and still no global search.

Update the reproducibility block with the counts the final gate prints.

- [ ] **Step 8: CLAUDE.md, AGENT.md, CHANGELOG.md**

In `CLAUDE.md`, "Where the work stands": state that all nine sensitive tools are served on the fake transport, and that `get_context` and both searches are implemented. Update the `pytest`/smoke counts in "Verify". Append a dated `**Raouf:**` entry to `AGENT.md` and `CHANGELOG.md` with Scope, Summary, Files changed, Verification (the exact counts) and Follow-ups.

- [ ] **Step 9: Full gate and commit**

Run the full gate from `CLAUDE.md`, each command on its own, reading each exit status. Expected: all green, with the smoke at 53 checks and the Test DC test skipped.

```bash
git add docs/verification CLAUDE.md AGENT.md CHANGELOG.md
git commit -m "docs: record the phase-4c evidence

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

## Staged proof

The eight tasks were applied in order to a fresh export of `main` at `cd1434e`. At each stage the proof ran:
- the task's own tests;
- `ruff check`, `ruff format --check`, `mypy`;
- the full suite (with `test_session_files_ignored.py` deselected, because the export is not a git repository; that test was run in the real repository and passed);
- the smoke.

| Task | own tests | ruff | format | mypy | full suite | smoke |
|---|---|---|---|---|---|---|
| 1 cursor state | 40 passed | All checks passed! | 186 files already formatted | Success: no issues found in 85 source files | 869 passed, 10 skipped, 4 deselected | 49 passed, 0 failed, 0 skipped — 49 checks |
| 2 adapter | 20 passed | All checks passed! | 187 files already formatted | Success: no issues found in 85 source files | 876 passed, 10 skipped, 4 deselected | 49 passed, 0 failed, 0 skipped — 49 checks |
| 3 bounds | 13 passed | All checks passed! | 188 files already formatted | Success: no issues found in 85 source files | 879 passed, 10 skipped, 4 deselected | 49 passed, 0 failed, 0 skipped — 49 checks |
| 4 engine | 361 passed | All checks passed! | 190 files already formatted | Success: no issues found in 86 source files | 1240 passed, 10 skipped, 4 deselected | 49 passed, 0 failed, 0 skipped — 49 checks |
| 5 authority | 30 passed | EXE001 at first (harness `shutil.copy` took mode 644 from its source file; git keeps 100755); after restoring the mode: All checks passed! | 192 files already formatted | Success: no issues found in 87 source files | 1253 passed, 10 skipped, 4 deselected | 49 passed, 0 failed, 0 skipped — 49 checks |
| 6 reads | 43 passed | All checks passed! | 195 files already formatted | Success: no issues found in 87 source files | 1281 passed, 10 skipped, 4 deselected | 49 passed, 0 failed, 0 skipped — 49 checks |
| 7 wiring | 14 passed | All checks passed! | 197 files already formatted | Success: no issues found in 87 source files | 1290 passed, 10 skipped, 4 deselected | 53 passed, 0 failed, 0 skipped — 53 checks |
| 8 testdc | 1 skipped | All checks passed! | 197 files already formatted | Success: no issues found in 87 source files | 1290 passed, 10 skipped, 4 deselected | 53 passed, 0 failed, 0 skipped — 53 checks |

## Owner steps (not the executor's)

1. The Test DC run, now covering 4c. See 4b's owner steps for the Keychain item and the `TG_TESTDC_*` variables. Then run: `uv run pytest tests/telegram/test_testdc.py --run-telegram-testdc -q -s`
2. The dedicated non-primary account re-run of the same harness (§4.6 qualification).
3. Carried from 4b: the Touch ID test, the installed-host boundary check, and re-packaging the signed agent bundle.
