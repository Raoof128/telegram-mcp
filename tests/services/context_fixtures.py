"""Shared context-engine test doubles (D8, D10, D11): a paging source and a clock."""

from comms.core.providers.protocols import ContextPage, ContextRefused


class Source:
    """A context source double: pages of message items, every call counted."""

    def __init__(self, provenance="telegram_live", per_page=3, pages=100, refuse=None):
        self.provenance, self.per_page, self.pages, self.refuse = (
            provenance,
            per_page,
            pages,
            refuse,
        )
        self.queries = []

    def read(self, query):
        self.queries.append(query)
        if self.refuse:
            raise ContextRefused(self.refuse)
        start = int(query.args.get("cursor") or 1000)
        items = tuple(
            {
                "source": self.provenance,
                "observed_at": "2026-09-25T00:00:00.000000Z",
                "message_id": start - i,
                "sent_at": "2026-09-24T12:00:00Z",
                "sender_id": "42",
                "chat_id": query.target.identity,
                "untrusted": {"text": f"hello {start - i}", "sender_name": "Ali"},
            }
            for i in range(self.per_page)
        )
        more = len(self.queries) < self.pages
        return ContextPage(items, self.provenance, str(start - self.per_page) if more else None)


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t
