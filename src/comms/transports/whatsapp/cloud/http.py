"""The Meta Graph client (comms v0.3 Task C22; A19, A26).

One pinned origin (``https://graph.facebook.com``), one ``POST`` per call, no retries, no
redirects. The access token is read from the secret store once, checked for shape, and sent
only in the ``Authorization`` header: never in a URL, a log line, a ``repr`` or an error
(transport errors are re-raised as a fixed ``GraphTransportError`` with no chained httpx
exception). The phone-number id is checked before it is put in a path.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Literal

import httpx

from comms.core.keys.secrets import SecretStore
from comms.transports.net import pinned_client

__all__ = ["GRAPH_ORIGIN", "GraphApi", "GraphRefused", "GraphResponse", "GraphTransportError"]

GRAPH_ORIGIN = "https://graph.facebook.com"
API_VERSION = "v21.0"
TOKEN_ITEM = "meta-access-token"
_TOKEN = re.compile(r"\A[A-Za-z0-9_.\-]{20,512}\Z")
_NUMBER_ID = re.compile(r"\A[0-9]{5,20}\Z")


class GraphRefused(Exception):
    """A call the client will not make (malformed token or phone-number id). Fixed messages."""


class GraphTransportError(Exception):
    """No complete HTTP response; ``not_sent`` only when no connection was made."""

    def __init__(self, stage: Literal["not_sent", "ambiguous"]) -> None:
        super().__init__(f"graph api transport error ({stage})")
        self.stage = stage


@dataclass(frozen=True)
class GraphResponse:
    http_status: int
    envelope: Mapping[str, Any] | None = field(repr=False)  # None: the body was not a JSON object


class GraphApi:
    def __init__(
        self,
        secrets: SecretStore,
        *,
        version: int,
        phone_number_id: str,
        timeout: float = 15.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        try:
            token = secrets.get(TOKEN_ITEM, version).decode("ascii")
        except UnicodeDecodeError:
            token = ""
        if not _TOKEN.match(token):
            raise GraphRefused("the access token is malformed")
        if not _NUMBER_ID.match(phone_number_id):
            raise GraphRefused("the phone-number id is malformed")
        self._token, self._phone = token, phone_number_id
        self._client = pinned_client(GRAPH_ORIGIN, timeout=timeout, transport=transport)

    def __repr__(self) -> str:
        return "GraphApi(<redacted>)"

    def send_message(self, body: Mapping[str, Any]) -> GraphResponse:
        """``POST /{phone-number-id}/messages``: one send, never retried."""
        return self._post(f"/{API_VERSION}/{self._phone}/messages", body)

    def _post(self, path: str, body: Mapping[str, Any]) -> GraphResponse:
        stage: Literal["not_sent", "ambiguous"] | None = None
        headers = {"Authorization": f"Bearer {self._token}"}
        try:
            response = self._client.post(f"{GRAPH_ORIGIN}{path}", json=dict(body), headers=headers)
        except (httpx.ConnectError, httpx.ConnectTimeout):
            stage = "not_sent"
        except httpx.HTTPError:
            stage = "ambiguous"
        if stage is not None:
            raise GraphTransportError(stage)  # outside the handler: no chained error
        try:
            envelope = json.loads(response.content)
        except ValueError:
            envelope = None
        return GraphResponse(response.status_code, envelope if isinstance(envelope, dict) else None)

    def close(self) -> None:
        self._client.close()
