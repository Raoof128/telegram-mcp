"""Location and audience tools (comms v0.3 Task D23; P §31).

Every write replays by its ``req_`` id (a replayed create makes one location). An audience that
would contain itself is refused. ``audience.resolve`` returns counts and endpoint refs — never
a delivery identity.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from comms.mcp.schemas import (
    BOOL,
    array,
    integer,
    nullable,
    obj,
    read,
    ref,
    string,
    write,
)
from comms.mcp.spec import ToolSpec

__all__ = ["DIRECTORY_TOOLS"]

_LOCATION = ref("location")
_AUDIENCE = ref("audience")
_MEMBER = ref("location", "audience", "destination", "recipient")
_NAME = string(1, 200)
_CURSOR = string(1, 18)
_TIME = string(1, 64)
_FAILURES = ("INVALID_ARGUMENT", "NOT_FOUND")


def _done(**extra: Mapping[str, Any]) -> dict[str, Any]:
    fields = {"op_ref": ref("operation"), "replayed": BOOL, **extra}
    return obj(fields, list(fields))


def _page(item: Mapping[str, Any]) -> dict[str, Any]:
    return obj(
        {"items": array(item, high=100), "next_cursor": nullable(_CURSOR)}, ["items", "next_cursor"]
    )


def _write(
    name: str,
    title: str,
    description: str,
    inputs: Mapping[str, Any],
    output: Mapping[str, Any],
    *,
    idempotent: bool = True,
) -> ToolSpec:
    return write(
        f"comms_{name}",
        title,
        description,
        name.replace("_", ".", 1),
        inputs,
        list(inputs),
        output,
        idempotent=idempotent,
        failures=_FAILURES,
    )


DIRECTORY_TOOLS: tuple[ToolSpec, ...] = (
    read(
        "comms_location_list",
        "List locations",
        "Locations, newest first: ref, name and whether enabled.",
        "location.list",
        {"limit": integer(1, 100), "cursor": _CURSOR},
        [],
        _page(
            obj(
                {"location": _LOCATION, "name": _NAME, "enabled": BOOL},
                ["location", "name", "enabled"],
            )
        ),
    ),
    read(
        "comms_location_get",
        "Get a location",
        "A location's name, whether enabled, and its member and destination counts.",
        "location.get",
        {"location": _LOCATION},
        ["location"],
        obj(
            {
                "location": _LOCATION,
                "name": _NAME,
                "enabled": BOOL,
                "members": integer(0),
                "destinations": integer(0),
                "created_at": _TIME,
            },
            ["location", "name", "enabled", "members", "destinations", "created_at"],
        ),
    ),
    _write(
        "location_create",
        "Create a location",
        "Create a location.",
        {"name": _NAME},
        _done(location=_LOCATION),
        idempotent=False,
    ),
    _write(
        "location_update",
        "Rename a location",
        "Rename a location; its ref never changes.",
        {"location": _LOCATION, "name": _NAME},
        _done(),
    ),
    _write(
        "location_enable",
        "Enable a location",
        "Enable a location.",
        {"location": _LOCATION},
        _done(),
    ),
    _write(
        "location_disable",
        "Disable a location",
        "Disable a location; campaigns skip it.",
        {"location": _LOCATION},
        _done(),
    ),
    read(
        "comms_audience_list",
        "List audiences",
        "Audiences, newest first: ref and name.",
        "audience.list",
        {"limit": integer(1, 100), "cursor": _CURSOR},
        [],
        _page(obj({"audience": _AUDIENCE, "name": _NAME}, ["audience", "name"])),
    ),
    read(
        "comms_audience_get",
        "Get an audience",
        "An audience's name and its members counted by kind.",
        "audience.get",
        {"audience": _AUDIENCE},
        ["audience"],
        obj(
            {
                "audience": _AUDIENCE,
                "name": _NAME,
                "members": obj(
                    {k: integer(0) for k in ("location", "audience", "destination", "recipient")},
                    ["location", "audience", "destination", "recipient"],
                ),
                "created_at": _TIME,
            },
            ["audience", "name", "members", "created_at"],
        ),
    ),
    _write(
        "audience_create",
        "Create an audience",
        "Create an audience.",
        {"name": _NAME},
        _done(audience=_AUDIENCE),
        idempotent=False,
    ),
    _write(
        "audience_update",
        "Rename an audience",
        "Rename an audience; its ref never changes.",
        {"audience": _AUDIENCE, "name": _NAME},
        _done(),
    ),
    _write(
        "audience_add",
        "Add to an audience",
        "Add a location, audience, destination or recipient to an audience. An audience that "
        "would contain itself is refused.",
        {"audience": _AUDIENCE, "member": _MEMBER},
        _done(),
    ),
    _write(
        "audience_remove",
        "Remove from an audience",
        "Remove a member from an audience.",
        {"audience": _AUDIENCE, "member": _MEMBER},
        _done(),
    ),
    read(
        "comms_audience_resolve",
        "Resolve an audience",
        "Who the audience reaches today: counts by transport and endpoint refs, never "
        "identities; truncated past 500 endpoints.",
        "audience.resolve",
        {"audience": _AUDIENCE},
        ["audience"],
        obj(
            {
                "audience": _AUDIENCE,
                "count": integer(0),
                "by_transport": {"type": "object", "additionalProperties": integer(0)},
                "endpoints": array(ref("contact_point", "destination"), high=500),
                "truncated": BOOL,
            },
            ["audience", "count", "by_transport", "endpoints", "truncated"],
        ),
    ),
)
