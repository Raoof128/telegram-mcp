"""``credential set|rotate|revoke`` (D39-PRE E8a; spec A13; R-E6).

``set`` and ``rotate`` are the same staged rotation: the candidate is stored as a new version,
proved live (``comms.runtime.proofs``), activated with the old version retired in one audited
transaction, then re-proved; a failed re-proof rolls the pointer back. A failure at any step
leaves the working credential active and destroys the candidate. After a change the daemon
rebuilds its adapters (``reload``), so the next call uses the new credential.

The value arrives only over the peer-credential admin socket, typed at the owner's terminal;
it is never echoed, logged or audited (the audit event carries purpose and versions only).
The Telegram session is not set here: ``comms transport telegram login`` creates it.
"""

from __future__ import annotations

from typing import Any

from comms.core.credentials import CredentialCheckFailed, revoke_credential, rotate_credential
from comms.core.keys.purposes import PURPOSES
from comms.core.keys.slots import KeySlotError
from comms.runtime.operator.context import OperatorContext, OperatorHandler
from comms.runtime.proofs import CredentialProofFailed

__all__ = ["CREDENTIAL_HANDLERS"]


def _purpose(ctx: OperatorContext, args: dict[str, Any]) -> str:
    purpose = args.get("purpose")
    if purpose == "telegram-session":
        raise ValueError("the Telegram session comes from: comms transport telegram login")
    if (
        not isinstance(purpose, str)
        or PURPOSES.get(purpose) is None
        or PURPOSES[purpose].rotation != "staged"
    ):
        raise ValueError("not a provider credential")
    if ctx.secrets is None or ctx.proofs is None or purpose not in ctx.proofs:
        raise ValueError("credentials are managed by the daemon")
    return purpose


def _reloaded(ctx: OperatorContext) -> bool:
    return bool(ctx.reload()["reloaded"]) if ctx.reload is not None else False


def _set(ctx: OperatorContext, args: dict[str, Any]) -> dict[str, Any]:
    purpose = _purpose(ctx, args)
    value = args.get("value")
    if not isinstance(value, str) or not value or len(value) > 4096:
        raise ValueError("the credential value is missing or too long")
    proofs = ctx.proofs or {}
    failure: list[str] = []

    def prove(candidate: bytes) -> None:
        try:
            proofs[purpose](candidate)
        except CredentialProofFailed as refused:
            failure.append(str(refused))  # a fixed reason, never the value
            raise

    try:
        version = rotate_credential(
            ctx.writer, ctx.secrets, purpose, value.encode("utf-8"), prove=prove, now=ctx.clock()
        )
    except CredentialCheckFailed:
        reason = failure[0] if failure else "the provider did not accept it"
        raise ValueError(f"the credential was not activated: {reason}") from None
    return {"purpose": purpose, "version": version, "reloaded": _reloaded(ctx)}


def _revoke(ctx: OperatorContext, args: dict[str, Any]) -> dict[str, Any]:
    purpose = _purpose(ctx, args)
    try:
        revoke_credential(ctx.writer, ctx.secrets, purpose, now=ctx.clock())
    except KeySlotError as refused:
        raise ValueError(str(refused)) from None
    return {"purpose": purpose, "revoked": True, "reloaded": _reloaded(ctx)}


CREDENTIAL_HANDLERS: dict[tuple[str, ...], OperatorHandler] = {
    ("credential", "set"): _set,
    ("credential", "rotate"): _set,
    ("credential", "revoke"): _revoke,
}
