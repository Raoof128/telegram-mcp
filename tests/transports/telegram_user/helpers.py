"""A fake MTProto session for the user transport: Telegram's dedupe by random_id, a peer cache,
readiness. No Telethon import, as the transport has none."""

from comms.transports.telegram.telegram.errors import GatewayError
from comms.transports.telegram.telegram.send_attempt import SendAttempt


class FakeSession:
    def __init__(self, script=(), *, readiness=None, cached=True):
        self.script = list(script)
        self.visible = {}
        self.sent = []  # (peer, text, random_id)
        self._readiness = readiness
        self.cached = cached
        self.update_owner = None

    def readiness(self):
        return self._readiness

    def input_peer(self, peer_type, peer_id):
        if not self.cached:
            raise GatewayError("NOT_ACCESSIBLE")
        return (peer_type, peer_id)

    async def send_text_once(self, peer, text, random_id, *, timeout, reply_to=None):
        self.sent.append((peer, text, random_id))
        step = self.script.pop(0) if self.script else "ok"
        if step == "flood":
            return SendAttempt("flood", retry_after=12)
        if step == "drop":
            return SendAttempt("ambiguous")
        if random_id in self.visible:
            return SendAttempt("duplicate")
        self.visible[random_id] = 100 + len(self.visible)
        return SendAttempt("sent", message_id=self.visible[random_id])

    def claim_updates(self, owner):
        assert self.update_owner in (None, owner)
        self.update_owner = owner

    def release_updates(self, owner):
        if self.update_owner == owner:
            self.update_owner = None

    async def self_rights(self, peer_type, peer_id, *, timeout):
        raise GatewayError("NOT_ACCESSIBLE")
