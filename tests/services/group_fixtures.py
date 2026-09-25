"""Shared group-service test doubles (D12, D13): real adapter validation, scripted outcomes."""

from datetime import timedelta
from itertools import count

from comms.core.campaigns import directory as d
from comms.core.groups import group_ref
from comms.core.providers.capability import Capability as C
from comms.core.providers.capability import CapabilityState as S
from comms.core.providers.protocols import CapabilitySnapshot, ProviderResult, ProviderTarget
from comms.services.capability import CapabilityService
from comms.services.groups import GroupService
from comms.services.mutations import CallContext, MutationExecutor
from comms.transports.telegram.bot.admin import BotAdmin
from comms.transports.telegram.user.admin import UserAdmin
from comms.transports.whatsapp.cloud.groups import WhatsAppAdmin
from tests.core import schema_fixtures as fx
from tests.core.audit.legacy_fixtures import comms_world
from tests.core.campaign_helpers import NOW

CTX = CallContext(client_ref="cli_" + "c" * 26)
TG_USER, WA_PHONE = "908180", "+61400000001"  # digits base32 refs never hold
_SERIAL = count(1)


class Tripwire:
    def __getattr__(self, name):
        raise AssertionError(name)


def _never(coroutine):
    raise AssertionError("a provider call ran")


def _created(capability):
    n = next(_SERIAL)
    return {
        C.INVITE_CREATE: f"https://t.me/+AbCdEf{n:04d}",
        C.GROUP_INVITE_RESET: f"https://chat.whatsapp.com/Inv{n:04d}",
        C.TOPIC_CREATE: str(n),
        C.MESSAGE_SEND: str(100 + n),
        C.TEMPLATE_CREATE: str(5_000_000 + n),
    }.get(capability)


class Admin:
    """Real adapter validation; outcomes scripted as "ok", "refused", "unknown" or a code."""

    def __init__(self, validator, outcome="ok", details=None):
        self.validator, self.outcome, self.calls = validator, outcome, []
        self.details = details or {}

    def validate(self, op, target):
        self.validator.validate(op, target)

    def invoke(self, op, target, key):
        self.calls.append((op.capability, dict(op.args)))
        if self.outcome == "unknown":
            return ProviderResult("OUTCOME_UNKNOWN", None)
        if self.outcome == "refused":
            return ProviderResult("FAILED", "NOT_AUTHORIZED")
        if self.outcome != "ok":
            return ProviderResult("FAILED", self.outcome)
        return ProviderResult(
            "SUCCEEDED",
            None,
            provider_ref=_created(op.capability),
            detail=self.details.get(op.capability, {}),
        )


class Provider:
    def __init__(self, state):
        self.state = state

    def snapshot(self, actor, target):
        return CapabilitySnapshot(actor, target.destination_ref, dict.fromkeys(C, self.state), "t")


def group_world(tmp_path):
    w = comms_world(tmp_path)
    conn = w["conn"]
    loc = d.add_location(conn, "L", now=NOW)
    dst = d.add_destination(conn, loc, "telegram", "group:77", "G", normalize=fx.tg, now=NOW)
    rcp = d.add_recipient(conn, now=NOW, display_name="Ali")
    d.add_contact_point(conn, rcp, "telegram", f"user:{TG_USER}", normalize=fx.tg, now=NOW)
    d.add_contact_point(conn, rcp, "whatsapp", WA_PHONE, normalize=fx.wa, now=NOW)
    w.update(
        grp=group_ref(conn, dst, now=NOW),
        rcp=rcp,
        bot=ProviderTarget("telegram", "telegram_bot", dst, "-77"),
        user=ProviderTarget("telegram", "telegram_user", dst, "-77"),
        wa=ProviderTarget("whatsapp", "whatsapp_cloud", "dst_w", "group:120363049891234567"),
    )
    return w


def fixtures(world, *, state=S.AVAILABLE, outcome="ok", details=None, states=None):
    """(capability service, executor, admins) over real adapter validation."""
    admins = {
        "telegram_bot": Admin(BotAdmin(Tripwire()), outcome, details),
        "telegram_user": Admin(
            UserAdmin(Tripwire(), run=_never, clock=lambda: NOW), outcome, details
        ),
        "whatsapp_cloud": Admin(WhatsAppAdmin(Tripwire(), Tripwire()), outcome, details),
    }
    capability = CapabilityService(
        {actor: Provider((states or {}).get(actor, state)) for actor in admins},
        clock=lambda: NOW,
        max_age=timedelta(minutes=5),
    )
    return capability, MutationExecutor(world["writer"], admins), admins


def group_service(world, *, state=S.AVAILABLE, outcome="ok"):
    capability, executor, admins = fixtures(world, state=state, outcome=outcome)
    return GroupService(world["conn"], capability, executor), admins
