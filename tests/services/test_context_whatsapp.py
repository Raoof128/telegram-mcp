"""comms v0.3 Task D11: WhatsApp archive and campaign-store context sources (P §20, §21)."""

import pytest

from comms.core.delivery import freeze
from comms.core.errors import CommsError
from comms.core.providers.protocols import ProviderTarget
from comms.services.context import ContextEngine
from tests.core import fakes
from tests.core import schema_fixtures as fx
from tests.core.campaign_helpers import NOW, person, ready
from tests.services.context_fixtures import Clock, Source

PHONE = "+61400000001"


@pytest.fixture
def wa_world(tmp_path):
    conn = fx.migrated(tmp_path)
    rcp, pts = person(conn, phone=PHONE)
    identity = conn.execute(
        "SELECT identity FROM delivery_identities WHERE transport = 'whatsapp'"
    ).fetchone()[0]
    target = ProviderTarget("whatsapp", "whatsapp_cloud", pts["wa"], identity)
    return {"conn": conn, "rcp": rcp, "pts": pts, "target": target}


def _engine(conn, sources):
    return ContextEngine(conn, sources, clock=lambda: NOW, monotonic=Clock())


def test_archive_items_labelled(wa_world):
    source = Source(provenance="whatsapp_webhook_archive")
    engine = _engine(wa_world["conn"], {"whatsapp_cloud": source})
    page = engine.archive(wa_world["rcp"], wa_world["target"], limit=3)
    assert page["source"] == "whatsapp_webhook_archive"
    assert [i["source"] for i in page["items"]] == ["whatsapp_webhook_archive"] * 3
    for item in page["items"]:
        assert item["message_ref"].startswith("cmg_") and item["group_ref"] == wa_world["rcp"]
        assert "chat_id" not in item and "message_id" not in item
        assert item["untrusted_text"].startswith("hello ")
    assert source.queries[0].kind == "recent"


def test_campaign_history_items_labelled(wa_world):
    conn = wa_world["conn"]
    cmp = ready(conn, {"recipients": [wa_world["rcp"]]})
    freeze.send(conn, cmp, {"whatsapp": fakes.FakeWhatsApp(conn=conn)}, now=NOW)
    engine = _engine(conn, {})
    page = engine.campaign_history(wa_world["rcp"], wa_world["target"], limit=10)
    assert page["source"] == "campaign_store" and len(page["items"]) == 1
    (item,) = page["items"]
    assert item["source"] == "campaign_store" and item["campaign_ref"] == cmp
    assert item["job_ref"].startswith("djb_") and item["state"] == "PENDING"
    assert item["observed_at"] and item["group_ref"] == wa_world["rcp"]
    assert PHONE not in repr(page)  # no provider identity, no payload
    assert "payload" not in item and "identity" not in item


def test_campaign_history_pages_newest_first(wa_world):
    conn = wa_world["conn"]
    refs = []
    for _ in range(3):
        cmp = ready(conn, {"recipients": [wa_world["rcp"]]})
        freeze.send(conn, cmp, {"whatsapp": fakes.FakeWhatsApp(conn=conn)}, now=NOW)
        refs.append(cmp)
    engine = _engine(conn, {})
    first = engine.campaign_history(wa_world["rcp"], wa_world["target"], limit=2)
    rest = engine.campaign_history(
        wa_world["rcp"], wa_world["target"], limit=2, cursor=first["next_cursor"]
    )
    seen = [i["campaign_ref"] for i in first["items"] + rest["items"]]
    assert seen == refs[::-1] and rest["next_cursor"] is None


@pytest.mark.parametrize("claimed", ["telegram_live", "whatsapp_live", "campaign_store"])
def test_whatsapp_never_claims_live_history(wa_world, claimed):
    engine = _engine(wa_world["conn"], {"whatsapp_cloud": Source(provenance=claimed)})
    with pytest.raises(CommsError) as refused:
        engine.archive(wa_world["rcp"], wa_world["target"], limit=3)
    assert refused.value.code == "PROVIDER_UNAVAILABLE"  # fail closed on a mislabelled source
    with pytest.raises(CommsError):
        engine.search([(wa_world["rcp"], wa_world["target"])], "hello", limit=3)


def test_whatsapp_search_is_labelled_archive_not_provider_history(wa_world):
    engine = _engine(
        wa_world["conn"], {"whatsapp_cloud": Source(provenance="whatsapp_webhook_archive")}
    )
    result = engine.search([(wa_world["rcp"], wa_world["target"])], "hello", limit=3)
    assert {i["source"] for i in result["items"]} == {"whatsapp_webhook_archive"}


def test_archive_refuses_a_telegram_target(tmp_path):
    conn = fx.migrated(tmp_path)
    engine = _engine(conn, {"telegram_user": Source()})
    target = ProviderTarget("telegram", "telegram_user", "dst_x", "-100")
    with pytest.raises(CommsError) as refused:
        engine.archive("grp_x", target, limit=3)
    assert refused.value.code == "INVALID_ARGUMENT"


def test_the_comms_archive_through_the_engine_is_labelled_and_stripped(wa_world):
    """D39-PRE E10b: the real archive as the source; provider identities never leave."""
    import json

    from comms.transports.whatsapp.webhooks.archive import ArchiveContext, CommsArchive

    conn, digits = wa_world["conn"], PHONE.removeprefix("+")
    body = json.dumps({"entry": [{"changes": [{"value": {
        "metadata": {"phone_number_id": "1234567890"},
        "contacts": [{"wa_id": digits, "profile": {"name": "Sara"}}],
        "messages": [{"from": digits, "id": "wamid.X1", "timestamp": "1758800000", "type": "text",
                      "text": {"body": "salaam"}}]}}]}]}).encode()  # fmt: skip
    CommsArchive(conn, clock=lambda: NOW).ingest(body)
    engine = _engine(conn, {"whatsapp_cloud": ArchiveContext(conn, clock=lambda: NOW)})
    page = engine.archive(wa_world["rcp"], wa_world["target"], limit=5)
    (item,) = page["items"]
    assert page["source"] == item["source"] == "whatsapp_webhook_archive"
    assert item["untrusted_text"] == "salaam" and item["untrusted"]["sender_name"] == "Sara"
    assert item["message_ref"].startswith("cmg_")
    assert "wamid.X1" not in json.dumps(page) and digits not in json.dumps(page)
