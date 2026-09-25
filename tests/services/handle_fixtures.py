"""Shared D9 fixtures: a comms world with a provisioned cursor key and a handle service."""

import os
from datetime import timedelta

from comms.core.keys import rotate as rot
from comms.services.handles import ContextHandles
from tests.core.audit.legacy_fixtures import comms_world
from tests.core.campaign_helpers import NOW

CLIENT, OTHER = "cli_" + "a" * 26, "cli_" + "b" * 26
TARGET = "grp_" + "g" * 26


class Clock:
    def __init__(self):
        self.now = NOW

    def __call__(self):
        return self.now

    def advance(self, **kw):
        self.now += timedelta(**kw)


def world(tmp_path):
    env = comms_world(tmp_path)
    rotate_cursor_key(env)
    env["clock"] = Clock()
    env["handles"] = ContextHandles(env["conn"], env["store"], clock=env["clock"])
    return env


def rotate_cursor_key(env):
    return rot.rotate(
        env["writer"],
        env["store"],
        "cursor-key",
        material=os.urandom(32),
        prove=lambda m: None,
        now=NOW,
    )


def open_handle(env, client=CLIENT):
    return env["handles"].open(
        client=client,
        owner="owner",
        target_ref=TARGET,
        actor="telegram_user",
        query_digest="q" * 64,
        snapshot={"kind": "recent"},
    )
