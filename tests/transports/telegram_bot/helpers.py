"""Shared Bot API test doubles: a canary-token secret store and fixture-driven transports."""

import json
from pathlib import Path

import httpx

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "providers" / "telegram_bot"
CANARY = "7000000001:AAcanaryTOKENcanaryTOKENcanary12345"


class Secrets:
    def __init__(self, token=CANARY):
        self.token = token.encode()

    def get(self, item, version):
        assert (item, version) == ("telegram-bot-token", 1)
        return self.token


def fixture_transport(name, seen=None):
    recorded = json.loads((FIXTURES / f"{name}.json").read_text())

    def handler(request):
        if seen is not None:
            seen.append(request)
        if "raw" in recorded:
            return httpx.Response(recorded["http_status"], content=recorded["raw"].encode())
        return httpx.Response(recorded["http_status"], json=recorded["body"])

    return httpx.MockTransport(handler)


def raising(exc_type):
    def handler(request):
        raise exc_type("boom", request=request)

    return httpx.MockTransport(handler)


def routed(routes, seen=None):
    """A transport answering each Bot API method from its own fixture (or exception type)."""

    def handler(request):
        if seen is not None:
            seen.append(request)
        method = request.url.path.rsplit("/", 1)[1]
        answer = routes[method]
        if isinstance(answer, type):
            raise answer("boom", request=request)
        recorded = json.loads((FIXTURES / f"{answer}.json").read_text())
        return httpx.Response(recorded["http_status"], json=recorded["body"])

    return httpx.MockTransport(handler)
