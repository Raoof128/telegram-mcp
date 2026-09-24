"""Loopback MCP auth (ledger #19). 127.0.0.1 is not an auth boundary — any local
process as the user could connect — so every request carries a bearer token held in
the keyring and compared in constant time."""

import hmac

TOKEN_KEY_NAME = "whatsvault.mcp.token.v1"


def provision_token(ks) -> str:
    return ks.provision(TOKEN_KEY_NAME, 32).hex()


def require_token(provided, expected) -> bool:
    if not provided or not expected:
        return False
    return hmac.compare_digest(str(provided), str(expected))
