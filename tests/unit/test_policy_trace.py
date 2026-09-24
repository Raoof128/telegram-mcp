"""One decision function, two entry points (design §2.2, 0B G13, G14)."""

import random

import pytest

from telegram_mcp.authority.policy import (
    AuthorityRequest,
    ClientProjectGrant,
    ClientState,
    Denial,
    OwnerScope,
    PeerFacts,
    ProjectState,
    evaluate,
    evaluate_with_trace,
    make_view,
)


def _view(rng: random.Random):
    peers = [f"user:{i}" for i in range(4)] + ["channel:9", "chat:3"]
    projects = {
        f"p{i}": ProjectState(f"p{i}", enabled=rng.random() > 0.2, project_epoch=1)
        for i in range(3)
    }
    grants = {}
    for ref in projects:
        if rng.random() > 0.3:
            level = rng.choice(("metadata_only", "excerpt", "full_text"))
            grants[("c", ref)] = ClientProjectGrant(
                can_read=rng.random() > 0.2,
                can_cross_search=rng.random() > 0.5,
                egress_level=level,
                excerpt_limit=100 if level == "excerpt" else None,
                grant_digest=f"g{ref}",
            )
    return make_view(
        clients={"c": ClientState("c", enabled=rng.random() > 0.1, principal_ref="prn")},
        projects=projects,
        grants=grants,
        memberships={ref: set(rng.sample(peers, 3)) for ref in projects},
        owner_allows=set(rng.sample(peers, 4)),
        owner_denies=set(rng.sample(peers, 1)),
        owner_mode=rng.choice(("allowlist", "all_cloud_chats")),
        owner_scope=OwnerScope(*(rng.random() > 0.3 for _ in range(4))),
    ), peers


def test_evaluate_and_evaluate_with_trace_agree_everywhere():
    rng = random.Random(20260924)
    for _ in range(2000):
        view, peers = _view(rng)
        request = AuthorityRequest(
            rng.choice(("read", "cross_search", "discover")),
            rng.choice(("c", "missing")),
            tuple(rng.sample(["p0", "p1", "p2", "px"], rng.randint(0, 2))),
            peer_identity=rng.choice([None, *peers]),
        )
        assert evaluate(view, request) == evaluate_with_trace(view, request)[0]


def test_a_success_trace_lists_every_layer_in_order():
    view = make_view(
        clients={"c": ClientState("c", True, "prn")},
        projects={"p": ProjectState("p", True, 1)},
        grants={("c", "p"): ClientProjectGrant(True, False, "full_text", None, "g")},
        memberships={"p": {"user:1"}},
        owner_allows={"user:1"},
        owner_scope=OwnerScope(False, True, True, True),
    )
    request = AuthorityRequest("read", "c", ("p",), "user:1", facts=PeerFacts("private", False))
    verdict, trace = evaluate_with_trace(view, request)
    assert not isinstance(verdict, Denial)
    assert [s for s, _ in trace] == [
        "client",
        "project",
        "client_enabled",
        "grant",
        "owner_deny",
        "owner_allow",
        "membership",
        "owner_class",
        "egress",
    ]
    assert trace[-1] == ("egress", "full_text")


def test_the_class_step_denies_only_on_facts():
    view = make_view(
        clients={"c": ClientState("c", True, "prn")},
        projects={"p": ProjectState("p", True, 1)},
        grants={("c", "p"): ClientProjectGrant(True, False, "full_text", None, "g")},
        memberships={"p": {"user:1"}},
        owner_allows={"user:1"},
        owner_scope=OwnerScope(False, False, True, True),  # private excluded
    )
    denied, trace = evaluate_with_trace(
        view, AuthorityRequest("read", "c", ("p",), "user:1", facts=PeerFacts("private", False))
    )
    assert isinstance(denied, Denial) and trace[-1] == ("owner_class", "deny:NOT_ACCESSIBLE")
    allowed, trace = evaluate_with_trace(view, AuthorityRequest("read", "c", ("p",), "user:1"))
    assert not isinstance(allowed, Denial) and ("owner_class", "facts_unknown") in trace


@pytest.mark.parametrize(
    ("scope", "facts", "expected"),
    [
        (OwnerScope(True, True, True, True), PeerFacts("private", None), True),
        (OwnerScope(False, True, True, True), PeerFacts("private", None), None),
        (OwnerScope(False, False, True, True), PeerFacts("private", None), False),
        (OwnerScope(False, True, True, True), PeerFacts(None, True), False),
        (OwnerScope(True, True, True, True), PeerFacts(None, None), None),
        (OwnerScope(False, True, False, True), PeerFacts("group", False), False),
        # stored channel: broadcast or supergroup (review #8)
        (OwnerScope(True, True, False, False), PeerFacts.from_stored("channel"), False),
        (OwnerScope(True, True, True, False), PeerFacts.from_stored("channel"), None),
        (
            OwnerScope(True, True, True, True),
            PeerFacts(None, False, ("supergroup", "channel")),
            True,
        ),
    ],
)
def test_owner_scope_decide(scope, facts, expected):
    assert scope.decide(facts) is expected


def test_stored_facts_are_exact_only_where_the_type_maps_exactly():
    assert PeerFacts.from_stored("user") == PeerFacts("private", None)
    assert PeerFacts.from_stored("chat") == PeerFacts("group", None)
    assert PeerFacts.from_stored("channel") == PeerFacts(None, None, ("supergroup", "channel"))


def test_seams_reexports_the_one_owner_scope():
    from telegram_mcp.authority import policy
    from telegram_mcp.disclosure import seams

    assert seams.OwnerScope is policy.OwnerScope


def test_admit_live_is_the_evaluator_with_live_facts():
    from telegram_mcp.authority.policy import admit_live

    view = make_view(
        clients={"c": ClientState("c", True, "prn")},
        projects={"p": ProjectState("p", True, 1)},
        grants={("c", "p"): ClientProjectGrant(True, False, "full_text", None, "g")},
        memberships={"p": {"user:1", "channel:2"}},
        owner_allows={"user:1", "channel:2"},
        owner_scope=OwnerScope(False, True, True, False),  # archived and channels excluded
    )
    read = lambda identity: AuthorityRequest("read", "c", ("p",), identity)
    assert admit_live(view, read("user:1"), chat_type="private", archived=False) is True
    assert admit_live(view, read("user:1"), chat_type="private", archived=True) is False
    assert admit_live(view, read("channel:2"), chat_type="channel", archived=False) is False
    assert admit_live(view, read("channel:2"), chat_type="supergroup", archived=False) is True
    _verdict, trace = evaluate_with_trace(
        view,
        AuthorityRequest("read", "c", ("p",), "user:1", facts=PeerFacts("private", True)),
    )
    assert trace[-1] == ("owner_class", "deny:NOT_ACCESSIBLE")
