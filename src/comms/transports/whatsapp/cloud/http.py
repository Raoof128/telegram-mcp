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
_OBJECT_ID = re.compile(r"\A[0-9]{1,20}\Z")  # a Graph object id (a template)
_GROUP_ID = re.compile(r"\A[0-9]{5,30}\Z")


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
        waba_id: str | None = None,
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
        if waba_id is not None and not _NUMBER_ID.match(waba_id):
            raise GraphRefused("the business account id is malformed")
        self._token, self._phone, self._waba = token, phone_number_id, waba_id
        self._client = pinned_client(GRAPH_ORIGIN, timeout=timeout, transport=transport)

    def __repr__(self) -> str:
        return "GraphApi(<redacted>)"

    def send_message(self, body: Mapping[str, Any]) -> GraphResponse:
        """``POST /{phone-number-id}/messages``: one send, never retried."""
        return self._post(f"/{API_VERSION}/{self._phone}/messages", body)

    def mark_read(self, message_id: str) -> GraphResponse:
        """``POST /{phone-number-id}/messages`` with ``status: read``: one call, never retried."""
        body = {"messaging_product": "whatsapp", "status": "read", "message_id": message_id}
        return self._post(f"/{API_VERSION}/{self._phone}/messages", body)

    def list_templates(
        self, *, limit: int, after: str | None = None, name: str | None = None
    ) -> GraphResponse:
        params = {
            "limit": str(limit),
            **({"after": after} if after else {}),
            **({"name": name} if name else {}),
        }
        return self._call(
            "GET", f"/{API_VERSION}/{self._business()}/message_templates", params=params
        )

    def create_template(self, body: Mapping[str, Any]) -> GraphResponse:
        return self._post(f"/{API_VERSION}/{self._business()}/message_templates", body)

    def edit_template(self, template_id: str, body: Mapping[str, Any]) -> GraphResponse:
        return self._post(f"/{API_VERSION}/{self._object(template_id)}", body)

    def delete_template(self, name: str) -> GraphResponse:
        path = f"/{API_VERSION}/{self._business()}/message_templates"
        return self._call("DELETE", path, params={"name": name})

    def media_info(self, media_id: str) -> GraphResponse:
        return self._call("GET", f"/{API_VERSION}/{self._object(media_id)}")

    def delete_media(self, media_id: str) -> GraphResponse:
        return self._call("DELETE", f"/{API_VERSION}/{self._object(media_id)}")

    def upload_media(self, data: bytes, mime: str) -> GraphResponse:
        return self._call(
            "POST",
            f"/{API_VERSION}/{self._phone}/media",
            form={"messaging_product": "whatsapp", "type": mime},
            files={"file": ("media", data, mime)},
        )

    def phone_info(self) -> GraphResponse:
        params = {"fields": "verified_name,quality_rating,status"}
        return self._call("GET", f"/{API_VERSION}/{self._phone}", params=params)

    def list_groups(self, *, limit: int) -> GraphResponse:
        return self._call(
            "GET", f"/{API_VERSION}/{self._phone}/groups", params={"limit": str(limit)}
        )

    def remove_group_participant(self, group_id: str, wa_id: str) -> GraphResponse:
        body = {"messaging_product": "whatsapp", "participants": [{"user": wa_id}]}
        return self._call(
            "DELETE", f"/{API_VERSION}/{self._group(group_id)}/participants", body=body
        )

    def reset_group_invite(self, group_id: str) -> GraphResponse:
        return self._post(
            f"/{API_VERSION}/{self._group(group_id)}/invite_link", {"messaging_product": "whatsapp"}
        )

    def update_group(self, group_id: str, body: Mapping[str, Any]) -> GraphResponse:
        return self._post(
            f"/{API_VERSION}/{self._group(group_id)}", {"messaging_product": "whatsapp", **body}
        )

    @staticmethod
    def _group(group_id: str) -> str:
        if not isinstance(group_id, str) or not _GROUP_ID.match(group_id):
            raise ValueError("group id refused")
        return group_id

    def bearer(self) -> dict[str, str]:
        """The authorization header, for the media downloader in this package only."""
        return {"Authorization": f"Bearer {self._token}"}

    @staticmethod
    def _object(object_id: str) -> str:
        if not isinstance(object_id, str) or not _OBJECT_ID.match(object_id):
            raise ValueError("graph object id refused")
        return object_id

    def _business(self) -> str:
        if self._waba is None:
            raise GraphRefused("no business account id is configured")
        return self._waba

    def _post(self, path: str, body: Mapping[str, Any]) -> GraphResponse:
        return self._call("POST", path, body=body)

    def _call(
        self,
        method: str,
        path: str,
        *,
        body: Mapping[str, Any] | None = None,
        params: Mapping[str, str] | None = None,
        form: Mapping[str, str] | None = None,
        files: Mapping[str, Any] | None = None,
    ) -> GraphResponse:
        stage: Literal["not_sent", "ambiguous"] | None = None
        headers = {"Authorization": f"Bearer {self._token}"}
        try:
            response = self._client.request(
                method,
                f"{GRAPH_ORIGIN}{path}",
                json=dict(body) if body is not None else None,
                params=dict(params or {}),
                data=dict(form) if form is not None else None,
                files=dict(files) if files is not None else None,
                headers=headers,
            )
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
