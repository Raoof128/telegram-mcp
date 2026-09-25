"""comms v0.3 Task D36: typed egress — bodies only where content reads require them; identities
only via inspect; secrets never (A44, G19).

Canaries are planted in a running world — a message body, a display name, a phone number, a
Telegram user id and a group's chat id, a bot token, a lease seed and an OAuth owner code —
then every catalog tool is driven through the real facades and dispatcher, and every output,
the CLI's printed JSON, the logs and every file in the database directory are swept.
"""

import json
import logging
import os
from datetime import UTC, datetime

import pytest

from comms.core import refs
from comms.core.auth import clients, lease_format
from comms.core.credentials import rotate_credential
from comms.core.delivery.commitment import commit_context
from comms.core.keys import rotate as rot
from comms.core.keys.secrets import FileSecretStore
from comms.core.providers.protocols import ContextPage
from comms.mcp.catalog import TOOL_CATALOG
from comms.mcp.dispatch import AuthenticatedClient, Dispatcher
from comms.mcp.egress import CLASSES, EGRESS_MATRIX
from comms.mcp.oauth.store import OwnerApprovals
from comms.runtime.facades import Services, build_registry
from comms.runtime.tool_calls import tool_call_handler
from comms.services.account import AccountService
from comms.services.campaigns import CampaignService
from comms.services.context import ContextEngine
from comms.services.directory import DirectoryService
from comms.services.groups import GroupService
from comms.services.handles import ContextHandles
from comms.services.identity import IdentityService
from comms.services.messages import MessageService
from tests.core import fakes
from tests.core.campaign_helpers import NOW
from tests.services.context_fixtures import Clock
from tests.services.group_fixtures import TG_USER, WA_PHONE, fixtures, group_world
from tests.services.test_templates_media_account import WebhookState

BODY = "CANARY-BODY-7f3a91"
NAME = "CANARY-NAME-c2e8"
IDENTITIES = (WA_PHONE, WA_PHONE[1:], TG_USER, "-77")
BOT_TOKEN = "908180123:AAE-canary-bot-token-0987654321"
CLIENT = AuthenticatedClient(client_ref="cli_" + "a" * 26, auth_kind="cml1")


class CanarySource:
    """A provider page: messages carry the body canary, member lists the name canary."""

    def __init__(self, provenance):
        self.provenance = provenance

    def read(self, query):
        stamp = {"source": self.provenance, "observed_at": "2026-09-25T00:00:00.000000Z"}
        if query.kind == "members":
            items = ({**stamp, "role": "admin", "user_id": 9, "untrusted": {"name": NAME}},)
            return ContextPage(items, self.provenance, None)
        start = int(query.args.get("cursor") or 1000)
        items = tuple(
            {
                **stamp,
                "message_id": start - i,
                "chat_id": "-77",
                "sender_id": TG_USER,
                "untrusted": {"text": f"{BODY} {start - i}", "sender_name": NAME},
            }
            for i in range(3)
        )
        return ContextPage(items, self.provenance, str(start - 3) if start > 990 else None)


@pytest.fixture(scope="module")
def sweep(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("egress")
    w = group_world(tmp)
    for purpose in ("campaign-commit-key", "cursor-key", "oauth-signing-key"):
        rot.rotate(
            w["writer"], w["store"], purpose, material=os.urandom(32), prove=lambda m: None, now=NOW
        )
    secrets = FileSecretStore(tmp / "secrets")
    rotate_credential(
        w["writer"],
        secrets,
        "telegram-bot-token",
        BOT_TOKEN.encode(),
        prove=lambda v: None,
        now=NOW,
    )
    (tmp / "helper").mkdir(mode=0o700)
    clients.add_client(
        w["conn"], w["store"], "c", now=datetime.now(UTC), helper_path=tmp / "helper" / "seed"
    )
    seed = lease_format.read_helper(tmp / "helper" / "seed")[1]
    owner_code = OwnerApprovals(lambda: datetime.now(UTC)).issue()
    capability, executor, _admins = fixtures(w)
    conn = w["conn"]
    services = Services(
        conn=conn,
        capability=capability,
        context=ContextEngine(
            conn,
            {
                "telegram_user": CanarySource("telegram_live"),
                "telegram_bot": CanarySource("telegram_local"),
            },
            clock=lambda: NOW,
            monotonic=Clock(),
            capability=capability,
        ),
        handles=ContextHandles(conn, w["store"], clock=lambda: NOW),
        groups=GroupService(conn, capability, executor),
        messages=MessageService(conn, capability, executor),
        campaigns=CampaignService(
            w["writer"],
            executor,
            {"whatsapp": fakes.FakeWhatsApp(conn=conn)},
            commit=lambda: commit_context(w["writer"], w["store"]),
        ),
        directory=DirectoryService(w["writer"], executor),
        templates=None,
        media=None,
        account=AccountService(capability, webhooks=WebhookState()),
        identity=IdentityService(conn),
        actors=("telegram_bot", "telegram_user"),
    )
    dispatcher = Dispatcher(build_registry(services))
    first = dispatcher.call(CLIENT, "comms_context_recent", {"group": w["grp"], "limit": 3})
    message = first.structured["items"][0]["message_ref"]
    values = {
        "group": w["grp"],
        "groups": [w["grp"]],
        "recipient": w["rcp"],
        "message": message,
        "to_group": w["grp"],
        "ref": w["rcp"],
        "query": BODY.split("-")[0],
        "text": "hi",
        "title": "T",
        "name": "N",
        "kind": "supergroup",
        "location": "loc_" + "a" * 26,
        "cursor": first.structured["next_cursor"],
        "profile": "moderator",
        "actor": "telegram_bot",
        "capability": "member.ban",
        "description": "d",
        "permissions": {"can_send_messages": True},
        "conversation": w["rcp"],
        "campaign": "cmp_" + "a" * 26,
        "job": "djb_" + "a" * 26,
        "verdict": "sent",
        "audience": "cau_" + "a" * 26,
        "member": w["rcp"],
        "at": "2026-12-01T00:00:00Z",
        "content": {"canonical": "x"},
        "targets": {"recipients": []},
        "transports": ["whatsapp"],
        "media": "med_" + "a" * 26,
        "file": "f",
        "mime": "image/png",
        "language": "en",
        "category": "MARKETING",
        "components": [{"type": "BODY", "text": "x"}],
        "template": "ctp_" + "a" * 26,
        "invite": "inv_" + "a" * 26,
        "topic": "top_" + "a" * 26,
    }
    outputs = {}
    with pytest.MonkeyPatch.context() as mp:
        records = []
        handler = logging.Handler()
        handler.emit = lambda record: records.append(record.getMessage())
        logging.getLogger().addHandler(handler)
        mp.setattr(logging.getLogger(), "level", logging.DEBUG)
        try:
            for spec in TOOL_CATALOG:
                arguments = {
                    k: values[k] for k in spec.input_schema.get("required", []) if k in values
                }
                if spec.requires_request_id:
                    arguments["request_id"] = refs.mint("request")
                result = dispatcher.call(CLIENT, spec.name, arguments)
                outputs[spec.name] = result.to_mcp()
            cli = tool_call_handler(dispatcher)(
                {"tool": "comms_context_recent", "arguments": {"group": w["grp"], "limit": 3}}
            )
        finally:
            logging.getLogger().removeHandler(handler)
    files = b"".join(p.read_bytes() for p in tmp.glob("comms.db*"))
    secrets_text = (BOT_TOKEN, seed.hex(), lease_format.encode(seed), owner_code)
    return {
        "outputs": outputs,
        "cli": cli,
        "logs": "\n".join(records),
        "files": files,
        "secrets": secrets_text,
    }


def _strings(value, path=()):
    if isinstance(value, str):
        yield path, value
    elif isinstance(value, dict):
        for key, inner in value.items():
            yield from _strings(inner, (*path, key))
    elif isinstance(value, list):
        for inner in value:
            yield from _strings(inner, path)


def test_every_tool_declares_its_egress_class():
    assert set(EGRESS_MATRIX) == {s.name for s in TOOL_CATALOG}
    assert all(classes <= CLASSES and "refs" in classes for classes in EGRESS_MATRIX.values())
    for spec in TOOL_CATALOG:  # a tool that may carry text has a schema field for it
        carries_text = "untrusted_text" in json.dumps(spec.output_schema)
        assert (
            "body" in EGRESS_MATRIX[spec.name] or "names" in EGRESS_MATRIX[spec.name]
        ) == carries_text, spec.name


def test_the_body_canary_appears_exactly_in_the_content_reads(sweep):
    carried = set()
    for name, payload in sweep["outputs"].items():
        for path, text in _strings(payload):
            if BODY in text:
                assert "body" in EGRESS_MATRIX[name], (name, path)
                assert path[-1] == "untrusted_text", (name, path)
                carried.add(name)
    assert {"comms_context_recent", "comms_context_get", "comms_message_get"} <= carried


def test_display_names_only_in_untrusted_fields_of_listing_reads(sweep):
    for name, payload in sweep["outputs"].items():
        for path, text in _strings(payload):
            if NAME in text:
                assert EGRESS_MATRIX[name] & {"names", "body"}, (name, path)
                assert "untrusted" in path, (name, path)


def test_identity_canaries_appear_only_in_inspect(sweep):
    for name, payload in sweep["outputs"].items():
        text = json.dumps(payload)
        leaked = [c for c in IDENTITIES if c in text]
        if name == "comms_admin_identity_inspect":
            assert WA_PHONE in text and TG_USER in text
        else:
            assert leaked == [], (name, leaked)


def test_secrets_appear_nowhere(sweep):
    surfaces = [json.dumps(sweep["outputs"]), json.dumps(sweep["cli"]), sweep["logs"]]
    for secret in sweep["secrets"]:
        for surface in surfaces:
            assert secret not in surface


def test_the_database_files_hold_no_plaintext_canary(sweep):
    assert sweep["files"]  # the encrypted database was swept
    for canary in (BODY, NAME, BOT_TOKEN, *IDENTITIES[:3]):
        assert canary.encode() not in sweep["files"], canary


def test_the_cli_prints_what_the_dispatcher_answers(sweep):
    printed = json.dumps(sweep["cli"])
    assert BODY in printed  # a content read through the CLI carries its body, as over MCP
    assert not [c for c in IDENTITIES if c in printed]
