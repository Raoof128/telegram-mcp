"""comms v0.3 Task D17: template, media, account, capability and identity-inspect services
(P §32–35, §44)."""

import json

import pytest

from comms.core import refs
from comms.core.errors import CommsError
from comms.core.objects import object_ref
from comms.core.providers.capability import Capability as C
from comms.core.providers.capability import CapabilityState as S
from comms.core.providers.protocols import ProviderTarget
from comms.core.providers.semantics import SUPPORT
from comms.services.account import AccountService
from comms.services.context import ContextEngine
from comms.services.directory import DirectoryService
from comms.services.identity import IdentityService
from comms.services.media import MediaService
from comms.services.resolve import resolve_group, resolve_person
from comms.services.templates import TemplateService
from tests.core.campaign_helpers import NOW
from tests.services.context_fixtures import Clock, Source
from tests.services.group_fixtures import CTX, TG_USER, WA_PHONE, fixtures, group_world

ACCOUNT = ProviderTarget("whatsapp", "whatsapp_cloud", "acct", "waba:102290129340398")
CANARIES = (WA_PHONE, WA_PHONE[1:], TG_USER, "-77", "102290129340398", "7788990011")


class Templates:
    """A template source double: one approved template."""

    def list(self, *, limit=50, cursor=None):
        item = {
            "name": "spring",
            "language": "en",
            "status": "APPROVED",
            "category": "MARKETING",
            "schema_version": 1,
            "components": [{"type": "BODY", "text": "Hi"}],
        }
        return type("Page", (), {"items": (item,), "next_cursor": None})()

    def get(self, name, language):
        page = self.list()
        return next((t for t in page.items if (t["name"], t["language"]) == (name, language)), None)


class Media:
    def info(self, media_id):
        return {"mime_type": "image/png", "file_size": 1234, "sha256": "ab" * 32}


class WebhookState:
    def counts(self):
        return {"pending": 2, "completed": 9, "configured": True}


@pytest.fixture
def world(tmp_path):
    w = group_world(tmp_path)
    capability, executor, admins = fixtures(w)
    w.update(capability=capability, executor=executor, admins=admins)
    return w


def _req():
    return refs.mint("request")


def _templates(world):
    return TemplateService(world["conn"], world["capability"], world["executor"], Templates())


def _media(world):
    return MediaService(world["conn"], world["capability"], world["executor"], Media())


# -- capability (P §35) ---------------------------------------------------------------------


def test_capability_for_group_matrix_shape(world):
    matrix = world["capability"].for_group(
        world["grp"], {"telegram_bot": world["bot"], "telegram_user": world["user"]}
    )
    assert set(matrix) == {"group_ref", "actors"} and matrix["group_ref"] == world["grp"]
    assert set(matrix["actors"]) == {"telegram_bot", "telegram_user"}
    for states in matrix["actors"].values():
        assert set(states) == {c.value for c in C} and set(states.values()) <= {s.value for s in S}
    json.dumps(matrix)  # plain JSON: ids and states only


def test_capability_list_get_for_actor_and_refresh(world):
    service = world["capability"]
    listed = service.list()
    assert set(listed["telegram_bot"]) == {
        c.value for c, actors in SUPPORT.items() if "telegram_bot" in actors
    }
    got = service.get(world["grp"], "telegram_bot", world["bot"], C.MEMBER_BAN)
    assert got == {
        "group_ref": world["grp"],
        "actor": "telegram_bot",
        "capability": "member.ban",
        "state": "AVAILABLE",
    }
    by_actor = service.for_actor("telegram_bot", [(world["grp"], world["bot"])])
    assert by_actor["actor"] == "telegram_bot" and world["grp"] in by_actor["groups"]
    refreshed = service.refresh(world["grp"], {"telegram_bot": world["bot"]})
    assert refreshed["actors"]["telegram_bot"]["member.ban"] == "AVAILABLE"


# -- templates (P §32) -----------------------------------------------------------------------


def test_template_list_and_get_are_reads(world):
    service = _templates(world)
    listed = service.list()
    assert [t["name"] for t in listed["items"]] == ["spring"] and listed["next_cursor"] is None
    assert service.get("spring", "en")["status"] == "APPROVED"
    with pytest.raises(CommsError) as refused:
        service.get("autumn", "en")
    assert refused.value.code == "NOT_FOUND"


def test_template_create_edit_delete_are_audited_writes(world):
    service = _templates(world)
    created = service.create(
        CTX,
        ACCOUNT,
        {
            "name": "spring_two",
            "language": "en",
            "category": "MARKETING",
            "components": [{"type": "BODY", "text": "Hi"}],
        },
        _req(),
    )
    assert created["result"] == "SUCCEEDED" and created["template"].startswith("ctp_")
    edited = service.edit(
        CTX, ACCOUNT, created["template"], [{"type": "BODY", "text": "Hello"}], _req()
    )
    deleted = service.delete(CTX, ACCOUNT, "spring_two", _req())
    assert edited["result"] == deleted["result"] == "SUCCEEDED"
    calls = [c for c, _a in world["admins"]["whatsapp_cloud"].calls]
    assert calls == [C.TEMPLATE_CREATE, C.TEMPLATE_EDIT, C.TEMPLATE_DELETE]
    tools = [r[0] for r in world["conn"].execute("SELECT tool FROM mutations ORDER BY id")]
    assert tools == [
        "comms_whatsapp_template_create",
        "comms_whatsapp_template_edit",
        "comms_whatsapp_template_delete",
    ]


# -- media (P §33) ---------------------------------------------------------------------------


def _media_ref(world, media_id="7788990011"):
    return object_ref(world["conn"], "media", "whatsapp", "whatsapp_cloud", None, media_id, now=NOW)


def test_media_inspect_by_ref_never_shows_the_provider_id(world):
    ref = _media_ref(world)
    inspected = _media(world).inspect(ref)
    assert inspected == {"media": ref, "mime": "image/png", "size": 1234, "sha256": "ab" * 32}


def test_media_delete_is_an_audited_write(world):
    ref = _media_ref(world)
    result = _media(world).delete(CTX, ACCOUNT, ref, _req())
    assert result["result"] == "SUCCEEDED"
    assert world["admins"]["whatsapp_cloud"].calls == [(C.MEDIA_DELETE, {"media_id": "7788990011"})]


@pytest.mark.parametrize("call", ["upload", "download"])
def test_media_upload_and_download_are_not_offered_yet(world, call):
    with pytest.raises(CommsError) as refused:
        getattr(_media(world), call)()
    assert refused.value.code == "PROVIDER_UNSUPPORTED"


# -- account (P §34) -------------------------------------------------------------------------


def test_account_status_explains_each_actor(world):
    capability, _executor, _admins = fixtures(world, states={"telegram_user": S.NOT_CONFIGURED})
    account = AccountService(capability, webhooks=WebhookState())
    status = account.status({"telegram_bot": world["bot"], "telegram_user": world["user"]})
    assert status["actors"]["telegram_bot"]["configured"] is True
    assert status["actors"]["telegram_user"] == {
        "configured": False,
        "available": 0,
        "reasons": {"NOT_CONFIGURED": len(C)},
    }
    assert account.webhook_status() == {"configured": True, "pending": 2, "completed": 9}


# -- identity inspect (P §44) ----------------------------------------------------------------


def test_identity_inspect_returns_provider_identities_only_when_asked(world):
    identity = IdentityService(world["conn"])
    assert identity.inspect(world["grp"])["identities"] == [
        {"transport": "telegram", "identity": "-77"}
    ]
    person = identity.inspect(world["rcp"])["identities"]
    assert {"transport": "whatsapp", "identity": WA_PHONE} in person
    assert {"transport": "telegram", "identity": TG_USER} in person
    with pytest.raises(CommsError) as refused:
        identity.inspect("rcp_" + "z" * 26)
    assert refused.value.code == "NOT_FOUND"


def test_identity_inspect_is_the_only_identity_bearing_output(world):
    """A canary sweep: every other service's output over the same world holds no identity."""
    conn = world["conn"]
    capability, executor, _admins = fixtures(world)
    outputs = []
    outputs.append(resolve_person(conn, "Ali", refuse=False))
    outputs.append(resolve_group(conn, "G", now=NOW, refuse=False))
    outputs.append(capability.for_group(world["grp"], {"telegram_bot": world["bot"]}))
    engine = ContextEngine(
        conn,
        {"telegram_bot": Source(provenance="telegram_local")},
        clock=lambda: NOW,
        monotonic=Clock(),
    )
    outputs.append(engine.recent(world["grp"], world["bot"], limit=3))
    directory = DirectoryService(world["writer"], executor)
    aud = directory.audience_create(CTX, "Families", _req())["audience"]
    directory.audience_add(CTX, aud, world["rcp"], _req())
    outputs += [
        directory.audience_get(aud),
        directory.audience_resolve(aud),
        directory.location_list(),
    ]
    outputs += [_templates(world).list(), _media(world).inspect(_media_ref(world))]
    outputs.append(
        AccountService(capability, webhooks=WebhookState()).status({"telegram_bot": world["bot"]})
    )
    text = repr(outputs)
    leaked = [c for c in CANARIES if c in text]
    assert leaked == [], leaked
    inspected = repr(IdentityService(conn).inspect(world["rcp"]))
    assert WA_PHONE in inspected  # the one place it may appear
