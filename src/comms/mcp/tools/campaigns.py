"""Campaign tools (comms v0.3 Task D22; P §30).

Every write carries its ``req_`` id and replays to the stored result — a send's replay returns
the same generation and freezes nothing new. Reads carry refs, states, counts and digests;
``preview`` never names a recipient or quotes the body.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from comms.mcp.schemas import (
    BOOL,
    READ_FAILURES,
    array,
    enum,
    integer,
    nullable,
    obj,
    read,
    ref,
    string,
    write,
)
from comms.mcp.spec import ToolSpec

__all__ = ["CAMPAIGN_TOOLS"]

LIFECYCLES = ("DRAFT", "READY", "SCHEDULED", "SENDING", "COMPLETE", "CANCELLED")
SUMMARIES = ("IN_PROGRESS", "INDETERMINATE", "SENT", "PARTIAL", "CANCELLED", "FAILED")
JOB_STATES = (
    "PENDING",
    "IN_FLIGHT",
    "ACCEPTED",
    "DELIVERED",
    "FAILED_TRANSIENT",
    "FAILED_PERMANENT",
    "OUTCOME_UNKNOWN",
    "CANCELLED",
    "SKIPPED_PLATFORM_POLICY",
    "SKIPPED_REVALIDATION",
)
_CAMPAIGN = ref("campaign")
_CURSOR = string(1, 18)
_TIME = string(1, 64)
_CONTENT = obj(
    {
        "canonical": string(0, 4096),
        "fa": string(0, 4096),
        "en": string(0, 4096),
        "links": array(string(1, 2048), high=20),
        "media": array(
            obj(
                {
                    "sha256": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
                    "mime": string(1, 128),
                    "name": string(1, 256),
                    "size": integer(0),
                },
                ["sha256", "mime", "name", "size"],
            ),
            high=10,
        ),
    }
)
_TARGETS = obj(
    {
        "audiences": array(ref("audience"), high=500),
        "locations": array(ref("location"), high=500),
        "destinations": array(ref("destination"), high=500),
        "recipients": array(ref("recipient"), high=5000),
    }
)
_FAILURES = ("INVALID_ARGUMENT", "NOT_FOUND")


def _done(**extra: Mapping[str, Any]) -> dict[str, Any]:
    fields = {"op_ref": ref("operation"), "replayed": BOOL, **extra}
    return obj(fields, list(fields))


def _campaign_write(
    tool: str,
    title: str,
    description: str,
    output: Mapping[str, Any],
    inputs: Mapping[str, Any] | None = None,
    required: tuple[str, ...] = (),
    *,
    idempotent: bool = False,
    destructive: bool = False,
    open_world: bool = False,
) -> ToolSpec:
    return write(
        f"comms_campaign_{tool}",
        title,
        description,
        f"campaign.{tool}",
        {"campaign": _CAMPAIGN, **(inputs or {})},
        ["campaign", *required],
        output,
        idempotent=idempotent,
        destructive=destructive,
        open_world=open_world,
        failures=_FAILURES,
    )


_VIEW = obj(
    {
        "campaign": _CAMPAIGN,
        "title": string(0, 200),
        "lifecycle": enum(LIFECYCLES),
        "summary": nullable(enum(SUMMARIES)),
        "generation": nullable(ref("generation")),
        "send_at": nullable(_TIME),
        "created_at": _TIME,
        "updated_at": _TIME,
    },
    [
        "campaign",
        "title",
        "lifecycle",
        "summary",
        "generation",
        "send_at",
        "created_at",
        "updated_at",
    ],
)

CAMPAIGN_TOOLS: tuple[ToolSpec, ...] = (
    write(
        "comms_campaign_create",
        "Create a campaign",
        "Create a draft campaign. Replaying the same request id returns the same campaign.",
        "campaign.create",
        {"title": string(1, 200)},
        ["title"],
        _done(campaign=_CAMPAIGN),
        failures=_FAILURES,
    ),
    read(
        "comms_campaign_get",
        "Get a campaign",
        "A campaign's title, lifecycle, summary and current generation.",
        "campaign.get",
        {"campaign": _CAMPAIGN},
        ["campaign"],
        _VIEW,
    ),
    read(
        "comms_campaign_list",
        "List campaigns",
        "Campaigns, newest first.",
        "campaign.list",
        {"limit": integer(1, 100), "cursor": _CURSOR},
        [],
        obj(
            {
                "items": array(
                    obj(
                        {
                            "campaign": _CAMPAIGN,
                            "title": string(0, 200),
                            "lifecycle": enum(LIFECYCLES),
                            "summary": nullable(enum(SUMMARIES)),
                            "updated_at": _TIME,
                        },
                        ["campaign", "title", "lifecycle", "summary", "updated_at"],
                    ),
                    high=100,
                ),
                "next_cursor": nullable(_CURSOR),
            },
            ["items", "next_cursor"],
        ),
    ),
    _campaign_write(
        "set_content",
        "Set content",
        "Set a draft's text (canonical, Persian, English), links and media descriptors.",
        _done(),
        {"content": _CONTENT},
        ("content",),
        idempotent=True,
    ),
    _campaign_write(
        "set_targets",
        "Set targets",
        "Set a draft's audiences, locations, destinations and recipients, and its transports.",
        _done(),
        {"targets": _TARGETS, "transports": array(enum(("telegram", "whatsapp")), low=1, high=2)},
        ("targets", "transports"),
        idempotent=True,
    ),
    _campaign_write(
        "validate",
        "Validate",
        "Check a draft is complete and every target is known; DRAFT becomes READY.",
        _done(),
        idempotent=True,
    ),
    read(
        "comms_campaign_preview",
        "Preview reach",
        "How many recipients a send would reach, by transport, with the targets' digest. "
        "Never an identity, never the body.",
        "campaign.preview",
        {"campaign": _CAMPAIGN},
        ["campaign"],
        obj(
            {
                "campaign": _CAMPAIGN,
                "lifecycle": enum(LIFECYCLES),
                "recipients": integer(0),
                "by_transport": {"type": "object", "additionalProperties": integer(0)},
                "target_digest": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
            },
            ["campaign", "lifecycle", "recipients", "by_transport", "target_digest"],
        ),
        failures=READ_FAILURES,
    ),
    _campaign_write(
        "schedule",
        "Schedule",
        "Freeze a READY campaign now for delivery at a later time (UTC). The result names the "
        "generation.",
        _done(generation=ref("generation")),
        {"at": {"type": "string", "format": "date-time", "maxLength": 64}},
        ("at",),
        open_world=True,
    ),
    _campaign_write(
        "unschedule",
        "Unschedule",
        "Discard a scheduled generation; the campaign returns to READY.",
        _done(),
        idempotent=True,
    ),
    _campaign_write(
        "send",
        "Send now",
        "Freeze a READY campaign and start delivery. Replaying the request id returns the same "
        "generation and sends nothing twice.",
        _done(generation=ref("generation")),
        open_world=True,
    ),
    _campaign_write(
        "cancel",
        "Cancel",
        "Cancel a campaign; a sending one is cancelled job by job, and the counts say what was "
        "already sent.",
        _done(
            cancelled_before_send=integer(0),
            already_sent=integer(0),
            currently_in_flight=integer(0),
        ),
        destructive=True,
    ),
    _campaign_write(
        "retry_failed",
        "Retry failures",
        "Requeue a campaign's transiently failed jobs, within the retry cap.",
        _done(requeued=integer(0), retry_exhausted=integer(0)),
        open_world=True,
    ),
    write(
        "comms_campaign_resolve_unknown",
        "Resolve an unknown outcome",
        "State whether a job whose outcome is unknown was sent or not; never guessed.",
        "campaign.resolve_unknown",
        {"job": ref("job"), "verdict": enum(("sent", "not_sent"))},
        ["job", "verdict"],
        _done(),
        failures=_FAILURES,
    ),
    read(
        "comms_campaign_status",
        "Status",
        "A campaign's lifecycle, summary and its current generation's jobs counted by state.",
        "campaign.status",
        {"campaign": _CAMPAIGN},
        ["campaign"],
        obj(
            {
                "campaign": _CAMPAIGN,
                "lifecycle": enum(LIFECYCLES),
                "summary": nullable(enum(SUMMARIES)),
                "generation": nullable(ref("generation")),
                "send_at": nullable(_TIME),
                "jobs": {"type": "object", "additionalProperties": integer(0)},
            },
            ["campaign", "lifecycle", "summary", "generation", "send_at", "jobs"],
        ),
    ),
    read(
        "comms_campaign_delivery_report",
        "Delivery report",
        "The current generation's jobs — ref, transport, state and attempts — page by page.",
        "campaign.delivery_report",
        {"campaign": _CAMPAIGN, "limit": integer(1, 100), "cursor": _CURSOR},
        ["campaign"],
        obj(
            {
                "campaign": _CAMPAIGN,
                "items": array(
                    obj(
                        {
                            "job": ref("job"),
                            "transport": enum(("telegram", "whatsapp")),
                            "state": enum(JOB_STATES),
                            "attempts": integer(0),
                        },
                        ["job", "transport", "state", "attempts"],
                    ),
                    high=100,
                ),
                "next_cursor": nullable(_CURSOR),
            },
            ["campaign", "items", "next_cursor"],
        ),
    ),
)
