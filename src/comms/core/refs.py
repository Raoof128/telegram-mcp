"""The campaign core's opaque-ref prefixes (comms 5b-4 design §1, H6).

Disjoint from every transport's prefixes; a test pins that. Refs are minted and
shape-checked by the single minter in ``comms.core.opaque``.
"""

from __future__ import annotations

from comms.core.opaque import mint_opaque_ref, validate_ref_format

__all__ = ["CORE_PREFIXES", "check", "kind_of", "mint"]

CORE_PREFIXES: dict[str, str] = {
    "location": "loc_",
    "destination": "dst_",
    "recipient": "rcp_",
    "contact_point": "rct_",
    "audience": "cau_",
    "campaign": "cmp_",
    "generation": "gen_",
    "job": "djb_",
    "attempt": "dat_",
    "event": "cev_",
    # comms v0.3 (design D.4, A31; no usr_)
    "group": "grp_",
    "message": "cmg_",
    "invite": "inv_",
    "template": "ctp_",
    "topic": "top_",
    "media": "med_",
    "context": "ctx_",
    "cursor": "cur_",
    "operation": "op_",
    "request": "req_",
    "cutover": "cut_",
    "client": "cli_",
    "audit_event": "aev_",
    "audit_checkpoint": "ack_",
}
_KIND_BY_PREFIX = {prefix: kind for kind, prefix in CORE_PREFIXES.items()}


def mint(kind: str) -> str:
    """Mint a fresh ref of ``kind``."""
    if kind not in CORE_PREFIXES:
        raise ValueError("unexpected ref")
    return mint_opaque_ref(CORE_PREFIXES[kind])


def kind_of(ref: object) -> str:
    """The kind whose prefix ``ref`` carries; ``ValueError`` for anything else."""
    if not isinstance(ref, str):
        raise ValueError("unexpected ref")  # noqa: TRY004 -- uniform ValueError on validation
    try:
        prefix = validate_ref_format(ref)
    except ValueError:
        raise ValueError("unexpected ref") from None
    if prefix not in _KIND_BY_PREFIX:
        raise ValueError("unexpected ref")
    return _KIND_BY_PREFIX[prefix]


def check(ref: object, kind: str) -> str:
    """Return ``ref`` if it is a well-formed ref of ``kind``; ``ValueError`` otherwise."""
    if kind_of(ref) != kind:
        raise ValueError("unexpected ref")
    assert isinstance(ref, str)
    return ref
