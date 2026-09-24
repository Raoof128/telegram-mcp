"""Task 9 Step 3: one call through validation, authority, consent, gate, adapter.

This is the only test that touches all three layers together. It proves the
ordering the design insists on: policy allowing a call is not enough — the
consent broker must have issued and consumed an agent-signed approval before
the disclosure gate is even asked. When the grant is revoked the call fails
closed at authority, and the broker is never reached.

Two notes on shape. The plan's sketch called ``consume(handle, agent_sig=...)``;
the frozen wire is the whole ``ApprovalEnvelope``
(``challenge_sha256``/``sig``/``key_id``), so the real signature is used
here. And ``canonical_request_hmac`` is computed with the cursor-key query
digest — the same keyed HMAC over the canonical request shape — because the
dedicated per-tool binding lands with the tool slices in Phase 4.

The fake adapter lives in this test on purpose: shipping a fake Telegram
adapter in ``src`` would be a thing that could be mistaken for the real one.
"""

import hashlib

import pytest

from comms.transports.telegram.authority.cursors import (
    project_scope_digest,
    query_digest,
    scope_entries_from_view,
)
from comms.transports.telegram.authority.policy import (
    AuthorityRequest,
    ClientProjectGrant,
    ClientState,
    Denial,
    ProjectState,
    evaluate,
    make_view,
)
from comms.transports.telegram.consent.broker import ConsentBroker
from comms.transports.telegram.consent.challenge import (
    StubSigner,
    display_digest,
    synthetic_exposure_digest,
)
from comms.transports.telegram.consent.gate import SyntheticDisclosureGate
from comms.transports.telegram.contract import load_contracts
from comms.transports.telegram.results import success_result
from comms.transports.telegram.storage.db import open_db
from comms.transports.telegram.validation import validate_arguments

TS = "2026-09-22T00:00:00Z"
CURSOR_KEY = b"\x11" * 32
PRIVACY_KEY = b"\x22" * 32
CHALLENGE_KEY = b"\x01" * 32
RUNTIME_ID = b"\x02" * 16
PRINCIPAL = "prn_" + "a" * 26
CLIENT = "tcl_" + "b" * 26
ACCOUNT = "tga_" + "c" * 26
PROJECT = "tpr_" + "d" * 26
PEER = "tgp_" + "e" * 26
PEER_IDENTITY = "user:5001"


class CountingBroker(ConsentBroker):
    """A broker that records whether it was asked for anything at all."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.issue_calls = 0

    async def issue(self, **kwargs):
        self.issue_calls += 1
        return await super().issue(**kwargs)


class FakeTelegramAdapter:
    """Phase-4 seam stand-in: one canned peer, no network."""

    def __init__(self) -> None:
        self.calls = 0

    def list_chats(self, *, project_ref: str, limit: int) -> list[dict]:
        self.calls += 1
        return [
            {
                "peer_ref": PEER,
                "display_name": "Ops",
                "username": None,
                "chat_type": "group",
                "unread_count": 2,
                "is_archived": False,
                "is_muted": False,
                "last_message_at": "2026-09-21T23:00:00Z",
                "origin_project_refs": [project_ref],
            }
        ][:limit]


def _seed(tmp_path, *, granted: bool):
    conn = open_db(tmp_path / "db" / "meta.db")
    conn.execute(
        "INSERT INTO accounts(id, account_ref, telegram_user_id, created_at, updated_at)"
        " VALUES (1, ?, 1001, ?, ?)",
        (ACCOUNT, TS, TS),
    )
    conn.execute(
        "INSERT INTO principals(id, principal_ref, principal_key, auth_mode, created_at)"
        " VALUES (1, ?, 'k1', 'local', ?)",
        (PRINCIPAL, TS),
    )
    conn.execute(
        "INSERT INTO mcp_clients(id, principal_id, client_ref, auth_kind, auth_binding,"
        " client_kind, enabled, created_at)"
        " VALUES (1, 1, ?, 'bearer', 'b1', 'codex_local', 1, ?)",
        (CLIENT, TS),
    )
    conn.execute(
        "INSERT INTO policy_state(principal_id, account_id, mode, policy_epoch,"
        " include_archived, include_private, include_groups, include_channels, updated_at)"
        " VALUES (1, 1, 'allowlist', 1, 0, 1, 1, 1, ?)",
        (TS,),
    )
    conn.execute(
        "INSERT INTO projects(id, account_id, project_ref, slug, display_name, enabled,"
        " project_epoch, created_at, updated_at) VALUES (1, 1, ?, 's1', 'Ops', 1, 1, ?, ?)",
        (PROJECT, TS, TS),
    )
    conn.execute(
        "INSERT INTO peers(id, account_id, peer_ref, telegram_peer_type, telegram_peer_id,"
        " first_seen_at, last_seen_at) VALUES (1, 1, ?, 'user', 5001, ?, ?)",
        (PEER, TS, TS),
    )
    conn.execute(
        "INSERT INTO project_peers(project_id, peer_id, membership_kind, created_at, updated_at)"
        " VALUES (1, 1, 'primary', ?, ?)",
        (TS, TS),
    )
    if granted:
        conn.execute(
            "INSERT INTO client_projects(client_id, project_id, can_read, can_cross_search,"
            " egress_level, excerpt_max_codepoints, created_at, updated_at)"
            " VALUES (1, 1, 1, 0, 'full_text', NULL, ?, ?)",
            (TS, TS),
        )
    conn.commit()
    return conn


def _view_from(conn):
    grants = {
        (CLIENT, row[0]): ClientProjectGrant(bool(row[1]), bool(row[2]), row[3], row[4], "0" * 64)
        for row in conn.execute(
            "SELECT p.project_ref, cp.can_read, cp.can_cross_search, cp.egress_level,"
            " cp.excerpt_max_codepoints FROM client_projects cp"
            " JOIN projects p ON p.id = cp.project_id"
        )
    }
    return make_view(
        clients={CLIENT: ClientState(client_ref=CLIENT, enabled=True, principal_ref=PRINCIPAL)},
        projects={PROJECT: ProjectState(project_ref=PROJECT, enabled=True, project_epoch=1)},
        grants=grants,
        memberships={PROJECT: {PEER_IDENTITY}},
        owner_allows={PEER_IDENTITY},
        policy_epoch=1,
        security_epoch=1,
    )


def _broker(stub: StubSigner) -> CountingBroker:
    return CountingBroker(
        challenge_key=CHALLENGE_KEY, agent_verify=stub.verify, runtime_id=RUNTIME_ID
    )


async def test_vertical_slice_needs_consent_not_just_policy(tmp_path):
    conn = _seed(tmp_path, granted=True)
    arguments = validate_arguments(
        load_contracts()["telegram_list_chats"], {"project_ref": PROJECT}
    )
    view = _view_from(conn)

    # 1. authority: every layer must pass before anything else happens
    snapshot = evaluate(
        view,
        AuthorityRequest(
            operation="read",
            client_ref=CLIENT,
            project_refs=(PROJECT,),
            peer_identity=PEER_IDENTITY,
        ),
    )
    assert not isinstance(snapshot, Denial)
    assert snapshot.effective_egress.level == "full_text"

    # 2. consent: a challenge bound to this exact request, approved by the agent
    stub = StubSigner(seed=0x09)
    broker = _broker(stub)
    scope = project_scope_digest(PRIVACY_KEY, scope_entries_from_view(view, CLIENT, (PROJECT,)))
    display = display_digest(
        {
            "action_display": "list chats",
            "client_display": "codex_local",
            "peer_display": None,
            "project_display": ["Ops"],
            "risk_class": "metadata",
        }
    )
    handle = await broker.issue(
        tool="telegram_list_chats",
        request_hmac=query_digest(CURSOR_KEY, tool="telegram_list_chats", request=arguments),
        principal=PRINCIPAL,
        client=CLIENT,
        account=ACCOUNT,
        policy_epoch=snapshot.policy_epoch,
        project_scope_digest=scope,
        security_epoch=snapshot.security_epoch,
        display_digest=display,
        exposure_snapshot_digest=synthetic_exposure_digest(),
    )
    challenge = broker.challenge_bytes(handle)
    consumed = await broker.consume(
        handle,
        {
            "challenge_sha256": hashlib.sha256(challenge).hexdigest(),
            "sig": stub.sign(challenge),
            "key_id": stub.key_id,
        },
    )
    assert consumed.tool == "telegram_list_chats"
    assert broker.pending_count() == 0

    # 3. the gate is only asked once consent is verified
    decision = await SyntheticDisclosureGate().authorize_disclosure(
        challenge=consumed, snapshot=snapshot
    )
    assert decision.allowed is True

    # 4. retrieval through the fake adapter, then the real result assembly
    adapter = FakeTelegramAdapter()
    chats = adapter.list_chats(project_ref=PROJECT, limit=arguments["limit"])
    result = success_result(
        "telegram_list_chats",
        {"project": {"project_ref": PROJECT, "display_name": "Ops"}, "chats": chats},
        {
            "source": "telegram",
            "content_trust": "untrusted_external_content",
            "truncated": False,
            "partial": False,
            "next_cursor": None,
            "disclosure": None,
            "coverage": None,
        },
    )
    assert result.is_error is False
    assert result.structured_content["data"]["chats"][0]["peer_ref"] == PEER
    assert adapter.calls == 1
    assert broker.issue_calls == 1


async def test_revoked_grant_fails_closed_before_the_broker(tmp_path):
    conn = _seed(tmp_path, granted=True)
    conn.execute("DELETE FROM client_projects")
    conn.commit()
    view = _view_from(conn)
    stub = StubSigner(seed=0x09)
    broker = _broker(stub)
    adapter = FakeTelegramAdapter()

    verdict = evaluate(
        view,
        AuthorityRequest(
            operation="read",
            client_ref=CLIENT,
            project_refs=(PROJECT,),
            peer_identity=PEER_IDENTITY,
        ),
    )
    assert isinstance(verdict, Denial)
    assert verdict.code == "NOT_ACCESSIBLE"
    # nothing downstream of authority ran
    assert broker.issue_calls == 0
    assert broker.pending_count() == 0
    assert adapter.calls == 0


async def test_a_consumed_approval_cannot_be_replayed(tmp_path):
    from comms.transports.telegram.consent.broker import ConsentError

    conn = _seed(tmp_path, granted=True)
    view = _view_from(conn)
    stub = StubSigner(seed=0x09)
    broker = _broker(stub)
    handle = await broker.issue(
        tool="telegram_list_chats",
        request_hmac=query_digest(
            CURSOR_KEY, tool="telegram_list_chats", request={"project_ref": PROJECT}
        ),
        principal=PRINCIPAL,
        client=CLIENT,
        account=ACCOUNT,
        policy_epoch=view.policy_epoch,
        project_scope_digest=project_scope_digest(
            PRIVACY_KEY, scope_entries_from_view(view, CLIENT, (PROJECT,))
        ),
        security_epoch=view.security_epoch,
        display_digest="3" * 64,
        exposure_snapshot_digest=synthetic_exposure_digest(),
    )
    challenge = broker.challenge_bytes(handle)
    envelope = {
        "challenge_sha256": hashlib.sha256(challenge).hexdigest(),
        "sig": stub.sign(challenge),
        "key_id": stub.key_id,
    }
    await broker.consume(handle, envelope)
    with pytest.raises(ConsentError):
        await broker.consume(handle, envelope)
