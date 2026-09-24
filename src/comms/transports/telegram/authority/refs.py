"""Opaque-ref validation against the ten spec prefixes (spec §11.1).

Minting lives in :mod:`telegram_mcp.opaque` and nowhere else; this module
adds the closed prefix table on top of the neutral shape check, so a ref
whose shape is right but whose prefix is not one of the ten fails here
rather than reaching a lookup. Unknown or unauthorized refs are reported by
the caller as non-enumerating ``REF_NOT_FOUND``/``NOT_ACCESSIBLE``
(spec §11.2); this function only decides shape and prefix.
"""

from __future__ import annotations

from comms.core.opaque import validate_ref_format as _validate_shape

__all__ = ["REF_PREFIXES", "validate_ref_format"]

# Spec §11.1 table, in table order: account, project, peer, message, cursor,
# principal, MCP client, disclosure receipt, local selector. The consent-challenge
# prefix `tgu_` is retired by comms spec v0.2 and tombstoned: it never validates.
REF_PREFIXES: tuple[str, ...] = (
    "tga_",
    "tpr_",
    "tgp_",
    "tgm_",
    "tgc_",
    "prn_",
    "tcl_",
    "tdr_",
    "tgl_",
)


def validate_ref_format(ref: str, *, expect: str | None = None) -> str:
    """Return the ref's prefix, or raise ``ValueError``.

    ``expect`` pins a single prefix for call sites that know which object
    they asked for (``expect="tgc_"`` on a cursor argument, say).
    """
    if expect is not None and expect not in REF_PREFIXES:
        raise ValueError("unknown opaque-ref prefix")
    prefix = _validate_shape(ref)
    if prefix not in REF_PREFIXES:
        raise ValueError("unknown opaque-ref prefix")
    if expect is not None and prefix != expect:
        raise ValueError("unexpected opaque-ref prefix")
    return prefix
