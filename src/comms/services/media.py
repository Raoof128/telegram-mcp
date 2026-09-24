"""Media services (comms v0.3 Task D17; P §33).

Every media object is named by an opaque ``med_`` ref; the provider id stays internal.
``inspect`` returns type, size and digest; ``delete`` is an audited provider write. Upload and
download move file bytes, which never pass through a tool call's JSON or its request digest;
they wait for a staged-file handle and are not offered yet.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol

from comms.core.errors import CommsError
from comms.core.objects import resolve_object
from comms.core.providers.capability import Capability as C
from comms.core.providers.protocols import ProviderTarget
from comms.services.capability import CapabilityService
from comms.services.mutations import CallContext, MutationExecutor
from comms.services.writes import ProviderWrites, summary

__all__ = ["MediaService", "MediaSource"]

ACTOR = "whatsapp_cloud"


class MediaSource(Protocol):
    def info(self, media_id: str) -> Mapping[str, Any]: ...


class MediaService:
    def __init__(
        self,
        conn: Any,
        capability: CapabilityService,
        executor: MutationExecutor,
        source: MediaSource,
    ) -> None:
        self._conn, self._source = conn, source
        self._writes = ProviderWrites(conn, capability, executor)

    def inspect(self, media: str) -> dict[str, Any]:
        found = resolve_object(self._conn, media, "media")
        try:
            info = self._source.info(found.provider_identity)
        except ValueError:
            raise CommsError("PROVIDER_UNAVAILABLE") from None
        return {
            "media": media,
            "mime": info.get("mime_type"),
            "size": info.get("file_size"),
            "sha256": info.get("sha256"),
        }

    def delete(
        self, ctx: CallContext, account: ProviderTarget, media: str, request_id: str
    ) -> dict[str, Any]:
        _chosen, _target, outcome = self._writes.write(
            ctx,
            "media.delete",
            {ACTOR: account},
            C.MEDIA_DELETE,
            {},
            request_id,
            ACTOR,
            objects={("media", "media_id"): media},
        )
        return {**summary(ACTOR, outcome), "media": media}

    def upload(self, *_args: Any, **_kwargs: Any) -> dict[str, Any]:
        raise CommsError("PROVIDER_UNSUPPORTED")  # needs a staged-file handle (D17 ruling)

    def download(self, *_args: Any, **_kwargs: Any) -> dict[str, Any]:
        raise CommsError("PROVIDER_UNSUPPORTED")  # needs a staged-file handle (D17 ruling)
