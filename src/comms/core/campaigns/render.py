"""One campaign's content as the message text every transport sends (comms v0.3 C23).

Campaign content is ``{"canonical", "fa", "en", "links", "media"}`` (``drafts.set_content``).
The message is the canonical body followed, after a blank line, by its links one per line.
Media is not carried as text: content with media renders to ``None`` (the transport skips it
until a media path exists), as does content with no canonical body.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

__all__ = ["rendered_text"]


def rendered_text(content: Mapping[str, Any]) -> str | None:
    body = content.get("canonical")
    if not isinstance(body, str) or not body.strip() or content.get("media"):
        return None
    links = [link for link in content.get("links") or () if isinstance(link, str) and link]
    return body + ("\n\n" + "\n".join(links) if links else "")
