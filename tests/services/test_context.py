"""comms v0.3 Task D8: the context engine — provenance on every item, P §71 search bounds,
untrusted text (P §19–22, §71; A32)."""

import pytest

from comms.core.errors import CommsError
from comms.core.providers.protocols import ContextPage, ProviderTarget
from comms.services.context import SEARCH_BOUNDS, ContextEngine
from tests.core.campaign_helpers import NOW
from tests.services.context_fixtures import Clock, Source


def _engine(world, sources, clock=None):
    return ContextEngine(world["conn"], sources, clock=lambda: NOW, monotonic=clock or Clock())


def test_every_item_has_provenance(world):
    source = Source()
    grp, target = world["targets"][0]
    page = _engine(world, {"telegram_user": source}).recent(grp, target, limit=3)
    assert page["source"] == "telegram_live" and page["group_ref"] == grp
    for item in page["items"]:
        assert item["source"] == "telegram_live" and item["observed_at"]
        assert item["message_ref"].startswith("cmg_") and item["group_ref"] == grp
    again = _engine(world, {"telegram_user": source}).recent(grp, target, limit=3)
    assert [i["message_ref"] for i in again["items"]] != [] and again["items"][0][
        "message_ref"
    ] == page["items"][0]["message_ref"]


def test_text_in_untrusted_field_only(world):
    grp, target = world["targets"][0]
    page = _engine(world, {"telegram_user": Source()}).recent(grp, target, limit=3)
    for item in page["items"]:
        assert item["untrusted_text"].startswith("hello") and item["untrusted"] == {
            "sender_name": "Ali"
        }
        assert "text" not in item and "message_id" not in item  # a provider id never leaves
        assert "sender_id" not in item and "chat_id" not in item


def test_search_bounds_enforced(world):
    assert SEARCH_BOUNDS == {
        "results": 50,
        "groups": 10,
        "requests": 20,
        "messages": 2000,
        "seconds": 20.0,
    }
    source = Source(per_page=2, pages=100)  # 20 requests x 2 = 40 results: the request bound bites
    groups = world["targets"][:2]
    result = _engine(world, {"telegram_user": source}).search(groups, "hello", limit=50)
    assert len(result["items"]) <= 50 and len(source.queries) <= SEARCH_BOUNDS["requests"]
    assert result["stopped_by"] == "requests"
    many = world["targets"][:12]
    with pytest.raises(CommsError) as refused:
        _engine(world, {"telegram_user": Source()}).search(many, "hello")
    assert refused.value.code == "INVALID_ARGUMENT"  # more groups than a search may examine
    clock = Clock()

    class Slow(Source):
        def read(self, query):
            clock.t += 6.0
            return super().read(query)

    slow = _engine(world, {"telegram_user": Slow(pages=100)}, clock).search(groups, "hello")
    assert slow["stopped_by"] == "seconds"
    small = _engine(world, {"telegram_user": Source(per_page=30, pages=100)}).search(
        groups, "hello", limit=40
    )
    assert len(small["items"]) == 40 and small["stopped_by"] == "results"
    with pytest.raises(CommsError):
        _engine(world, {"telegram_user": Source()}).search(groups, "hello", limit=51)


def test_search_never_expands_across_accounts_implicitly(world):
    (grp_a, user_target), (grp_b, _other) = world["targets"][:2]
    bot_target = ProviderTarget("telegram", "telegram_bot", _other.destination_ref, _other.identity)
    engine = _engine(
        world,
        {
            "telegram_user": Source(pages=1),
            "telegram_bot": Source(provenance="telegram_local", pages=1),
        },
    )
    with pytest.raises(CommsError) as refused:
        engine.search([(grp_a, user_target), (grp_b, bot_target)], "hello")
    assert refused.value.code == "INVALID_ARGUMENT"
    both = engine.search([(grp_a, user_target), (grp_b, bot_target)], "hello", across_accounts=True)
    assert {i["source"] for i in both["items"]} == {"telegram_live", "telegram_local"}


def test_a_source_refusal_becomes_its_error(world):
    grp, target = world["targets"][0]
    with pytest.raises(CommsError) as refused:
        _engine(world, {"telegram_user": Source(refuse="PROVIDER_UNSUPPORTED")}).recent(grp, target)
    assert refused.value.code == "PROVIDER_UNSUPPORTED"
    with pytest.raises(CommsError) as missing:
        _engine(world, {}).recent(grp, target)
    assert missing.value.code == "NOT_CONFIGURED"


def test_get_assembles_the_requested_sections(world):
    grp, target = world["targets"][0]

    class Rich(Source):
        def read(self, query):
            if query.kind == "members":
                self.queries.append(query)
                return ContextPage(
                    (
                        {
                            "source": "telegram_live",
                            "observed_at": "t",
                            "user_id": 42,
                            "role": "admin",
                            "untrusted": {"name": "Ali"},
                        },
                    ),
                    "telegram_live",
                )
            return super().read(query)

    got = _engine(world, {"telegram_user": Rich()}).get(
        grp, target, include=["messages", "members"], message_limit=2
    )
    assert set(got) == {"group_ref", "messages", "members"}
    assert len(got["messages"]["items"]) <= 3 and got["members"]["items"][0]["role"] == "admin"
    assert "user_id" not in got["members"]["items"][0]
    with pytest.raises(CommsError):
        _engine(world, {"telegram_user": Rich()}).get(grp, target, include=["everything"])
