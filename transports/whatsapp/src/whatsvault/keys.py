"""Recipient-key retirement safety (ledger #39). Retirement is time/state-based, not
a scan of Cloudflare Queue contents: retire refuses while the local DLQ still
references the key or the edge has not been confirmed drained beyond retention."""

from .ingest import dlq


class KeyStillReferenced(Exception):
    pass


def retire(vault_conn, recipient_key_id, *, edge_clear: bool) -> None:
    if not edge_clear:
        raise KeyStillReferenced(f"edge not confirmed drained for key {recipient_key_id}")
    if dlq.references_key(vault_conn, recipient_key_id) > 0:
        raise KeyStillReferenced(f"local DLQ still references key {recipient_key_id}")
    # Safety gate only; retirement bookkeeping (key state table) lands with live rotation.
