# Telegram MCP Phase 3a — Disclosure Machinery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the deterministic half of Phase 3 — measurement, egress transformation, provenance, search coverage, Appendix-K receipts and their offline verifier — so that a signed disclosure receipt can be produced and independently verified with no database and no Telegram.

**Architecture:** A new `src/telegram_mcp/disclosure/` package of pure, side-effect-free modules. Every module takes already-retrieved data and returns values; none of them opens a socket, writes a row or decides policy. `measure.py` is the single measurement authority that Plans 3b and 3c both import — a second copy of that arithmetic is the defect this package exists to prevent. Nothing here is wired into a tool yet; Plan 3c composes it.

**Tech Stack:** Python 3.12, `cryptography` (Ed25519), stdlib `sqlite3`, `pytest`, `uv`. The JCS encoder, the opaque-ref minter and the key store already exist and are imported, never reimplemented.

**Spec:** `docs/superpowers/specs/2026-09-22-telegram-mcp-phase-3-design.md` (revision 3). Read §3, §4, §5.1, §5.2 and §8 before starting. The frozen contract it implements is `telegram-mcp-v0.1.10-final-engineering-spec.md` §23A, §23B, §23D and Appendix K.

## Global Constraints

- **Never commit or log Telegram credentials, session files, keys or private material.** Private key halves never enter SQLite, logs or any exported artifact.
- **No production claim** until Gates A–R pass. Phase 3a moves parts of Gate O only.
- **Fail closed.** A check that cannot be performed reports that it was skipped; it never reports success.
- **No test-only flags in production paths.** Headless testing goes through injected seams or separate `selftest-` subcommands, never a branch inside a real path.
- **One copy of each shared rule.** `jcs_dumps` lives in `telegram_mcp.consent.challenge`; `mint_opaque_ref` lives in `telegram_mcp.opaque`. Import them. A second copy is the defect.
- **Message text is never sanitised.** The Phase-2b display sanitiser must never touch a Telegram message body.
- **Uniform `ValueError` on validation**, with `# noqa: TRY004 -- <reason>` where ruff objects.
- **Key ids** are `<kind>:sha256:<64 hex>`, recomputed on load, never stored.
- **Read `AGENT.md` and `CHANGELOG.md` before editing**, and append a dated `**Raouf:**` entry to both afterwards with Scope, Summary, Files changed, Verification and Follow-ups.
- Verification gate for every task: `uv run pytest -q`, `uv run ruff check src tests scripts`, `uv run ruff format --check src tests scripts`, `uv run mypy src/telegram_mcp`.

## File Structure

| File | Responsibility |
|---|---|
| `src/telegram_mcp/disclosure/__init__.py` | package marker, no logic |
| `src/telegram_mcp/disclosure/measure.py` | THE measurement authority: records, global bytes, per-project bytes, container bytes |
| `src/telegram_mcp/disclosure/egress.py` | `metadata_only` / `excerpt` / `full_text` transformation and profile intersection |
| `src/telegram_mcp/disclosure/provenance.py` | the ordered provenance vector and its digest |
| `src/telegram_mcp/disclosure/coverage.py` | the §23D coverage object, its invariants and its digest |
| `src/telegram_mcp/disclosure/receipts.py` | Appendix-K payload construction, signing, and the offline verifier |
| `src/telegram_mcp/disclosure/keys.py` | `verification_keys` registry rows: publish, look up current and historical |
| `tests/unit/test_disclosure_measure.py` | measurement, including the cross-project trap |
| `tests/unit/test_disclosure_egress.py` | transformation determinism over a Unicode/RTL corpus |
| `tests/unit/test_disclosure_provenance.py` | vector shape, ordering sensitivity, no text |
| `tests/unit/test_disclosure_coverage.py` | the invariant chain and the dedup direction |
| `tests/unit/test_disclosure_receipts.py` | payload shape, signature, tamper rejection |
| `tests/security/test_offline_verifier.py` | verification in a process with no database handle |

---

### Task 1: Provision the three Phase-3 keys

The key registry already carries `disclosure-key`, `audit-checkpoint-key` and `audit-chain-key` with `required_phase=3`, but `provision_missing` only creates names listed in `_FILE_BACKED_ROWS`, which holds the four Phase-2 rows. Provisioning silently skips all three today regardless of the `phases` argument.

**Files:**
- Modify: `src/telegram_mcp/keys/store.py:59-61` (`_FILE_BACKED_ROWS`, `FILE_BACKED_KEYS`)
- Test: `tests/unit/test_keys.py`

**Interfaces:**
- Consumes: `KEY_REGISTRY` from `telegram_mcp.keys.registry`; `provision_missing(store_dir, *, phases)`, `load_key(name)`, `key_id(name)` from `telegram_mcp.keys.store`.
- Produces: `provision_missing(d, phases=(2, 3))` creates seven `0600` files; `key_id("disclosure-key")` returns `ed25519:sha256:<64 hex>`; `key_id("audit-chain-key")` returns `hmac:sha256:<64 hex>`.

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/test_keys.py`:

```python
def test_phase_three_rows_are_provisioned_and_have_distinct_ids(tmp_path):
    from telegram_mcp.keys.store import key_id, provision_missing

    created = provision_missing(tmp_path, phases=(2, 3))

    assert {"disclosure-key", "audit-checkpoint-key", "audit-chain-key"} <= set(created)
    assert (tmp_path / "disclosure-key").stat().st_mode & 0o777 == 0o600
    assert key_id("disclosure-key").startswith("ed25519:sha256:")
    assert key_id("audit-chain-key").startswith("hmac:sha256:")
    # Purpose separation: three rows, three distinct recomputed ids.
    ids = {key_id(n) for n in ("disclosure-key", "audit-checkpoint-key", "audit-chain-key")}
    assert len(ids) == 3


def test_phase_two_only_provisioning_still_skips_phase_three(tmp_path):
    from telegram_mcp.keys.store import provision_missing

    created = provision_missing(tmp_path, phases=(2,))

    assert "disclosure-key" not in created
    assert not (tmp_path / "disclosure-key").exists()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_keys.py -k phase_three -v`
Expected: FAIL — `assert {...} <= set(created)` is false because `created` contains only Phase-2 names.

- [ ] **Step 3: Write minimal implementation**

In `src/telegram_mcp/keys/store.py`, replace the `_FILE_BACKED_ROWS` definition:

```python
# Rows provisioned as private files by ``provision_missing``: the Phase-2
# file-backed runtime-account secrets, plus the three Phase-3 rows that sign
# and chain disclosures. Tunnel references come from install, agent keys live
# in the keychain/Enclave (daemon keeps pins only), and per-client lease seeds
# are minted on demand via ``provision_lease_seed``.
_FILE_BACKED_ROWS = frozenset(
    {
        "principal-key",
        "cursor-key",
        "privacy-key",
        "challenge-key",
        "disclosure-key",
        "audit-checkpoint-key",
        "audit-chain-key",
    }
)
```

`provision_missing` already filters on `spec.required_phase not in wanted`, so the Phase-3 rows appear only when `3` is in `phases`. No other change is needed.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_keys.py -v`
Expected: PASS, including the pre-existing doctor tests that read `FILE_BACKED_KEYS`.

- [ ] **Step 5: Confirm doctor still agrees**

Run: `uv run pytest tests/unit/test_doctor.py -q`
Expected: PASS. `doctor` checks `FILE_BACKED_KEYS` as "runtime-owned files"; with no Phase-3 store provisioned it reports them missing, which is the honest answer, not a failure of the check.

- [ ] **Step 6: Commit**

```bash
git add src/telegram_mcp/keys/store.py tests/unit/test_keys.py
git commit -m "feat: provision the three Phase-3 key rows"
```

---

### Task 2: The measurement authority

Gate P requires that measurement be identical between prompts, reservations, ledger rows and receipts. There are four call sites, so there is exactly one module.

**Files:**
- Create: `src/telegram_mcp/disclosure/__init__.py`, `src/telegram_mcp/disclosure/measure.py`
- Test: `tests/unit/test_disclosure_measure.py`

**Interfaces:**
- Consumes: `jcs_dumps` from `telegram_mcp.consent.challenge`.
- Produces:
  - `RECORD_ELEMENT: dict[str, str]` — tool name to the `data` member holding records.
  - `records_disclosed(tool_name: str, data: Mapping[str, Any]) -> int`
  - `bytes_disclosed(data: Mapping[str, Any]) -> int`
  - `project_bytes(tool_name: str, data: Mapping[str, Any]) -> dict[str, int]`
  - `container_bytes(tool_name: str, data: Mapping[str, Any]) -> int`
  - `MeasurementError(Exception)`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_disclosure_measure.py`:

```python
"""The single measurement authority (design §5.1, §5.2; Gate P)."""

import pytest

from telegram_mcp.disclosure.measure import (
    RECORD_ELEMENT,
    MeasurementError,
    bytes_disclosed,
    container_bytes,
    project_bytes,
    records_disclosed,
)

_CROSS = {
    "projects": [{"project_ref": "tpr_" + "a" * 26}, {"project_ref": "tpr_" + "b" * 26}],
    "search_scope": {"mode": "cross_project"},
    "results": [
        {"message_ref": "tgm_" + "a" * 26, "origin_project_refs": ["tpr_" + "a" * 26]},
        {
            "message_ref": "tgm_" + "b" * 26,
            "origin_project_refs": ["tpr_" + "a" * 26, "tpr_" + "b" * 26],
        },
    ],
}


def test_record_element_is_read_from_the_frozen_contracts():
    # resolve_* emit matches[], get_unread emits chats[]; guessing peers[]
    # or unread[] would count nothing at all.
    assert RECORD_ELEMENT["telegram_resolve_peer"] == "matches"
    assert RECORD_ELEMENT["telegram_resolve_project"] == "matches"
    assert RECORD_ELEMENT["telegram_get_unread"] == "chats"
    # cross-project search also carries a projects[] array that is scope
    # metadata, not records.
    assert RECORD_ELEMENT["telegram_cross_project_search"] == "results"
    assert "telegram_status" not in RECORD_ELEMENT


def test_records_disclosed_counts_the_record_element_only():
    assert records_disclosed("telegram_cross_project_search", _CROSS) == 2


def test_status_is_not_measurable():
    with pytest.raises(MeasurementError):
        records_disclosed("telegram_status", {"connected": False})


def test_bytes_disclosed_is_canonical_bytes_of_data():
    assert bytes_disclosed({"a": 1}) == len(b'{"a":1}')


def test_shared_record_is_charged_to_every_contributing_project():
    per = project_bytes("telegram_cross_project_search", _CROSS)
    a, b = "tpr_" + "a" * 26, "tpr_" + "b" * 26
    assert set(per) == {a, b}
    # The two-origin record is counted whole in BOTH buckets. Assert that
    # directly: the sum of project bytes exceeds the sum of record bytes.
    # Do NOT compare against bytes_disclosed -- the global figure also
    # carries container overhead (projects[], search_scope), so the
    # comparison can go either way and would fail on this very fixture.
    from telegram_mcp.consent.challenge import jcs_dumps

    record_bytes = sum(len(jcs_dumps(r)) for r in _CROSS["results"])
    assert sum(per.values()) > record_bytes
    assert per[a] > per[b]  # the shared record lands in a as well as b


def test_container_overhead_is_global_and_reconciles_for_one_project():
    data = {
        "project": {"project_ref": "tpr_" + "c" * 26},
        "messages": [{"message_ref": "tgm_" + "c" * 26, "origin_project_refs": ["tpr_" + "c" * 26]}],
    }
    per = project_bytes("telegram_get_messages", data)
    assert sum(per.values()) + container_bytes("telegram_get_messages", data) == bytes_disclosed(data)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_disclosure_measure.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'telegram_mcp.disclosure'`.

- [ ] **Step 3: Write minimal implementation**

Create `src/telegram_mcp/disclosure/__init__.py`:

```python
"""Phase-3 disclosure machinery: measurement, egress, provenance, receipts."""
```

Create `src/telegram_mcp/disclosure/measure.py`:

```python
"""The single measurement authority (design §5.1; frozen spec §23C.2).

Gate P requires that exposure measurement be identical between prompts,
reservations, ledger rows and receipts. Those are four call sites, so the
arithmetic lives here once and is imported. A second copy is the defect.

Every function takes the ``data`` object *after* egress transformation and
*before* the ``meta``/proof envelope, which is what §23C.2 measures.
``meta`` — including ``coverage`` and the proof envelope — is outside the
measurement entirely.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from telegram_mcp.consent.challenge import jcs_dumps

__all__ = [
    "RECORD_ELEMENT",
    "MeasurementError",
    "bytes_disclosed",
    "container_bytes",
    "project_bytes",
    "records_disclosed",
]

# Read off the frozen contracts in src/telegram_mcp/contracts/*.data.json,
# not inferred from tool names: the resolve tools emit ``matches``, unread
# emits ``chats``, and cross-project search carries a ``projects`` array
# that is scope metadata rather than records.
RECORD_ELEMENT: dict[str, str] = {
    "telegram_list_projects": "projects",
    "telegram_resolve_project": "matches",
    "telegram_list_chats": "chats",
    "telegram_resolve_peer": "matches",
    "telegram_get_unread": "chats",
    "telegram_get_messages": "messages",
    "telegram_get_context": "messages",
    "telegram_search_messages": "results",
    "telegram_cross_project_search": "results",
}


class MeasurementError(Exception):
    """Measurement was requested for something that is not accounted."""


def _element(tool_name: str) -> str:
    key = RECORD_ELEMENT.get(tool_name)
    if key is None:
        # telegram_status is the sole non-sensitive tool and is never
        # exposure-accounted (spec §23C.2).
        raise MeasurementError("tool is not exposure-accounted")
    return key


def _records(tool_name: str, data: Mapping[str, Any]) -> Sequence[Mapping[str, Any]]:
    records = data.get(_element(tool_name), [])
    if not isinstance(records, Sequence) or isinstance(records, str | bytes):
        raise MeasurementError("record element is not a sequence")  # noqa: TRY004 -- uniform ValueError-style seam
    return records


def records_disclosed(tool_name: str, data: Mapping[str, Any]) -> int:
    """Count the records actually present in ``data`` for this tool."""
    return len(_records(tool_name, data))


def bytes_disclosed(data: Mapping[str, Any]) -> int:
    """UTF-8 length of the canonical JSON encoding of ``data``."""
    return len(jcs_dumps(data))


def project_bytes(tool_name: str, data: Mapping[str, Any]) -> dict[str, int]:
    """Canonical bytes attributed to each contributing project.

    A record with several origins is counted **whole** in every contributing
    bucket, so the sum of project bytes may exceed ``bytes_disclosed``. That
    over-count is deliberate: it is what stops an attacker cycling projects
    to dilute the per-project ceiling.
    """
    totals: dict[str, int] = {}
    for record in _records(tool_name, data):
        size = len(jcs_dumps(record))
        origins = record.get("origin_project_refs") or []
        for project_ref in origins:
            totals[project_ref] = totals.get(project_ref, 0) + size
    return totals


def container_bytes(tool_name: str, data: Mapping[str, Any]) -> int:
    """``data`` bytes that are not records: charged to the global bucket only.

    For a single-origin response this reconciles exactly:
    ``sum(project_bytes) + container_bytes == bytes_disclosed``.
    """
    records_size = sum(len(jcs_dumps(r)) for r in _records(tool_name, data))
    return bytes_disclosed(data) - records_size
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_disclosure_measure.py -v`
Expected: PASS, 6 tests.

- [ ] **Step 5: Verify the reconciliation identity is not an accident**

Run: `uv run python -c "
from telegram_mcp.disclosure.measure import bytes_disclosed, container_bytes, project_bytes
d = {'project': {'project_ref': 'tpr_' + 'c'*26},
     'messages': [{'message_ref': 'tgm_' + 'c'*26, 'origin_project_refs': ['tpr_' + 'c'*26]},
                  {'message_ref': 'tgm_' + 'd'*26, 'origin_project_refs': ['tpr_' + 'c'*26]}]}
print(sum(project_bytes('telegram_get_messages', d).values()), container_bytes('telegram_get_messages', d), bytes_disclosed(d))
"`
Expected: the first two numbers sum to the third.

- [ ] **Step 6: Commit**

```bash
git add src/telegram_mcp/disclosure/ tests/unit/test_disclosure_measure.py
git commit -m "feat: add the single exposure measurement authority"
```

---

### Task 3: Egress transformation

**Files:**
- Create: `src/telegram_mcp/disclosure/egress.py`
- Test: `tests/unit/test_disclosure_egress.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces:
  - `EGRESS_ORDER: tuple[str, ...]` = `("metadata_only", "excerpt", "full_text")`
  - `intersect_profiles(profiles: Sequence[tuple[str, int | None]]) -> tuple[str, int | None]`
  - `transform_record(record: Mapping[str, Any], level: str, excerpt_max: int | None) -> dict[str, Any]`
  - `effective_egress_level(records: Sequence[Mapping[str, Any]]) -> str`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_disclosure_egress.py`:

```python
"""Egress transformation (design §3.1; frozen spec §23B.2)."""

import pytest

from telegram_mcp.disclosure.egress import (
    effective_egress_level,
    intersect_profiles,
    transform_record,
)

# Codepoints, not bytes: an RTL string plus an astral emoji.
_RTL = "مرحبا \U0001f600 hello world"


def test_most_restrictive_profile_and_smallest_excerpt_win():
    assert intersect_profiles([("full_text", None), ("excerpt", 200)]) == ("excerpt", 200)
    assert intersect_profiles([("excerpt", 200), ("excerpt", 64)]) == ("excerpt", 64)
    assert intersect_profiles([("excerpt", 64), ("metadata_only", None)]) == ("metadata_only", None)


def test_metadata_only_drops_text_and_never_flags_truncation():
    out = transform_record({"message_ref": "tgm_" + "a" * 26, "text": _RTL}, "metadata_only", None)
    assert out["text"] is None
    assert out["text_truncated"] is False


def test_excerpt_truncates_by_codepoints_and_flags_it():
    out = transform_record({"message_ref": "tgm_" + "a" * 26, "text": _RTL}, "excerpt", 8)
    assert out["text"] == _RTL[:8]
    assert len(out["text"]) == 8
    assert out["text_truncated"] is True


def test_excerpt_shorter_than_limit_is_not_truncated():
    out = transform_record({"message_ref": "tgm_" + "a" * 26, "text": "hi"}, "excerpt", 64)
    assert out["text"] == "hi"
    assert out["text_truncated"] is False


def test_transformation_never_sanitises_message_text():
    # Bidi and C0 controls survive verbatim: sanitising a body would corrupt
    # the evidence the model reads (design §3.1).
    # Built with chr() rather than a literal: ruff's PLE2502 trojan-source
    # rule rejects a bidi control in source, and it is right to -- the point
    # here is that the transformer passes the character through, not that
    # this file contains one.
    hostile = "a" + chr(0x202E) + "b" + chr(0x07) + "c"
    out = transform_record({"message_ref": "tgm_" + "a" * 26, "text": hostile}, "full_text", None)
    assert out["text"] == hostile


def test_transformation_is_deterministic():
    record = {"message_ref": "tgm_" + "a" * 26, "text": _RTL}
    assert transform_record(record, "excerpt", 5) == transform_record(record, "excerpt", 5)


def test_effective_level_is_what_is_present_not_what_was_permitted():
    # Every record came back metadata-only under a full_text grant.
    records = [{"text": None, "text_truncated": False}, {"text": None, "text_truncated": False}]
    assert effective_egress_level(records) == "metadata_only"
    assert effective_egress_level([{"text": "x", "text_truncated": True}]) == "excerpt"
    assert effective_egress_level([{"text": "x", "text_truncated": False}]) == "full_text"
    assert effective_egress_level([]) == "metadata_only"


def test_unknown_level_is_rejected():
    with pytest.raises(ValueError):
        transform_record({"text": "x"}, "everything", None)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_disclosure_egress.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'telegram_mcp.disclosure.egress'`.

- [ ] **Step 3: Write minimal implementation**

Create `src/telegram_mcp/disclosure/egress.py`:

```python
"""Egress transformation (frozen spec §23B.2; design §3.1).

Runs after retrieval and revalidation, before accounting, signing and
serialisation. Deterministic, and it never summarises.

**Message text is never sanitised here.** The Phase-2b display sanitiser
strips C0/C1 and bidi controls so a consent prompt cannot be spoofed;
running it over a Telegram body would corrupt the evidence the model is
reading. The only content change this module makes is excerpt truncation.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

__all__ = [
    "EGRESS_ORDER",
    "effective_egress_level",
    "intersect_profiles",
    "transform_record",
]

# metadata_only < excerpt < full_text (spec §23B.2).
EGRESS_ORDER: tuple[str, ...] = ("metadata_only", "excerpt", "full_text")
_RANK = {level: index for index, level in enumerate(EGRESS_ORDER)}


def _check_level(level: str) -> str:
    if level not in _RANK:
        raise ValueError("unknown egress level")
    return level


def intersect_profiles(
    profiles: Sequence[tuple[str, int | None]],
) -> tuple[str, int | None]:
    """Most restrictive level wins; among excerpts the smallest limit wins."""
    if not profiles:
        raise ValueError("no egress profiles to intersect")
    level = min((_check_level(p[0]) for p in profiles), key=lambda name: _RANK[name])
    if level != "excerpt":
        return level, None
    limits = [p[1] for p in profiles if p[0] == "excerpt" and p[1] is not None]
    if not limits:
        raise ValueError("excerpt profile without a limit")
    return level, min(limits)


def transform_record(
    record: Mapping[str, Any], level: str, excerpt_max: int | None
) -> dict[str, Any]:
    """Return a new record carrying only the authorised content class."""
    _check_level(level)
    out = dict(record)
    text = out.get("text")

    if level == "metadata_only":
        out["text"] = None
        out["text_truncated"] = False
        return out

    if level == "excerpt":
        if excerpt_max is None:
            raise ValueError("excerpt level requires a limit")
        if isinstance(text, str) and len(text) > excerpt_max:
            # Codepoints, not bytes, not grapheme clusters (spec §23B.2).
            out["text"] = text[:excerpt_max]
            out["text_truncated"] = True
        else:
            out["text_truncated"] = bool(out.get("text_truncated", False))
        return out

    out["text_truncated"] = bool(out.get("text_truncated", False))
    return out


def effective_egress_level(records: Sequence[Mapping[str, Any]]) -> str:
    """The actual maximum content class present, not the grant's ceiling.

    A response that happens to carry only metadata is ``metadata_only`` even
    under a ``full_text`` grant (spec §23B.2).
    """
    level = "metadata_only"
    for record in records:
        if record.get("text") is None:
            continue
        level = "excerpt" if record.get("text_truncated") else "full_text"
        if level == "full_text":
            return level
    return level
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_disclosure_egress.py -v`
Expected: PASS, 8 tests.

- [ ] **Step 5: Prove the sanitiser is not reachable from here**

Run: `uv run python -c "
import inspect, telegram_mcp.disclosure.egress as e
src = inspect.getsource(e)
assert 'sanit' not in src.lower().replace('never sanitised', '').replace('display sanitiser', ''), 'sanitiser reached egress'
print('egress does not sanitise')
"`
Expected: `egress does not sanitise`.

- [ ] **Step 6: Commit**

```bash
git add src/telegram_mcp/disclosure/egress.py tests/unit/test_disclosure_egress.py
git commit -m "feat: add egress transformation and profile intersection"
```

---

### Task 4: Provenance vector and digest

§23A.2A calls this "the ordered set of privacy-safe record identities", which is a contradiction in terms. Design §3.2 reads it as an ordered vector in emitted-record order with four frozen fields.

**Files:**
- Create: `src/telegram_mcp/disclosure/provenance.py`
- Test: `tests/unit/test_disclosure_provenance.py`

**Interfaces:**
- Consumes: `jcs_dumps` from `telegram_mcp.consent.challenge`; `effective_egress_level` from Task 3.
- Produces:
  - `provenance_vector(tool_name: str, data: Mapping[str, Any]) -> list[dict[str, Any]]`
  - `provenance_digest(tool_name: str, data: Mapping[str, Any]) -> str` — lowercase hex SHA-256.

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_disclosure_provenance.py`:

```python
"""Provenance vector and digest (design §3.2; frozen spec §23A.2A)."""

from telegram_mcp.disclosure.provenance import provenance_digest, provenance_vector

_A, _B = "tpr_" + "a" * 26, "tpr_" + "b" * 26


def _data(texts):
    return {
        "results": [
            {
                "message_ref": f"tgm_{chr(97 + i) * 26}",
                "origin_project_refs": [_B, _A],  # deliberately unsorted
                "text": text,
                "text_truncated": text is not None,
            }
            for i, text in enumerate(texts)
        ]
    }


def test_vector_carries_exactly_the_four_frozen_fields():
    vector = provenance_vector("telegram_search_messages", _data(["hello"]))
    # sorted(), not list(): the dict is insertion-ordered and JCS sorts the
    # keys when the digest is taken, so the entry's construction order is
    # not what the contract fixes -- its field set is.
    assert sorted(vector[0]) == ["egress_level", "origin_project_refs", "record_ref", "text_truncated"]


def test_origin_project_refs_are_sorted_ascending():
    vector = provenance_vector("telegram_search_messages", _data(["hello"]))
    assert vector[0]["origin_project_refs"] == sorted([_A, _B])


def test_digest_never_hashes_message_text():
    one = provenance_digest("telegram_search_messages", _data(["alpha"]))
    two = provenance_digest("telegram_search_messages", _data(["omega"]))
    # Same refs, same truncation state, different bodies: same digest. The
    # digest commits to identity and provenance, never to content.
    assert one == two


def test_digest_is_sensitive_to_emitted_order():
    forward = _data(["a", "b"])
    reversed_ = {"results": list(reversed(forward["results"]))}
    assert provenance_digest("telegram_search_messages", forward) != provenance_digest(
        "telegram_search_messages", reversed_
    )


def test_digest_is_lowercase_hex_sha256():
    digest = provenance_digest("telegram_search_messages", _data(["x"]))
    assert len(digest) == 64
    assert digest == digest.lower()
    int(digest, 16)


def test_metadata_only_response_proves_no_body_was_emitted():
    data = {"results": [{"message_ref": "tgm_" + "a" * 26, "origin_project_refs": [_A], "text": None, "text_truncated": False}]}
    vector = provenance_vector("telegram_search_messages", data)
    assert vector[0]["egress_level"] == "metadata_only"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_disclosure_provenance.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'telegram_mcp.disclosure.provenance'`.

- [ ] **Step 3: Write minimal implementation**

Create `src/telegram_mcp/disclosure/provenance.py`:

```python
"""Privacy-safe provenance vector and digest (spec §23A.2A; design §3.2).

§23A.2A says the digest commits to "the ordered set of privacy-safe record
identities", which cannot be taken literally — a set has no order, and two
implementations would canonicalise it differently. This module reads it as
an ordered vector in **emitted-record order**, one entry per record actually
present in ``data``, each carrying exactly four fields.

It binds what leaves, not what Telegram returned, and it never hashes
message or search text.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from typing import Any

from telegram_mcp.consent.challenge import jcs_dumps
from telegram_mcp.disclosure.egress import effective_egress_level
from telegram_mcp.disclosure.measure import RECORD_ELEMENT, MeasurementError

__all__ = ["provenance_digest", "provenance_vector"]


def _record_ref(record: Mapping[str, Any]) -> str:
    for field in ("message_ref", "peer_ref", "project_ref", "chat_ref"):
        value = record.get(field)
        if isinstance(value, str):
            return value
    raise ValueError("record carries no privacy-safe identity")


def provenance_vector(tool_name: str, data: Mapping[str, Any]) -> list[dict[str, Any]]:
    """One entry per emitted record, in emitted order."""
    element = RECORD_ELEMENT.get(tool_name)
    if element is None:
        raise MeasurementError("tool has no provenance vector")
    vector: list[dict[str, Any]] = []
    for record in data.get(element, []):
        vector.append(
            {
                "record_ref": _record_ref(record),
                "origin_project_refs": sorted(record.get("origin_project_refs") or []),
                "egress_level": effective_egress_level([record]),
                "text_truncated": bool(record.get("text_truncated", False)),
            }
        )
    return vector


def provenance_digest(tool_name: str, data: Mapping[str, Any]) -> str:
    """``SHA-256(JCS(vector))`` in lowercase hexadecimal."""
    return hashlib.sha256(jcs_dumps(provenance_vector(tool_name, data))).hexdigest()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_disclosure_provenance.py -v`
Expected: PASS, 6 tests.

- [ ] **Step 5: Commit**

```bash
git add src/telegram_mcp/disclosure/provenance.py tests/unit/test_disclosure_provenance.py
git commit -m "feat: add the provenance vector and its digest"
```

---

### Task 5: Search coverage object, invariants and digest

The coverage object is already frozen in `contracts/meta.json`. Phase 3 owns its construction, the invariants JSON Schema cannot express, and its digest.

**Files:**
- Create: `src/telegram_mcp/disclosure/coverage.py`
- Test: `tests/unit/test_disclosure_coverage.py`

**Interfaces:**
- Consumes: `jcs_dumps`.
- Produces:
  - `PARTIAL_REASONS: tuple[str, ...]` — the closed six.
  - `build_coverage(*, complete, eligible_peers, peers_scanned, telegram_rpcs, hits_examined, hits_returned, partial_reasons, project_coverage) -> dict[str, Any]`
  - `validate_coverage(coverage: Mapping[str, Any], *, next_cursor: str | None, partial: bool) -> None`
  - `coverage_digest(coverage: Mapping[str, Any] | None) -> str | None`
  - `CoverageError(Exception)`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_disclosure_coverage.py`:

```python
"""Search coverage proof (design §3.3; frozen spec §23D)."""

import pytest

from telegram_mcp.disclosure.coverage import (
    PARTIAL_REASONS,
    CoverageError,
    build_coverage,
    coverage_digest,
    validate_coverage,
)

_A, _B = "tpr_" + "a" * 26, "tpr_" + "b" * 26


def _complete():
    return build_coverage(
        complete=True,
        eligible_peers=3,
        peers_scanned=3,
        telegram_rpcs=5,
        hits_examined=40,
        hits_returned=7,
        partial_reasons=[],
        project_coverage=[
            {"project_ref": _A, "eligible_peers": 2, "peers_scanned": 2},
            {"project_ref": _B, "eligible_peers": 2, "peers_scanned": 2},
        ],
    )


def test_partial_reasons_is_the_closed_six():
    assert PARTIAL_REASONS == (
        "deadline",
        "rpc_budget",
        "hit_budget",
        "peer_budget",
        "response_limit",
        "telegram_partial",
    )


def test_complete_requires_no_cursor_no_partial_no_reasons():
    validate_coverage(_complete(), next_cursor=None, partial=False)


def test_complete_with_a_cursor_is_refused():
    with pytest.raises(CoverageError):
        validate_coverage(_complete(), next_cursor="tgc_" + "a" * 26, partial=False)


def test_complete_with_partial_true_is_refused():
    with pytest.raises(CoverageError):
        validate_coverage(_complete(), next_cursor=None, partial=True)


def test_a_cursor_requires_response_limit_in_reasons():
    bounded = build_coverage(
        complete=False,
        eligible_peers=3,
        peers_scanned=1,
        telegram_rpcs=2,
        hits_examined=10,
        hits_returned=5,
        partial_reasons=["deadline"],
        project_coverage=[{"project_ref": _A, "eligible_peers": 2, "peers_scanned": 1}],
    )
    with pytest.raises(CoverageError):
        validate_coverage(bounded, next_cursor="tgc_" + "a" * 26, partial=True)


def test_unknown_partial_reason_is_refused():
    with pytest.raises(CoverageError):
        build_coverage(
            complete=False,
            eligible_peers=1,
            peers_scanned=0,
            telegram_rpcs=0,
            hits_examined=0,
            hits_returned=0,
            partial_reasons=["ran_out_of_patience"],
            project_coverage=[{"project_ref": _A, "eligible_peers": 1, "peers_scanned": 0}],
        )


def test_coverage_dedups_globally_while_projects_may_double_count():
    # One canonical peer shared by both projects: global totals count it
    # once, each project counts it locally. This is the OPPOSITE of the
    # exposure-accounting rule, and the two must never be swapped.
    coverage = build_coverage(
        complete=True,
        eligible_peers=1,
        peers_scanned=1,
        telegram_rpcs=1,
        hits_examined=2,
        hits_returned=2,
        partial_reasons=[],
        project_coverage=[
            {"project_ref": _A, "eligible_peers": 1, "peers_scanned": 1},
            {"project_ref": _B, "eligible_peers": 1, "peers_scanned": 1},
        ],
    )
    validate_coverage(coverage, next_cursor=None, partial=False)
    assert coverage["eligible_peers"] == 1
    assert sum(p["eligible_peers"] for p in coverage["project_coverage"]) == 2


def test_project_coverage_must_hold_one_to_eight_entries():
    with pytest.raises(CoverageError):
        build_coverage(
            complete=True, eligible_peers=0, peers_scanned=0, telegram_rpcs=0,
            hits_examined=0, hits_returned=0, partial_reasons=[], project_coverage=[],
        )


def test_digest_is_null_for_non_search_tools():
    assert coverage_digest(None) is None


def test_digest_is_lowercase_hex_and_order_stable():
    digest = coverage_digest(_complete())
    assert len(digest) == 64 and digest == digest.lower()
    assert digest == coverage_digest(_complete())
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_disclosure_coverage.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'telegram_mcp.disclosure.coverage'`.

- [ ] **Step 3: Write minimal implementation**

Create `src/telegram_mcp/disclosure/coverage.py`:

```python
"""Search coverage proof (frozen spec §23D; design §3.3).

The coverage object is already frozen in ``contracts/meta.json``. What lives
here is everything JSON Schema cannot express: the implication chain between
``complete``, ``next_cursor``, ``partial`` and ``partial_reasons``, and the
digest that binds the emitted object into the signed proof.

**Deduplication runs the opposite way from exposure accounting.** A shared
canonical peer is counted once in the global totals and once in each
contributing project's local counts; exposure accounting deliberately
over-counts the same record in every contributing project bucket. Copying
either rule onto the other is the easiest mistake in Phase 3.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from typing import Any

from telegram_mcp.consent.challenge import jcs_dumps

__all__ = [
    "PARTIAL_REASONS",
    "CoverageError",
    "build_coverage",
    "coverage_digest",
    "validate_coverage",
]

# Closed list, spec §23D. Not extensible without a contract change.
PARTIAL_REASONS: tuple[str, ...] = (
    "deadline",
    "rpc_budget",
    "hit_budget",
    "peer_budget",
    "response_limit",
    "telegram_partial",
)

_MAX_PROJECTS = 8


class CoverageError(Exception):
    """The coverage object is internally inconsistent or out of contract."""


def build_coverage(
    *,
    complete: bool,
    eligible_peers: int,
    peers_scanned: int,
    telegram_rpcs: int,
    hits_examined: int,
    hits_returned: int,
    partial_reasons: Sequence[str],
    project_coverage: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Assemble a contract-shaped coverage object, rejecting bad input."""
    unknown = [r for r in partial_reasons if r not in PARTIAL_REASONS]
    if unknown:
        raise CoverageError("unknown partial reason")
    if len(set(partial_reasons)) != len(partial_reasons):
        raise CoverageError("duplicate partial reason")
    if not 1 <= len(project_coverage) <= _MAX_PROJECTS:
        raise CoverageError("project_coverage must hold 1..8 entries")
    for count in (eligible_peers, peers_scanned, telegram_rpcs, hits_examined, hits_returned):
        if count < 0:
            raise CoverageError("coverage counts must be non-negative")
    if peers_scanned > eligible_peers:
        raise CoverageError("peers_scanned exceeds eligible_peers")

    return {
        "complete": bool(complete),
        "eligible_peers": eligible_peers,
        "peers_scanned": peers_scanned,
        "telegram_rpcs": telegram_rpcs,
        "hits_examined": hits_examined,
        "hits_returned": hits_returned,
        "partial_reasons": list(partial_reasons),
        "project_coverage": [dict(entry) for entry in project_coverage],
    }


def validate_coverage(
    coverage: Mapping[str, Any], *, next_cursor: str | None, partial: bool
) -> None:
    """Enforce the §23D implication chain. Raises, never warns."""
    complete = bool(coverage["complete"])
    reasons = list(coverage["partial_reasons"])

    if complete:
        if next_cursor is not None:
            raise CoverageError("complete coverage cannot carry a next_cursor")
        if partial:
            raise CoverageError("complete coverage cannot be partial")
        if reasons:
            raise CoverageError("complete coverage cannot carry partial reasons")
        if coverage["peers_scanned"] != coverage["eligible_peers"]:
            raise CoverageError("complete coverage must have scanned every eligible peer")
        return

    if next_cursor is not None and "response_limit" not in reasons:
        raise CoverageError("a next_cursor requires response_limit in partial_reasons")


def coverage_digest(coverage: Mapping[str, Any] | None) -> str | None:
    """``SHA-256(JCS(coverage))`` in lowercase hex; ``None`` for non-search tools."""
    if coverage is None:
        return None
    return hashlib.sha256(jcs_dumps(coverage)).hexdigest()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_disclosure_coverage.py -v`
Expected: PASS, 10 tests.

- [ ] **Step 5: Check the built object against the frozen contract**

Run: `uv run python -c "
import json, jsonschema
from telegram_mcp.disclosure.coverage import build_coverage
schema = json.load(open('src/telegram_mcp/contracts/meta.json'))['properties']['coverage']
c = build_coverage(complete=True, eligible_peers=1, peers_scanned=1, telegram_rpcs=1,
                   hits_examined=1, hits_returned=1, partial_reasons=[],
                   project_coverage=[{'project_ref': 'tpr_' + 'a'*26, 'eligible_peers': 1, 'peers_scanned': 1}])
jsonschema.validate(c, schema)
print('coverage validates against the frozen contract')
"`
Expected: `coverage validates against the frozen contract`.

- [ ] **Step 6: Commit**

```bash
git add src/telegram_mcp/disclosure/coverage.py tests/unit/test_disclosure_coverage.py
git commit -m "feat: add the search coverage object and its invariants"
```

---

### Task 6: Appendix-K receipts — build, sign, verify offline

**Files:**
- Create: `src/telegram_mcp/disclosure/receipts.py`
- Test: `tests/unit/test_disclosure_receipts.py`, `tests/security/test_offline_verifier.py`

**Interfaces:**
- Consumes: `jcs_dumps`; `load_key`, `key_id` from `telegram_mcp.keys.store`; `mint_opaque_ref` from `telegram_mcp.opaque`.
- Produces:
  - `PROOF_SCHEMA = "tg-mcp-disclosure/v1"`
  - `APPENDIX_K_FIELDS: tuple[str, ...]` — the twenty-one field names.
  - `build_proof_payload(**fields) -> dict[str, Any]`
  - `sign_payload(payload, *, private_seed, proof_key_id) -> dict[str, str]` returning `{"proof_key_id", "proof_signature", "proof_payload_sha256"}`
  - `verify_proof(payload, *, proof_signature, proof_payload_sha256, public_key_b64url) -> bool`
  - `mint_disclosure_ref() -> str`
  - `ReceiptError(Exception)`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_disclosure_receipts.py`:

```python
"""Appendix-K receipts: build, sign, verify (design §4)."""

import base64

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from telegram_mcp.disclosure.receipts import (
    APPENDIX_K_FIELDS,
    PROOF_SCHEMA,
    ReceiptError,
    build_proof_payload,
    mint_disclosure_ref,
    sign_payload,
    verify_proof,
)

_SEED = bytes(range(32))


def _public_b64url(seed: bytes) -> str:
    raw = Ed25519PrivateKey.from_private_bytes(seed).public_key().public_bytes_raw()
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _payload(**overrides):
    fields = {
        "disclosure_ref": mint_disclosure_ref(),
        "principal_ref": "prn_" + "a" * 26,
        "client_ref": "tcl_" + "b" * 26,
        "account_ref": "tga_" + "c" * 26,
        "tool_name": "telegram_get_messages",
        "security_epoch": 12,
        "policy_epoch": 31,
        "project_scope_digest": "hmac-sha256:" + "0" * 64,
        "project_count": 1,
        "effective_egress_level": "excerpt",
        "records_disclosed": 20,
        "bytes_disclosed": 16384,
        "partial": False,
        "committed_at": "2026-09-22T00:00:00Z",
        "consent_key_id": "p256:sha256:" + "1" * 64,
        "consent_challenge_digest": "2" * 64,
        "canonical_result_provenance_digest": "3" * 64,
        "canonical_coverage_digest": None,
    }
    fields.update(overrides)
    return build_proof_payload(**fields)


def test_payload_carries_exactly_the_twenty_one_appendix_k_fields():
    payload = _payload()
    assert len(APPENDIX_K_FIELDS) == 21
    assert set(payload) == set(APPENDIX_K_FIELDS)
    assert payload["schema"] == PROOF_SCHEMA
    assert payload["consent_verified"] is True
    assert payload["commit_status"] == "committed"


def test_disclosure_ref_uses_the_frozen_tdr_shape():
    ref = mint_disclosure_ref()
    assert ref.startswith("tdr_") and len(ref) == 30


def test_signature_verifies_and_digest_matches():
    payload = _payload()
    signed = sign_payload(payload, private_seed=_SEED, proof_key_id="ed25519:sha256:" + "f" * 64)
    assert verify_proof(
        payload,
        proof_signature=signed["proof_signature"],
        proof_payload_sha256=signed["proof_payload_sha256"],
        public_key_b64url=_public_b64url(_SEED),
    )


def test_one_changed_field_breaks_verification():
    payload = _payload()
    signed = sign_payload(payload, private_seed=_SEED, proof_key_id="ed25519:sha256:" + "f" * 64)
    tampered = dict(payload, records_disclosed=21)
    assert not verify_proof(
        tampered,
        proof_signature=signed["proof_signature"],
        proof_payload_sha256=signed["proof_payload_sha256"],
        public_key_b64url=_public_b64url(_SEED),
    )


def test_a_wrong_key_does_not_verify():
    payload = _payload()
    signed = sign_payload(payload, private_seed=_SEED, proof_key_id="ed25519:sha256:" + "f" * 64)
    assert not verify_proof(
        payload,
        proof_signature=signed["proof_signature"],
        proof_payload_sha256=signed["proof_payload_sha256"],
        public_key_b64url=_public_b64url(bytes(32)),
    )


def test_payload_refuses_unknown_fields():
    with pytest.raises(ReceiptError):
        _payload(search_query="invoices")


def test_payload_refuses_a_missing_field():
    with pytest.raises(ReceiptError):
        build_proof_payload(disclosure_ref=mint_disclosure_ref())


def test_search_tools_must_carry_a_coverage_digest():
    with pytest.raises(ReceiptError):
        _payload(tool_name="telegram_search_messages", canonical_coverage_digest=None)


def test_non_search_tools_must_not_carry_one():
    with pytest.raises(ReceiptError):
        _payload(tool_name="telegram_get_messages", canonical_coverage_digest="4" * 64)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_disclosure_receipts.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'telegram_mcp.disclosure.receipts'`.

- [ ] **Step 3: Write minimal implementation**

Create `src/telegram_mcp/disclosure/receipts.py`:

```python
"""Proof-carrying retrieval receipts (frozen spec §23A, Appendix K).

The payload carries exactly the twenty-one Appendix-K fields, signed with
the dedicated Ed25519 disclosure key over RFC 8785 canonical bytes.
``proof_payload_sha256`` is the lowercase hex SHA-256 of those exact bytes
and must equal the persisted receipt column of the same name.

A verifier needs only ``proof_payload``, ``proof_signature`` and the
matching public key — no database, no Telegram. Nothing in this module
opens either.
"""

from __future__ import annotations

import base64
import hashlib
from collections.abc import Mapping
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

from telegram_mcp.consent.challenge import jcs_dumps
from telegram_mcp.opaque import mint_opaque_ref

__all__ = [
    "APPENDIX_K_FIELDS",
    "PROOF_SCHEMA",
    "ReceiptError",
    "build_proof_payload",
    "mint_disclosure_ref",
    "sign_payload",
    "verify_proof",
]

PROOF_SCHEMA = "tg-mcp-disclosure/v1"

# Appendix K.1, in the order the appendix lists them. Set equality is what
# is enforced; JCS sorts the keys for the signed bytes.
APPENDIX_K_FIELDS: tuple[str, ...] = (
    "schema",
    "disclosure_ref",
    "principal_ref",
    "client_ref",
    "account_ref",
    "tool_name",
    "security_epoch",
    "policy_epoch",
    "project_scope_digest",
    "project_count",
    "effective_egress_level",
    "records_disclosed",
    "bytes_disclosed",
    "partial",
    "commit_status",
    "committed_at",
    "consent_verified",
    "consent_key_id",
    "consent_challenge_digest",
    "canonical_result_provenance_digest",
    "canonical_coverage_digest",
)

_SEARCH_TOOLS = frozenset({"telegram_search_messages", "telegram_cross_project_search"})

# Fields the caller does not supply because they are constants of a
# committed receipt.
_DERIVED = {"schema": PROOF_SCHEMA, "commit_status": "committed", "consent_verified": True}


class ReceiptError(Exception):
    """The proof payload is out of contract."""


def mint_disclosure_ref() -> str:
    """Mint a ``tdr_`` disclosure-receipt reference (spec §11.1)."""
    return mint_opaque_ref("tdr_")


def build_proof_payload(**fields: Any) -> dict[str, Any]:
    """Assemble the Appendix-K payload, rejecting anything out of contract."""
    supplied = set(fields)
    expected = set(APPENDIX_K_FIELDS) - set(_DERIVED)
    unknown = supplied - expected
    if unknown:
        # Never name the offending value; the field name is enough.
        raise ReceiptError("proof payload carries unknown fields")
    missing = expected - supplied
    if missing:
        raise ReceiptError("proof payload is missing required fields")

    payload: dict[str, Any] = dict(fields)
    payload.update(_DERIVED)

    tool_name = payload["tool_name"]
    coverage = payload["canonical_coverage_digest"]
    if tool_name in _SEARCH_TOOLS and coverage is None:
        raise ReceiptError("search tools require a coverage digest")
    if tool_name not in _SEARCH_TOOLS and coverage is not None:
        raise ReceiptError("non-search tools must not carry a coverage digest")

    return payload


def _canonical(payload: Mapping[str, Any]) -> bytes:
    if set(payload) != set(APPENDIX_K_FIELDS):
        raise ReceiptError("proof payload is not the Appendix-K shape")
    return jcs_dumps(payload)


def sign_payload(
    payload: Mapping[str, Any], *, private_seed: bytes, proof_key_id: str
) -> dict[str, str]:
    """Sign the canonical payload bytes; returns the three ``meta`` fields."""
    raw = _canonical(payload)
    signature = Ed25519PrivateKey.from_private_bytes(private_seed).sign(raw)
    return {
        "proof_key_id": proof_key_id,
        "proof_signature": base64.urlsafe_b64encode(signature).rstrip(b"=").decode("ascii"),
        "proof_payload_sha256": hashlib.sha256(raw).hexdigest(),
    }


def _b64url_decode(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def verify_proof(
    payload: Mapping[str, Any],
    *,
    proof_signature: str,
    proof_payload_sha256: str,
    public_key_b64url: str,
) -> bool:
    """Appendix K.2 steps 2, 3 and 5. No database, no network."""
    try:
        raw = _canonical(payload)
    except ReceiptError:
        return False
    if hashlib.sha256(raw).hexdigest() != proof_payload_sha256:
        return False
    try:
        public = Ed25519PublicKey.from_public_bytes(_b64url_decode(public_key_b64url))
        public.verify(_b64url_decode(proof_signature), raw)
    except (InvalidSignature, ValueError):
        return False
    return True
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_disclosure_receipts.py -v`
Expected: PASS, 9 tests.

- [ ] **Step 5: Write the offline-verifier test**

Create `tests/security/test_offline_verifier.py`:

```python
"""A verifier needs no database handle (design §4.1; Appendix K.2)."""

import base64
import sqlite3

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from telegram_mcp.disclosure.receipts import (
    build_proof_payload,
    mint_disclosure_ref,
    sign_payload,
    verify_proof,
)

_SEED = bytes(range(32))


def test_verification_succeeds_with_sqlite_and_sockets_forbidden(monkeypatch):
    payload = build_proof_payload(
        disclosure_ref=mint_disclosure_ref(),
        principal_ref="prn_" + "a" * 26,
        client_ref="tcl_" + "b" * 26,
        account_ref="tga_" + "c" * 26,
        tool_name="telegram_get_messages",
        security_epoch=1,
        policy_epoch=1,
        project_scope_digest="hmac-sha256:" + "0" * 64,
        project_count=1,
        effective_egress_level="metadata_only",
        records_disclosed=0,
        bytes_disclosed=2,
        partial=False,
        committed_at="2026-09-22T00:00:00Z",
        consent_key_id="p256:sha256:" + "1" * 64,
        consent_challenge_digest="2" * 64,
        canonical_result_provenance_digest="3" * 64,
        canonical_coverage_digest=None,
    )
    signed = sign_payload(payload, private_seed=_SEED, proof_key_id="ed25519:sha256:" + "f" * 64)
    raw = Ed25519PrivateKey.from_private_bytes(_SEED).public_key().public_bytes_raw()
    public = base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")

    def _forbidden(*args, **kwargs):
        raise AssertionError("the verifier must not touch a database or a socket")

    monkeypatch.setattr(sqlite3, "connect", _forbidden)
    import socket

    monkeypatch.setattr(socket, "socket", _forbidden)

    assert verify_proof(
        payload,
        proof_signature=signed["proof_signature"],
        proof_payload_sha256=signed["proof_payload_sha256"],
        public_key_b64url=public,
    )
```

- [ ] **Step 6: Run the offline test**

Run: `uv run pytest tests/security/test_offline_verifier.py -v`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add src/telegram_mcp/disclosure/receipts.py tests/unit/test_disclosure_receipts.py tests/security/test_offline_verifier.py
git commit -m "feat: add Appendix-K receipts and the offline verifier"
```

---

### Task 7: Verification-key registry and historical export

**Files:**
- Create: `src/telegram_mcp/disclosure/keys.py`
- Test: `tests/unit/test_disclosure_keys.py`

**Interfaces:**
- Consumes: `open_db` from `telegram_mcp.storage.db`; `load_key`, `key_id` from `telegram_mcp.keys.store`; `verify_proof` from Task 6.
- Produces:
  - `PURPOSES: tuple[str, ...]` = `("disclosure_proof", "audit_checkpoint", "policy_backup")`
  - `publish_verification_key(conn, *, key_id, purpose, algorithm, public_key_b64url, activated_at) -> None`
  - `retire_verification_key(conn, *, key_id, retired_at) -> None`
  - `current_verification_key(conn, purpose) -> dict[str, Any] | None`
  - `lookup_verification_key(conn, key_id) -> dict[str, Any] | None`
  - `export_verification_keys(conn, *, key_id: str | None = None) -> list[dict[str, Any]]`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_disclosure_keys.py`:

```python
"""verification_keys registry and historical export (design §4.2, §8)."""

import pytest

from telegram_mcp.disclosure.keys import (
    current_verification_key,
    export_verification_keys,
    lookup_verification_key,
    publish_verification_key,
    retire_verification_key,
)
from telegram_mcp.storage.db import open_db
from telegram_mcp.storage.migrations import migrate

_OLD = "ed25519:sha256:" + "a" * 64
_NEW = "ed25519:sha256:" + "b" * 64


@pytest.fixture
def conn(tmp_path):
    connection = open_db(tmp_path / "meta.db")
    migrate(connection)
    return connection


def _publish(conn, key_id, activated_at):
    publish_verification_key(
        conn,
        key_id=key_id,
        purpose="disclosure_proof",
        algorithm="Ed25519",
        public_key_b64url="A" * 43,
        activated_at=activated_at,
    )


def test_current_key_is_the_unretired_one(conn):
    _publish(conn, _OLD, "2026-01-01T00:00:00Z")
    retire_verification_key(conn, key_id=_OLD, retired_at="2026-06-01T00:00:00Z")
    _publish(conn, _NEW, "2026-06-01T00:00:00Z")

    assert current_verification_key(conn, "disclosure_proof")["key_id"] == _NEW


def test_a_retired_key_stays_exportable_for_historical_receipts(conn):
    _publish(conn, _OLD, "2026-01-01T00:00:00Z")
    retire_verification_key(conn, key_id=_OLD, retired_at="2026-06-01T00:00:00Z")

    row = lookup_verification_key(conn, _OLD)
    assert row["retired_at"] == "2026-06-01T00:00:00Z"
    assert row["public_key_b64url"] == "A" * 43


def test_export_returns_current_and_historical(conn):
    _publish(conn, _OLD, "2026-01-01T00:00:00Z")
    retire_verification_key(conn, key_id=_OLD, retired_at="2026-06-01T00:00:00Z")
    _publish(conn, _NEW, "2026-06-01T00:00:00Z")

    exported = export_verification_keys(conn)
    assert {row["key_id"] for row in exported} == {_OLD, _NEW}
    assert [row["key_id"] for row in export_verification_keys(conn, key_id=_OLD)] == [_OLD]


def test_export_never_emits_private_material(conn):
    _publish(conn, _OLD, "2026-01-01T00:00:00Z")
    for row in export_verification_keys(conn):
        assert set(row) == {"key_id", "purpose", "algorithm", "public_key_b64url", "activated_at", "retired_at"}


def test_unknown_purpose_is_refused(conn):
    with pytest.raises(ValueError):
        publish_verification_key(
            conn, key_id=_OLD, purpose="general_vibes", algorithm="Ed25519",
            public_key_b64url="A" * 43, activated_at="2026-01-01T00:00:00Z",
        )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_disclosure_keys.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'telegram_mcp.disclosure.keys'`.

- [ ] **Step 3: Write minimal implementation**

Create `src/telegram_mcp/disclosure/keys.py`:

```python
"""Public verification-key registry (spec §23A.2, §33; design §4.2, §8).

Only public halves live here. A retained receipt must stay verifiable for
receipt retention plus the verification-key grace period, so retirement
never deletes a row — it stamps ``retired_at`` and leaves the public key
exportable.
"""

from __future__ import annotations

import sqlite3
from typing import Any

__all__ = [
    "PURPOSES",
    "current_verification_key",
    "export_verification_keys",
    "lookup_verification_key",
    "publish_verification_key",
    "retire_verification_key",
]

# Mirrors the CHECK constraint on verification_keys.purpose (spec §12.2).
PURPOSES: tuple[str, ...] = ("disclosure_proof", "audit_checkpoint", "policy_backup")

_COLUMNS = "key_id, purpose, algorithm, public_key_b64url, activated_at, retired_at"


def _row(cursor_row: sqlite3.Row | tuple[Any, ...] | None) -> dict[str, Any] | None:
    if cursor_row is None:
        return None
    return dict(zip(_COLUMNS.split(", "), cursor_row, strict=True))


def publish_verification_key(
    conn: sqlite3.Connection,
    *,
    key_id: str,
    purpose: str,
    algorithm: str,
    public_key_b64url: str,
    activated_at: str,
) -> None:
    """Record one public half. Private material must never reach this call."""
    if purpose not in PURPOSES:
        raise ValueError("unknown verification-key purpose")
    conn.execute(
        "INSERT INTO verification_keys"
        " (key_id, purpose, algorithm, public_key_b64url, activated_at, retired_at)"
        " VALUES (?, ?, ?, ?, ?, NULL)",
        (key_id, purpose, algorithm, public_key_b64url, activated_at),
    )
    conn.commit()


def retire_verification_key(conn: sqlite3.Connection, *, key_id: str, retired_at: str) -> None:
    """Stamp retirement. The row and its public key stay for verification."""
    conn.execute(
        "UPDATE verification_keys SET retired_at = ? WHERE key_id = ?", (retired_at, key_id)
    )
    conn.commit()


def current_verification_key(conn: sqlite3.Connection, purpose: str) -> dict[str, Any] | None:
    """The one unretired key for a purpose, newest activation first."""
    if purpose not in PURPOSES:
        raise ValueError("unknown verification-key purpose")
    return _row(
        conn.execute(
            f"SELECT {_COLUMNS} FROM verification_keys"
            " WHERE purpose = ? AND retired_at IS NULL"
            " ORDER BY activated_at DESC LIMIT 1",
            (purpose,),
        ).fetchone()
    )


def lookup_verification_key(conn: sqlite3.Connection, key_id: str) -> dict[str, Any] | None:
    """Resolve any key id, current or historical (Appendix K.2 step 4)."""
    return _row(
        conn.execute(
            f"SELECT {_COLUMNS} FROM verification_keys WHERE key_id = ?", (key_id,)
        ).fetchone()
    )


def export_verification_keys(
    conn: sqlite3.Connection, *, key_id: str | None = None
) -> list[dict[str, Any]]:
    """Backing query for ``telegram-mcp disclosure key [--id <key-id>]``."""
    if key_id is None:
        rows = conn.execute(
            f"SELECT {_COLUMNS} FROM verification_keys ORDER BY activated_at"
        ).fetchall()
    else:
        rows = conn.execute(
            f"SELECT {_COLUMNS} FROM verification_keys WHERE key_id = ?", (key_id,)
        ).fetchall()
    return [row for row in (_row(r) for r in rows) if row is not None]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_disclosure_keys.py -v`
Expected: PASS, 5 tests.

- [ ] **Step 5: Commit**

```bash
git add src/telegram_mcp/disclosure/keys.py tests/unit/test_disclosure_keys.py
git commit -m "feat: add the public verification-key registry"
```

---

### Task 8: Retire the synthetic status key

`tools/status.py` advertises a key derived from 32 zero bytes. Appendix K.2 step 4 tells a verifier to resolve `proof_key_id` against the key `telegram_status` advertises, so the moment a real signer exists that key is a forgery oracle. A tripwire test in `tests/security/test_demo_isolation.py` currently pins the synthetic values and must now be replaced — not relaxed.

**Files:**
- Modify: `src/telegram_mcp/tools/status.py` (whole module)
- Modify: `tests/security/test_demo_isolation.py` (replace `test_synthetic_disclosure_key_is_marked_and_tripwired`)
- Test: `tests/unit/test_server.py` (existing status assertions must still pass)

**Interfaces:**
- Consumes: `current_verification_key` from Task 7; `key_id` from `telegram_mcp.keys.store`.
- Produces: `make_status(*, disclosure_key: tuple[str, str]) -> dict` — the pair is `(key_id, public_key_b64url)`, **required**.

**The frozen contract forbids null here.** `telegram_status.data.json` types
`disclosure_proof_key_id` as `{"type": "string", "minLength": 1}` and
`disclosure_proof_public_key` as `{"pattern": "^[A-Za-z0-9_-]{43}$"}`. Neither
admits `null`, and widening them would contradict the design's controlling
statement that no result schema changes. So `make_status` **requires** a key
pair. Production passes the provisioned `disclosure-key`; the synthetic demo
generates a fresh **ephemeral per-process** key at startup. An ephemeral key
is honest — it is a real Ed25519 key whose private half exists only in this
process and signs nothing — and it is not the publicly known zero seed.

- [ ] **Step 1: Write the failing test**

Replace `test_synthetic_disclosure_key_is_marked_and_tripwired` in `tests/security/test_demo_isolation.py` with:

```python
def test_status_never_advertises_a_publicly_known_key():
    """The zero-seed key is gone. It must not come back.

    Its private half is 32 zero bytes, which everybody has. Appendix K.2
    step 4 points verifiers at whatever this tool advertises, so a build
    that ships this key lets anyone forge a receipt that verifies.
    """
    import base64

    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    from telegram_mcp.tools.status import make_status

    zero_raw = Ed25519PrivateKey.from_private_bytes(bytes(32)).public_key().public_bytes_raw()
    zero_public = base64.urlsafe_b64encode(zero_raw).rstrip(b"=").decode("ascii")

    data = make_status()["data"]
    assert data["disclosure_proof_public_key"] != zero_public
    assert data["disclosure_proof_key_id"] != "synthetic-test-key-v1"


def test_status_requires_a_key_pair():
    import pytest

    from telegram_mcp.tools.status import make_status

    # The contract admits no null here, so there is no "no key" state to
    # report. A build with no key cannot answer telegram_status at all,
    # which is the honest failure.
    with pytest.raises(TypeError):
        make_status()


def test_status_reports_the_supplied_key(ephemeral_disclosure_key):
    from telegram_mcp.tools.status import make_status

    data = make_status(disclosure_key=ephemeral_disclosure_key)["data"]
    assert data["disclosure_proof_key_id"] == ephemeral_disclosure_key[0]
    assert data["disclosure_proof_public_key"] == ephemeral_disclosure_key[1]


def test_the_advertised_key_satisfies_the_frozen_contract(ephemeral_disclosure_key):
    import json

    import jsonschema

    from telegram_mcp.tools.status import make_status

    schema = json.load(open("src/telegram_mcp/contracts/telegram_status.data.json"))
    jsonschema.validate(make_status(disclosure_key=ephemeral_disclosure_key)["data"], schema)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/security/test_demo_isolation.py -v`
Expected: FAIL — the zero key is still advertised and `make_status` takes no `disclosure_key` argument.

- [ ] **Step 3: Add the ephemeral-key fixture**

Add to `tests/conftest.py`:

```python
@pytest.fixture
def ephemeral_disclosure_key() -> tuple[str, str]:
    """A real Ed25519 key that exists only for this test and signs nothing."""
    import base64
    import hashlib
    import secrets

    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    seed = secrets.token_bytes(32)
    raw = Ed25519PrivateKey.from_private_bytes(seed).public_key().public_bytes_raw()
    key_id = "ed25519:sha256:" + hashlib.sha256(raw).hexdigest()
    public = base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")
    return key_id, public
```

The same construction belongs in the demo launcher, which mints one ephemeral
key per process and passes it to `make_status`. **Do not widen the contract.**
Both fields stay non-nullable strings; the key id keeps the frozen
`<kind>:sha256:<64 hex>` shape and the public half is exactly 43 base64url
characters, which is what the contract's pattern requires.

- [ ] **Step 4: Write minimal implementation**

Replace `src/telegram_mcp/tools/status.py` entirely:

```python
"""Status factory: gateway health plus the current disclosure public key.

Appendix K.2 step 4 resolves a receipt's ``proof_key_id`` against the key
this tool advertises, so what goes here is load-bearing. The caller passes
the provisioned key or nothing; this module never derives one, and in
particular never derives the all-zero development key that earlier builds
advertised.
"""

from __future__ import annotations

__all__ = ["make_status"]


def make_status(*, disclosure_key: tuple[str, str]) -> dict:
    """Build the ``telegram_status`` result.

    ``disclosure_key`` is ``(key_id, public_key_b64url)`` for the active
    disclosure-signing key and is **required**: the frozen contract types
    both fields as non-null strings, so there is no "no key" state to report.
    Production passes the provisioned ``disclosure-key``; the synthetic demo
    passes a fresh ephemeral key whose private half exists only in that
    process and signs nothing.

    This module never derives a key, and in particular never derives the
    all-zero development key earlier builds advertised.
    """
    key_id, public_key = disclosure_key
    return {
        "data": {
            "connected": False,
            "authorised": False,
            "account_ref": None,
            "account_label": None,
            "read_scope_mode": None,
            "policy_epoch": None,
            "security_epoch": 1,
            "security_locked": False,
            "disclosure_proof_key_id": key_id,
            "disclosure_proof_public_key": public_key,
            "capabilities": {
                "read_chats": False,
                "search_messages": False,
                "project_namespaces": True,
                "cross_project_search": True,
                "project_selection_required": True,
                "proof_carrying_retrieval": True,
                "egress_profiles": True,
                "exposure_budgets": True,
                "tamper_evident_audit": True,
                "emergency_lock": True,
                "write_messages": False,
                "attachments": False,
                "secret_chats": False,
            },
        },
        "meta": {
            "source": "gateway",
            "content_trust": "non_instructional_gateway_metadata",
            "truncated": False,
            "partial": False,
            "next_cursor": None,
            "disclosure": None,
            "coverage": None,
        },
    }
```

- [ ] **Step 5: Run the whole suite**

Run: `uv run pytest -q`
Expected: PASS. If `tests/unit/test_server.py` or `tests/contract/test_schemas.py` asserted the old key id, update those assertions to the null-or-real pair — they were describing the defect, not a requirement.

- [ ] **Step 6: Commit**

```bash
git add src/telegram_mcp/tools/status.py tests/security/test_demo_isolation.py tests/unit/test_server.py tests/conftest.py src/telegram_mcp/cli.py
git commit -m "fix: stop advertising the zero-seed disclosure key"
```

---

### Task 9: Phase-3a evidence and gate ledger

**Files:**
- Create: `docs/verification/phase-3.md`
- Modify: `AGENT.md`, `CHANGELOG.md`, `README.md`

- [ ] **Step 1: Run the full gate and capture real output**

```bash
uv run pytest -q
uv run python scripts/e2e_smoke.py
uv run python scripts/extract_contracts.py --check
uv run ruff check src tests scripts && uv run ruff format --check src tests scripts
uv run mypy src/telegram_mcp
```

- [ ] **Step 2: Write `docs/verification/phase-3.md`**

Follow the shape of `docs/verification/phase-2a.md`: a gate ledger with one row per gate, the exact command that proves each row, and an honest PARTIAL for every gate not fully met. Gate O is PARTIAL after 3a — receipts sign and verify, but nothing commits them and no audit chain exists. Record any deviation taken in Task 8 Step 3.

- [ ] **Step 3: Append the dated `**Raouf:**` entry to `AGENT.md` and `CHANGELOG.md`**

Scope, Summary, Files changed, Verification (with the real counts from Step 1), Follow-ups. State plainly that Plan 3a wires nothing into a tool, that no Telegram access occurred, and that no production claim is made.

- [ ] **Step 4: Commit**

```bash
git add docs/verification/phase-3.md AGENT.md CHANGELOG.md README.md
git commit -m "docs: record Phase-3a evidence"
```

---

## Self-Review

**Spec coverage:** §3.1 egress → Task 3. §3.2 provenance → Task 4. §3.3 coverage → Task 5. §4.1 live verification → Task 6. §4.2 historical verification → Task 7. §4.3 ref stability → the rotation test belongs with the receipt *persistence* that Plan 3c builds, and is carried there; 3a has no receipt row to rotate against. §4.4 synthetic key → Task 8. §5.1 measurement authority → Task 2. §5.2 quantities → Task 2. §8 keys → Task 1 and Task 7.

**Deferred to 3c on purpose:** anything requiring a committed receipt row — `disclosure verify` reconstruction, the ref-rotation test, and the content-leak sweep across all four stores.

**Type consistency:** `RECORD_ELEMENT` is used by `measure.py` and `provenance.py` under that exact name. `effective_egress_level` is defined in Task 3 and consumed in Task 4. `verify_proof` keyword arguments match between Task 6's definition and Task 7's test.
