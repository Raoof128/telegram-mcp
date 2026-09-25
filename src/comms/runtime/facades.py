"""The runtime facades: every catalog service name bound to a typed service call (comms v0.3
Task D30; A37).

``build_registry`` registers one callable per ``ToolSpec.service``; the dispatcher (MCP) and
the admin socket (CLI) both call through it, so the two reach the same service by construction.
A facade turns validated tool arguments into a service call: a ``grp_`` ref becomes provider
targets for the configured actors, a request id and the caller become the call context, and a
context page's provider cursor becomes a client-bound ``cur_`` token (D9). The few tools whose
services are not offered yet answer ``PROVIDER_UNSUPPORTED`` (``NOT_OFFERED``).
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from comms.core.campaigns.directory import member_identity
from comms.core.canonical import jcs_dumps
from comms.core.errors import CommsError
from comms.core.groups import GroupError, group_identity
from comms.core.objects import resolve_object
from comms.core.providers.capability import Capability
from comms.core.providers.protocols import ProviderTarget
from comms.mcp.catalog import TOOL_CATALOG
from comms.mcp.dispatch import AuthenticatedClient
from comms.services.account import AccountService
from comms.services.campaigns import CampaignService
from comms.services.capability import CapabilityService
from comms.services.context import ContextEngine
from comms.services.directory import DirectoryService
from comms.services.groups import GroupService
from comms.services.handles import ContextHandles
from comms.services.identity import IdentityService
from comms.services.media import MediaService
from comms.services.messages import MessageService
from comms.services.mutations import CallContext
from comms.services.registry import ServiceRegistry
from comms.services.templates import TemplateService

__all__ = ["NOT_OFFERED", "Services", "build_registry", "group_targets"]

Facade = Callable[[AuthenticatedClient, dict[str, Any]], dict[str, Any]]
_TELEGRAM = ("telegram_bot", "telegram_user")
_SOURCE_ACTOR = {"telegram_live": "telegram_user", "telegram_local": "telegram_bot"}
# Tools whose services are not offered yet (each named in the D-task that catalogued it).
NOT_OFFERED = frozenset(
    {
        "message.forward",
        "media.upload",
        "media.download",
        "group.members_get",
        "group.permissions_get",
        "group.topic_get",
        "group.invite_list",
        "group.join_requests_list",
        "group.topic_list",
        "group.admin_log",
        "group.create",
        "account.profile",
        "whatsapp.phone_status",
    }
)


@dataclass(frozen=True)
class Services:
    conn: Any
    capability: CapabilityService
    context: ContextEngine
    handles: ContextHandles
    groups: GroupService
    messages: MessageService
    campaigns: CampaignService
    directory: DirectoryService
    templates: TemplateService | None
    media: MediaService | None
    account: AccountService
    identity: IdentityService
    actors: tuple[str, ...]
    account_target: ProviderTarget | None = None


def group_targets(conn: Any, group: str, actors: tuple[str, ...]) -> dict[str, ProviderTarget]:
    """The configured Telegram actors' targets for a ``grp_`` ref; ``NOT_FOUND`` if unknown."""
    try:
        destination, identity = group_identity(conn, group)
    except GroupError:
        raise CommsError("NOT_FOUND") from None
    targets = {
        actor: ProviderTarget("telegram", actor, destination, identity)
        for actor in actors
        if actor in _TELEGRAM
    }
    if not targets:
        raise CommsError("NOT_CONFIGURED")
    return targets


def _ctx(client: AuthenticatedClient) -> CallContext:
    return CallContext(client_ref=client.client_ref)


def _not_offered(client: AuthenticatedClient, arguments: dict[str, Any]) -> dict[str, Any]:
    raise CommsError("PROVIDER_UNSUPPORTED")


class _Facades:
    def __init__(self, s: Services) -> None:
        self.s = s

    # -- shared --------------------------------------------------------------------------

    def targets(self, group: str) -> dict[str, ProviderTarget]:
        return group_targets(self.s.conn, group, self.s.actors)

    def reader(
        self, group: str, capability: Capability = Capability.HISTORY_READ
    ) -> ProviderTarget:
        """The target a context read uses: the user account when it can read, else the bot."""
        targets = self.targets(group)
        user = targets.get("telegram_user")
        if (
            user is not None
            and self.s.capability.state("telegram_user", user, capability).value == "AVAILABLE"
        ):
            return user
        return targets.get("telegram_bot") or next(iter(targets.values()))

    def message_id(self, message: str, target: ProviderTarget) -> int:
        found = resolve_object(self.s.conn, message, "message")
        chat, _sep, message_id = found.provider_identity.rpartition(":")
        if chat != target.identity or not message_id.isdigit():
            raise CommsError("NOT_FOUND")
        return int(message_id)

    def tokened(
        self, client: AuthenticatedClient, page: dict[str, Any], kind: str, group: str,
        actor: str, args: Mapping[str, Any],
    ) -> dict[str, Any]:  # fmt: skip
        """A page whose provider cursor becomes a client-bound ``cur_`` token (D9)."""
        raw = page.get("next_cursor")
        if raw is None:
            return {**page, "next_cursor": None}
        snapshot = {"kind": kind, "group": group, "actor": actor, "args": dict(args)}
        digest = hashlib.sha256(jcs_dumps(snapshot)).hexdigest()
        ctx_ref = self.s.handles.open(
            client=client.client_ref, owner=client.client_ref, target_ref=group, actor=actor,
            query_digest=digest, snapshot=snapshot,
        )  # fmt: skip
        token = self.s.handles.cursor(client.client_ref, ctx_ref, {"cursor": raw})
        return {**page, "next_cursor": token}

    def run_page(
        self, client: AuthenticatedClient, kind: str, group: str, actor: str,
        args: Mapping[str, Any], cursor: str | None = None,
    ) -> dict[str, Any]:  # fmt: skip
        target = self.targets(group)[actor]
        limit = int(args.get("limit", 20))
        if kind == "recent":
            page = self.s.context.recent(group, target, limit=limit, cursor=cursor)
        elif kind == "members":
            page = self.s.context.get(group, target, include=("members",))["members"]
        else:
            raise CommsError("STALE_HANDLE")
        return self.tokened(client, page, kind, group, actor, args)

    # -- context ---------------------------------------------------------------------------

    def context_recent(self, client: AuthenticatedClient, a: dict[str, Any]) -> dict[str, Any]:
        target = self.reader(a["group"])
        return self.run_page(
            client, "recent", a["group"], target.actor, {"limit": a.get("limit", 20)}
        )

    def context_page(self, client: AuthenticatedClient, a: dict[str, Any]) -> dict[str, Any]:
        handle, position = self.s.handles.position(client.client_ref, a["cursor"])
        snapshot = handle.snapshot
        return self.run_page(
            client, snapshot["kind"], snapshot["group"], snapshot["actor"], snapshot["args"],
            cursor=position["cursor"],
        )  # fmt: skip

    def context_get(self, client: AuthenticatedClient, a: dict[str, Any]) -> dict[str, Any]:
        target = self.reader(a["group"])
        include = tuple(a.get("include", ("messages",)))
        served = tuple(i for i in include if i in ("messages", "members", "admins"))
        got = self.s.context.get(a["group"], target, include=served or ("messages",),
                                 message_limit=a.get("message_limit", 20))  # fmt: skip
        if "capabilities" in include:
            got["capabilities"] = self.s.capability.for_group(a["group"], self.targets(a["group"]))[
                "actors"
            ]
        return {k: {**v, "next_cursor": None} if isinstance(v, dict) and "items" in v else v
                for k, v in got.items()}  # fmt: skip

    def around(self, client: AuthenticatedClient, a: dict[str, Any]) -> dict[str, Any]:
        target = self.reader(a["group"])
        page = self.s.context.around_message(
            a["group"], target, self.message_id(a["message"], target),
            before=a.get("before", 10), after=a.get("after", 10),
        )  # fmt: skip
        return {**page, "next_cursor": None}

    def thread(self, client: AuthenticatedClient, a: dict[str, Any]) -> dict[str, Any]:
        target = self.reader(a["group"])
        page = self.s.context.thread(a["group"], target, self.message_id(a["message"], target),
                                     limit=a.get("limit", 20))  # fmt: skip
        return {**page, "next_cursor": None}

    def search(self, client: AuthenticatedClient, a: dict[str, Any]) -> dict[str, Any]:
        groups = [(g, self.targets(g)) for g in a["groups"]]
        return self.s.context.search_for(groups, a["query"], limit=a.get("limit", 20))

    def summarize(self, client: AuthenticatedClient, a: dict[str, Any]) -> dict[str, Any]:
        return self.s.context.summarize_source(a["group"], self.targets(a["group"]))

    def message_get(self, client: AuthenticatedClient, a: dict[str, Any]) -> dict[str, Any]:
        return self.around(client, {**a, "before": 0, "after": 0})

    # -- groups and messages ------------------------------------------------------------------

    def member(self, tool: str) -> Facade:
        def call(client: AuthenticatedClient, a: dict[str, Any]) -> dict[str, Any]:
            args = {
                k: v for k, v in a.items() if k not in ("group", "recipient", "actor", "request_id")
            }
            return self.s.groups.member(
                _ctx(client), tool, a["group"], self.targets(a["group"]), a["recipient"], args,
                a["request_id"], actor=a.get("actor"),
            )  # fmt: skip

        return call

    def admin(self, tool: str) -> Facade:
        def call(client: AuthenticatedClient, a: dict[str, Any]) -> dict[str, Any]:
            args = {k: v for k, v in a.items() if k not in ("group", "actor", "request_id")}
            return self.s.groups.admin(
                _ctx(client), tool, a["group"], self.targets(a["group"]), args, a["request_id"],
                actor=a.get("actor"),
            )  # fmt: skip

        return call

    def send(self, client: AuthenticatedClient, a: dict[str, Any]) -> dict[str, Any]:
        return self.s.messages.send(
            _ctx(client), a["group"], self.targets(a["group"]), a["text"], a["request_id"],
            actor=a.get("actor"), reply_to=a.get("message"),
        )  # fmt: skip

    def edit(self, client: AuthenticatedClient, a: dict[str, Any]) -> dict[str, Any]:
        return self.s.messages.edit(_ctx(client), a["group"], self.targets(a["group"]),
                                    a["message"], a["text"], a["request_id"], actor=a.get("actor"))  # fmt: skip

    def delete(self, client: AuthenticatedClient, a: dict[str, Any]) -> dict[str, Any]:
        return self.s.messages.delete(
            _ctx(client), a["group"], self.targets(a["group"]), a["message"], a["request_id"],
            scope=a.get("scope", "everyone"), actor=a.get("actor"),
        )  # fmt: skip

    def pin(self, pinned: bool) -> Facade:
        def call(client: AuthenticatedClient, a: dict[str, Any]) -> dict[str, Any]:
            return self.s.messages.pin(_ctx(client), a["group"], self.targets(a["group"]),
                                       a["message"], a["request_id"], pinned=pinned,
                                       actor=a.get("actor"))  # fmt: skip

        return call

    def mark_read(self, client: AuthenticatedClient, a: dict[str, Any]) -> dict[str, Any]:
        identity = member_identity(self.s.conn, a["conversation"], "whatsapp")
        if identity is None:
            raise CommsError("NOT_FOUND")
        target = ProviderTarget("whatsapp", "whatsapp_cloud", a["conversation"], identity)
        return self.s.messages.mark_read(_ctx(client), a["conversation"], {"whatsapp_cloud": target},
                                         a["message"], a["request_id"])  # fmt: skip

    # -- the rest --------------------------------------------------------------------------

    def account_status(self, actors: tuple[str, ...] | None) -> Facade:
        def call(client: AuthenticatedClient, a: dict[str, Any]) -> dict[str, Any]:
            wanted = actors or self.s.actors
            targets: dict[str, ProviderTarget] = {}
            for actor in wanted:
                if actor == "whatsapp_cloud" and self.s.account_target is not None:
                    targets[actor] = self.s.account_target
                elif actor in _TELEGRAM:
                    targets[actor] = ProviderTarget("telegram", actor, "account", "account")
            return self.s.account.status(targets)

        return call

    def templates(self) -> TemplateService:
        if self.s.templates is None or self.s.account_target is None:
            raise CommsError("NOT_CONFIGURED")
        return self.s.templates

    def media(self) -> MediaService:
        if self.s.media is None or self.s.account_target is None:
            raise CommsError("NOT_CONFIGURED")
        return self.s.media

    def account_target(self) -> ProviderTarget:
        if self.s.account_target is None:
            raise CommsError("NOT_CONFIGURED")
        return self.s.account_target

    def table(self) -> dict[str, Facade]:
        s, c = self.s, _ctx
        return {
            "capability.list": lambda cl, a: {"capabilities": [
                {"capability": cap, "transport": "telegram" if actor.startswith("telegram") else "whatsapp",
                 "state": "SUPPORTED"} for actor, caps in s.capability.list().items() for cap in caps
                if a.get("transport") in (None, "telegram" if actor.startswith("telegram") else "whatsapp")]},
            "context.get": self.context_get,
            "context.recent": self.context_recent,
            "context.around_message": self.around,
            "context.thread": self.thread,
            "context.search": self.search,
            "context.summarize_source": self.summarize,
            "context.page": self.context_page,
            "message.get": self.message_get,
            "message.recent": self.context_recent,
            "message.search": self.search,
            "message.context": self.around,
            "message.send": self.send,
            "message.reply": self.send,
            "message.edit": self.edit,
            "message.delete": self.delete,
            "message.pin": self.pin(True),
            "message.unpin": self.pin(False),
            "message.mark_read": self.mark_read,
            "group.list": lambda cl, a: s.groups.list(limit=a.get("limit", 50), cursor=a.get("cursor")),
            "group.get": lambda cl, a: s.groups.get(a["group"]),
            "group.context": lambda cl, a: self.context_get(cl, {**a, "include": ["messages", "members", "admins"]}),
            "group.capabilities": lambda cl, a: s.capability.for_group(a["group"], self.targets(a["group"])),
            "group.members_list": lambda cl, a: self.run_page(cl, "members", a["group"], self.reader(a["group"], Capability.MEMBER_LIST).actor, {}),
            "group.admins_list": lambda cl, a: {**(g := self.context_get(cl, {**a, "include": ["admins"]}))["admins"]},
            **{f"group.member_{t}": self.member(f"group.member.{t}")
               for t in ("add", "invite", "remove", "ban", "unban", "restrict", "unrestrict")},
            "group.admin_promote": self.member("group.admin.promote"),
            "group.admin_update_rights": self.member("group.admin.update_rights"),
            "group.admin_demote": self.member("group.admin.demote"),
            "group.join_requests_approve": self.member("group.join_requests.approve"),
            "group.join_requests_reject": self.member("group.join_requests.reject"),
            **{f"group.{name.replace('.', '_', 1)}": self.admin(f"group.{name}")
               for name in ("permissions.set", "info.set_title", "info.set_description",
                            "info.set_photo", "invite.create", "invite.edit", "invite.revoke",
                            "topic.create", "topic.edit", "topic.close", "topic.reopen")},
            "group.delete": self.admin("group.delete"),
            "group.migrate": self.admin("group.migrate"),
            "campaign.create": lambda cl, a: s.campaigns.create(c(cl), a["title"], a["request_id"]),
            "campaign.get": lambda cl, a: s.campaigns.get(a["campaign"]),
            "campaign.list": lambda cl, a: s.campaigns.list(limit=a.get("limit", 20), cursor=a.get("cursor")),
            "campaign.set_content": lambda cl, a: s.campaigns.set_content(c(cl), a["campaign"], a["content"], a["request_id"]),
            "campaign.set_targets": lambda cl, a: s.campaigns.set_targets(c(cl), a["campaign"], a["targets"], a["transports"], a["request_id"]),
            "campaign.validate": lambda cl, a: s.campaigns.validate(c(cl), a["campaign"], a["request_id"]),
            "campaign.preview": lambda cl, a: s.campaigns.preview(a["campaign"]),
            "campaign.schedule": lambda cl, a: s.campaigns.schedule(c(cl), a["campaign"], _instant(a["at"]), a["request_id"]),
            "campaign.unschedule": lambda cl, a: s.campaigns.unschedule(c(cl), a["campaign"], a["request_id"]),
            "campaign.send": lambda cl, a: s.campaigns.send(c(cl), a["campaign"], a["request_id"]),
            "campaign.cancel": lambda cl, a: s.campaigns.cancel(c(cl), a["campaign"], a["request_id"]),
            "campaign.retry_failed": lambda cl, a: s.campaigns.retry_failed(c(cl), a["campaign"], a["request_id"]),
            "campaign.resolve_unknown": lambda cl, a: s.campaigns.resolve_unknown(c(cl), a["job"], a["verdict"], a["request_id"]),
            "campaign.status": lambda cl, a: s.campaigns.status(a["campaign"]),
            "campaign.delivery_report": lambda cl, a: s.campaigns.delivery_report(a["campaign"], limit=a.get("limit", 50), cursor=a.get("cursor")),
            "location.list": lambda cl, a: s.directory.location_list(limit=a.get("limit", 50), cursor=a.get("cursor")),
            "location.get": lambda cl, a: s.directory.location_get(a["location"]),
            "location.create": lambda cl, a: s.directory.location_create(c(cl), a["name"], a["request_id"]),
            "location.update": lambda cl, a: s.directory.location_update(c(cl), a["location"], a["name"], a["request_id"]),
            "location.enable": lambda cl, a: s.directory.location_enable(c(cl), a["location"], a["request_id"]),
            "location.disable": lambda cl, a: s.directory.location_disable(c(cl), a["location"], a["request_id"]),
            "audience.list": lambda cl, a: s.directory.audience_list(limit=a.get("limit", 50), cursor=a.get("cursor")),
            "audience.get": lambda cl, a: s.directory.audience_get(a["audience"]),
            "audience.create": lambda cl, a: s.directory.audience_create(c(cl), a["name"], a["request_id"]),
            "audience.update": lambda cl, a: s.directory.audience_update(c(cl), a["audience"], a["name"], a["request_id"]),
            "audience.add": lambda cl, a: s.directory.audience_add(c(cl), a["audience"], a["member"], a["request_id"]),
            "audience.remove": lambda cl, a: s.directory.audience_remove(c(cl), a["audience"], a["member"], a["request_id"]),
            "audience.resolve": lambda cl, a: s.directory.audience_resolve(a["audience"]),
            "whatsapp.template_list": lambda cl, a: self.templates().list(limit=a.get("limit", 50), cursor=a.get("cursor")),
            "whatsapp.template_get": lambda cl, a: self.templates().get(a["name"], a["language"]),
            "whatsapp.template_create": lambda cl, a: self.templates().create(
                c(cl), self.account_target(), {k: a[k] for k in ("name", "language", "category", "components")}, a["request_id"]),
            "whatsapp.template_edit": lambda cl, a: self.templates().edit(c(cl), self.account_target(), a["template"], a["components"], a["request_id"]),
            "whatsapp.template_delete": lambda cl, a: self.templates().delete(c(cl), self.account_target(), a["name"], a["request_id"]),
            "media.inspect": lambda cl, a: self.media().inspect(a["media"]),
            "media.delete": lambda cl, a: self.media().delete(c(cl), self.account_target(), a["media"], a["request_id"]),
            "account.status": self.account_status(None),
            "account.capabilities": lambda cl, a: {"actors": s.capability.list()},
            "telegram.bot_status": self.account_status(("telegram_bot",)),
            "telegram.user_status": self.account_status(("telegram_user",)),
            "whatsapp.account_status": self.account_status(("whatsapp_cloud",)),
            "whatsapp.webhook_status": lambda cl, a: s.account.webhook_status(),
            "capability.get": lambda cl, a: s.capability.get(
                a["group"], a["actor"], self.targets(a["group"])[a["actor"]], _capability(a["capability"])),
            "capability.for_group": lambda cl, a: s.capability.for_group(a["group"], self.targets(a["group"])),
            "capability.for_actor": lambda cl, a: s.capability.for_actor(
                a["actor"], [(g, self.targets(g)[a["actor"]]) for g in a["groups"]]),
            "capability.refresh": lambda cl, a: s.capability.refresh(a["group"], self.targets(a["group"])),
            "admin.identity_inspect": lambda cl, a: s.identity.inspect(a["ref"]),
        }  # fmt: skip


def _instant(text: str) -> datetime:
    try:
        instant = datetime.fromisoformat(text)
    except ValueError:
        raise CommsError("INVALID_ARGUMENT") from None
    if instant.tzinfo is None:
        raise CommsError("INVALID_ARGUMENT")  # a time without a zone is ambiguous
    return instant


def _capability(value: str) -> Capability:
    try:
        return Capability(value)
    except ValueError:
        raise CommsError("INVALID_ARGUMENT") from None


def _keyword(facade: Facade) -> Callable[..., dict[str, Any]]:
    """The registry calls ``fn(client=…, arguments=…)``."""

    def call(*, client: AuthenticatedClient, arguments: dict[str, Any]) -> dict[str, Any]:
        return facade(client, arguments)

    return call


def build_registry(services: Services | None) -> ServiceRegistry:
    """One callable per catalog service; with no services, every one registered as refusing
    (used to prove coverage without a database)."""
    registry = ServiceRegistry()
    table = _Facades(services).table() if services is not None else {}
    for spec in TOOL_CATALOG:
        if spec.service in NOT_OFFERED or services is None:
            registry.register(spec.service, _not_offered)
        else:
            registry.register(spec.service, _keyword(table[spec.service]))
    if services is not None:
        missing = {s.service for s in TOOL_CATALOG} - set(table) - NOT_OFFERED
        if missing:
            raise ValueError("a catalog service has no facade")
    return registry
