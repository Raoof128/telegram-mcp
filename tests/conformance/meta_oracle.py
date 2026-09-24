"""The Meta contract oracle as an httpx transport (comms v0.3 Task C27; design §C.8).

WhatsVault's ``FakeGraph`` (fake_meta.py, ruling R-C27) holds the behaviour; this module only
turns an ``httpx.Request`` into its call and its answer into an ``httpx.Response``.
"""

from __future__ import annotations

import json

import httpx
from whatsvault.providers.fake_meta import FakeGraph

PHONE_ID = "106540352242922"
WABA_ID = "102290129340398"
APP_SECRET = b"fixture-app-secret-not-a-real-one"


def oracle(*, groups: str = "available") -> FakeGraph:
    return FakeGraph(
        phone_number_id=PHONE_ID, waba_id=WABA_ID, app_secret=APP_SECRET, groups=groups
    )


def oracle_transport(graph: FakeGraph) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        query = dict(request.url.params)
        status, body, content_type = graph.handle(
            request.method,
            request.url.host,
            request.url.path,
            query,
            request.content,
            request.headers.get("content-type", ""),
        )
        if isinstance(body, bytes):
            return httpx.Response(status, content=body, headers={"content-type": content_type})
        return httpx.Response(
            status, content=json.dumps(body).encode(), headers={"content-type": content_type}
        )

    return httpx.MockTransport(handler)
