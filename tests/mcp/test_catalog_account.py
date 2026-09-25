"""comms v0.3 Task D24: template, media, account, capability and identity-inspect tools
(P §32–35, §44)."""

import pytest

from comms.core import refs
from comms.core.objects import object_ref
from comms.core.providers.capability import Capability as C
from comms.core.providers.protocols import ProviderTarget
from comms.mcp.catalog import TOOL_CATALOG
from comms.mcp.tools.account import ACCOUNT_TOOLS
from comms.services.account import AccountService
from comms.services.identity import IdentityService
from comms.services.media import MediaService
from comms.services.templates import TemplateService
from tests.core.campaign_helpers import NOW
from tests.mcp import family
from tests.services.group_fixtures import CTX, fixtures, group_world
from tests.services.test_templates_media_account import Media, Templates, WebhookState

BY_NAME = {spec.name: spec for spec in ACCOUNT_TOOLS}
NAMES = sorted(BY_NAME)
ACCOUNT = ProviderTarget("whatsapp", "whatsapp_cloud", "acct", "waba:102290129340398")
WRITES = {
    "comms_whatsapp_template_create": C.TEMPLATE_CREATE,
    "comms_whatsapp_template_edit": C.TEMPLATE_EDIT,
    "comms_whatsapp_template_delete": C.TEMPLATE_DELETE,
    "comms_media_upload": C.MEDIA_UPLOAD,
    "comms_media_delete": C.MEDIA_DELETE,
}


@pytest.fixture(scope="module")
def results(tmp_path_factory):
    world = group_world(tmp_path_factory.mktemp("acct"))
    conn, grp, bot, user, rcp = (world[k] for k in ("conn", "grp", "bot", "user", "rcp"))
    capability, executor, _admins = fixtures(world)
    templates = TemplateService(conn, capability, executor, Templates())
    media = MediaService(conn, capability, executor, Media())
    account = AccountService(capability, webhooks=WebhookState())

    def req():
        return refs.mint("request")

    definition = {
        "name": "spring_two",
        "language": "en",
        "category": "MARKETING",
        "components": [{"type": "BODY", "text": "Hi"}],
    }
    created = templates.create(CTX, ACCOUNT, definition, req())
    med = object_ref(conn, "media", "whatsapp", "whatsapp_cloud", None, "7788990011", now=NOW)
    both = {"telegram_bot": bot, "telegram_user": user}
    status = account.status(both)
    produced = {
        "comms_whatsapp_template_list": templates.list(),
        "comms_whatsapp_template_get": templates.get("spring", "en"),
        "comms_whatsapp_template_create": created,
        "comms_whatsapp_template_edit": templates.edit(
            CTX, ACCOUNT, created["template"], [{"type": "BODY", "text": "Hello"}], req()
        ),
        "comms_whatsapp_template_delete": templates.delete(CTX, ACCOUNT, "spring_two", req()),
        "comms_media_inspect": media.inspect(med),
        "comms_media_upload": {  # never produced: upload is not offered yet
            **{k: v for k, v in created.items() if k != "template"},
            "media": None,
        },
        "comms_media_download": {"media": med, "file": "stage_x"},  # never produced: not offered
        "comms_media_delete": media.delete(CTX, ACCOUNT, med, req()),
        "comms_account_status": status,
        "comms_account_profile": {"actors": {"telegram_bot": {"configured": True, "kind": "bot"}}},
        "comms_account_capabilities": {"actors": capability.list()},
        "comms_telegram_bot_status": account.status({"telegram_bot": bot}),
        "comms_telegram_user_status": account.status({"telegram_user": user}),
        "comms_whatsapp_account_status": account.status({"whatsapp_cloud": ACCOUNT}),
        "comms_whatsapp_phone_status": {"quality_rating": "GREEN", "status": "CONNECTED"},
        "comms_whatsapp_webhook_status": account.webhook_status(),
        "comms_capability_get": capability.get(grp, "telegram_bot", bot, C.MEMBER_BAN),
        "comms_capability_for_group": capability.for_group(grp, both),
        "comms_capability_for_actor": capability.for_actor("telegram_bot", [(grp, bot)]),
        "comms_capability_refresh": capability.refresh(grp, both),
        "comms_admin_identity_inspect": IdentityService(conn).inspect(rcp),
    }
    examples = {
        "comms_whatsapp_template_list": {"limit": 10},
        "comms_whatsapp_template_get": {"name": "spring", "language": "en"},
        "comms_whatsapp_template_create": definition,
        "comms_whatsapp_template_edit": {
            "template": created["template"],
            "components": [{"type": "BODY", "text": "Hello"}],
        },
        "comms_whatsapp_template_delete": {"name": "spring_two"},
        "comms_media_inspect": {"media": med},
        "comms_media_upload": {"file": "stage_x", "mime": "image/png"},
        "comms_media_download": {"media": med},
        "comms_media_delete": {"media": med},
        "comms_capability_get": {"group": grp, "actor": "telegram_bot", "capability": "member.ban"},
        "comms_capability_for_group": {"group": grp},
        "comms_capability_for_actor": {"actor": "telegram_bot", "groups": [grp]},
        "comms_capability_refresh": {"group": grp},
        "comms_admin_identity_inspect": {"ref": rcp},
    }
    for name in NAMES:
        examples.setdefault(name, {})
    return {"results": produced, "examples": examples}


@pytest.mark.parametrize("name", NAMES)
def test_schema_valid_json_schema_2020_12(name):
    family.schema_valid(BY_NAME[name])


@pytest.mark.parametrize("name", NAMES)
def test_output_schema_matches_service_result(name, results):
    family.output_matches(BY_NAME[name], results["results"][name])


@pytest.mark.parametrize("name", NAMES)
def test_annotations(name):
    family.annotations(BY_NAME[name], WRITES.get(name))
    assert BY_NAME[name].read_only == (name not in WRITES)


@pytest.mark.parametrize("name", sorted(WRITES))
def test_write_requires_request_id(name, results):
    family.write_requires_request_id(BY_NAME[name], results["examples"][name])


@pytest.mark.parametrize("name", NAMES)
def test_dispatch_reaches_its_service(name, results):
    family.dispatch_reaches_its_service(
        BY_NAME[name], results["examples"][name], results["results"][name]
    )


def test_identity_inspect_is_read_only_and_the_only_identity_output(results):
    spec = BY_NAME["comms_admin_identity_inspect"]
    assert spec.read_only and not spec.destructive
    inspected = repr(results["results"]["comms_admin_identity_inspect"])
    assert "+61400000001" in inspected
    others = repr({k: v for k, v in results["results"].items() if k != spec.name})
    assert "+61400000001" not in others and "61400000001" not in others


def test_every_catalog_output_schema_names_identity_only_in_the_inspect_tool():
    for spec in TOOL_CATALOG:
        text = repr(spec.output_schema)
        if spec.name != "comms_admin_identity_inspect":
            assert "'identity'" not in text, spec.name
