"""comms v0.3 Task C25: WhatsApp media and the Meta-only bounded downloader (A26; O6)."""

import json
import logging

import httpx
import pytest

from comms.core.providers.capability import Capability as C
from comms.core.providers.semantics import SEMANTICS
from comms.transports.net import EgressRefused, host_allowed
from comms.transports.whatsapp.cloud.http import GraphApi
from comms.transports.whatsapp.cloud.media import (
    MAX_MEDIA_BYTES,
    META_MEDIA_HOST_SUFFIXES,
    MediaOps,
    MediaRefused,
)
from tests.transports.whatsapp_cloud.helpers import CANARY, Secrets

PHONE_ID = "106540352242922"
MEDIA_ID = "1037543291543636"
URL = "https://lookaside.fbsbx.com/whatsapp_business/attachments/?mid=1037543291543636&ext=1"
BYTES = b"\x89PNG CANARY-MEDIA-BYTES " + b"x" * 64


def _graph(routes, seen):
    def handler(request):
        seen.append(request)
        answer = routes[(request.method, request.url.path)]
        if callable(answer):
            return answer(request)
        return httpx.Response(answer[0], json=answer[1])

    return httpx.MockTransport(handler)


def _cdn(respond, seen):
    def handler(request):
        seen.append(request)
        return respond(request)

    return httpx.MockTransport(handler)


def _ops(graph_routes=None, cdn=None, graph_seen=None, cdn_seen=None):
    graph_seen = [] if graph_seen is None else graph_seen
    cdn_seen = [] if cdn_seen is None else cdn_seen
    info = (
        200,
        {
            "url": URL,
            "mime_type": "image/png",
            "sha256": "a" * 64,
            "file_size": len(BYTES),
            "id": MEDIA_ID,
        },
    )
    routes = {("GET", f"/v21.0/{MEDIA_ID}"): info, **(graph_routes or {})}
    api = GraphApi(
        Secrets(), version=1, phone_number_id=PHONE_ID, transport=_graph(routes, graph_seen)
    )
    cdn = cdn or (
        lambda request: httpx.Response(200, content=BYTES, headers={"content-type": "image/png"})
    )
    return MediaOps(api, download_transport=_cdn(cdn, cdn_seen))


def test_retrieve_follows_the_graph_url_with_the_bearer_header():
    graph_seen, cdn_seen = [], []
    blob = _ops(graph_seen=graph_seen, cdn_seen=cdn_seen).retrieve(MEDIA_ID)
    assert (blob.data, blob.mime) == (BYTES, "image/png")
    assert (
        str(cdn_seen[0].url) == URL and cdn_seen[0].headers["authorization"] == f"Bearer {CANARY}"
    )


@pytest.mark.parametrize("value", [URL, "https://evil.example/x.png", "../me", "", 5])
def test_caller_supplied_url_refused(value):
    cdn_seen = []
    with pytest.raises(ValueError):
        _ops(cdn_seen=cdn_seen).retrieve(value)
    assert cdn_seen == []


@pytest.mark.parametrize(
    "url",
    [
        "https://evilfbcdn.net/x",
        "https://fbcdn.net.evil.com/x",
        "https://user:pw@lookaside.fbsbx.com/x",
        "https://lookaside.fbsbx.com:8443/x",
        "http://lookaside.fbsbx.com/x",
        "https://xn--fbsbx-l2a.com/x",
        "https://lookaside.fbsbx.com./x/../../",
    ],
)
def test_host_matching_is_exact_suffix_not_substring(url):
    allowed = url == "https://lookaside.fbsbx.com./x/../../"  # a trailing root dot is the same host
    assert host_allowed(url, META_MEDIA_HOST_SUFFIXES) is allowed
    cdn_seen = []
    ops = _ops(
        graph_routes={
            ("GET", f"/v21.0/{MEDIA_ID}"): (
                200,
                {"url": url, "mime_type": "image/png", "id": MEDIA_ID},
            )
        },
        cdn_seen=cdn_seen,
    )
    if not allowed:
        with pytest.raises(MediaRefused):
            ops.retrieve(MEDIA_ID)
        assert cdn_seen == []


def test_the_allowed_suffixes_are_meta_only():
    assert set(META_MEDIA_HOST_SUFFIXES) == {"fbsbx.com", "fbcdn.net", "whatsapp.net"}
    for good in (
        "https://fbcdn.net/a",
        "https://scontent.xx.fbcdn.net/a",
        "https://mmg.whatsapp.net/a",
    ):
        assert host_allowed(good, META_MEDIA_HOST_SUFFIXES)


def test_off_suffix_redirect_refused():
    cdn_seen = []

    def redirect(request):
        return httpx.Response(302, headers={"location": "https://evil.example/steal"})

    with pytest.raises((MediaRefused, EgressRefused)):
        _ops(cdn=redirect, cdn_seen=cdn_seen).retrieve(MEDIA_ID)
    assert [r.url.host for r in cdn_seen] == ["lookaside.fbsbx.com"]


def test_an_on_suffix_redirect_is_followed_once():
    def respond(request):
        if request.url.host == "lookaside.fbsbx.com":
            return httpx.Response(302, headers={"location": "https://mmg.whatsapp.net/d/f/x.enc"})
        return httpx.Response(200, content=BYTES, headers={"content-type": "image/png"})

    cdn_seen = []
    assert _ops(cdn=respond, cdn_seen=cdn_seen).retrieve(MEDIA_ID).data == BYTES
    assert [r.url.host for r in cdn_seen] == ["lookaside.fbsbx.com", "mmg.whatsapp.net"]


def test_size_cap_enforced_streaming():
    chunks = []

    def stream(request):
        def body():
            for _ in range(MAX_MEDIA_BYTES // 65536 + 2):
                chunks.append(1)
                yield b"x" * 65536

        return httpx.Response(200, content=body(), headers={"content-type": "image/png"})

    with pytest.raises(MediaRefused):
        _ops(cdn=stream).retrieve(MEDIA_ID)
    assert len(chunks) <= MAX_MEDIA_BYTES // 65536 + 1  # stopped as the cap was crossed, not after


@pytest.mark.parametrize("mime", ["text/html", "application/x-msdownload", ""])
def test_content_type_allowlist(mime):
    def respond(request):
        return httpx.Response(200, content=b"x", headers={"content-type": mime})

    with pytest.raises(MediaRefused):
        _ops(cdn=respond).retrieve(MEDIA_ID)


def test_upload_and_delete_by_media_id():
    graph_seen = []
    ops = _ops(
        graph_routes={
            ("POST", f"/v21.0/{PHONE_ID}/media"): (200, {"id": MEDIA_ID}),
            ("DELETE", f"/v21.0/{MEDIA_ID}"): (200, {"success": True}),
        },
        graph_seen=graph_seen,
    )
    created = ops.upload(BYTES, "image/png")
    assert (created.outcome, created.provider_ref) == ("SUCCEEDED", MEDIA_ID)
    assert (
        b"CANARY-MEDIA-BYTES" in graph_seen[0].content
        and b'name="messaging_product"' in graph_seen[0].content
    )
    assert ops.delete(MEDIA_ID).outcome == "SUCCEEDED"
    assert SEMANTICS[(C.MEDIA_UPLOAD, "whatsapp_cloud")].retry_class == "CREATE"
    assert SEMANTICS[(C.MEDIA_DELETE, "whatsapp_cloud")].retry_class == "DESTRUCTIVE_NONIDEMPOTENT"
    with pytest.raises(ValueError):
        ops.upload(b"x" * (MAX_MEDIA_BYTES + 1), "image/png")
    with pytest.raises(ValueError):
        ops.upload(BYTES, "text/html")


def test_media_bytes_never_logged(caplog):
    caplog.set_level(logging.DEBUG)
    ops = _ops()
    blob = ops.retrieve(MEDIA_ID)
    texts = [repr(ops), repr(blob), str(blob)] + [r.getMessage() for r in caplog.records]
    assert all("CANARY-MEDIA-BYTES" not in t and CANARY not in t for t in texts)
    assert json.dumps(texts)  # every line is plain text
