"""Meta Graph test doubles: a canary-token secret store and fixture-driven transports."""

import json
from pathlib import Path

import httpx

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "providers" / "meta"
CANARY = "EAAGcanaryTOKENcanaryTOKENcanaryTOKEN0123456789"


class Secrets:
    def __init__(self, token=CANARY):
        self.token = token.encode()

    def get(self, item, version):
        assert (item, version) == ("meta-access-token", 1)
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
