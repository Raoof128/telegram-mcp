"""FakeMeta simulates the §6.6 outcome matrix without any network."""


class TimeoutAfterSend(Exception):
    pass


class ConnectFailed(Exception):
    pass


class FakeMeta:
    def __init__(self, mode="ok"):
        self.mode = mode
        self.sends = []
        self.mark_reads = []

    def send_text(self, *, phone_number_id, recipient_wa_id, body) -> dict:
        if self.mode == "timeout_after_send":
            self.sends.append((recipient_wa_id, body))  # went out, response lost
            raise TimeoutAfterSend()
        if self.mode == "connect_fail":
            raise ConnectFailed()  # nothing sent
        self.sends.append((recipient_wa_id, body))
        if self.mode == "ok":
            return {"outcome": "SUBMITTED", "wamid": "wamid.NEW"}
        return {"outcome": "FAILED", "error_code": self.mode}

    def health(self) -> dict:
        return {"ok": True}

    def mark_read(self, *, wamid) -> dict:
        self.mark_reads.append(wamid)
        return {"outcome": "OK"}


# --- comms v0.3 C27 (ruling R-C27): the Meta Graph contract oracle -----------------------------
# FakeMeta above is unchanged. FakeGraph answers the Graph API calls the comms adapter makes,
# deterministically and with no network or HTTP library: a caller passes the parsed request
# (method, host, path, query, body) and gets (status, JSON or bytes, content type) back.
# Sends queue "sent" then "delivered" statuses, emitted as an HMAC-signed webhook body.

import hashlib as _hashlib
import hmac as _hmac
import json as _json


class FakeGraph:
    VERSION = "v21.0"
    MEDIA_HOST = "lookaside.fbsbx.com"

    def __init__(self, *, phone_number_id, waba_id, app_secret, groups="available", timestamp=1790000000):
        self.phone_number_id = phone_number_id
        self.waba_id = waba_id
        self.app_secret = app_secret
        self.groups = groups  # "available" | "ineligible" | "unsupported"
        self.timestamp = timestamp
        self.sent = []  # message bodies, in order
        self.unknown_routes = []
        self._pending_statuses = []
        self.read = []
        self._counter = 0
        self.templates = {
            ("nowruz_greeting", "en"): {
                "id": "1000",
                "name": "nowruz_greeting",
                "language": "en",
                "status": "APPROVED",
                "category": "MARKETING",
                "components": [{"type": "BODY", "text": "Happy Nowruz, {{1}}"}],
            }
        }
        self.media = {"2000": (b"\x89PNG fixture", "image/png")}

    def _next(self):
        self._counter += 1
        return self._counter

    @staticmethod
    def _error(status, code, message):
        return status, {"error": {"message": message, "type": "OAuthException", "code": code}}, "application/json"

    def handle(self, method, host, path, query, body, content_type=""):
        if host == self.MEDIA_HOST:
            item = self.media.get(query.get("mid", ""))
            if method != "GET" or item is None:
                return 404, b"", "text/plain"
            return 200, item[0], item[1]
        v, phone, waba = f"/{self.VERSION}", self.phone_number_id, self.waba_id
        parts = path.split("/")
        if method == "POST" and path == f"{v}/{phone}/messages":
            return self._send(_json.loads(body))
        if method == "GET" and path == f"{v}/{phone}":
            return 200, {"verified_name": "Fixture Co", "quality_rating": "GREEN", "status": "CONNECTED", "id": phone}, "application/json"
        if method == "GET" and path == f"{v}/{phone}/groups":
            if self.groups == "ineligible":
                return self._error(403, 10, "(#10) Application does not have permission for this action")
            if self.groups == "unsupported":
                return self._error(400, 2500, "Unknown path components: /groups")
            return 200, {"data": [{"id": "120363049891234567", "subject": "Fixture group"}], "paging": {}}, "application/json"
        if path == f"{v}/{waba}/message_templates":
            return self._templates(method, query, body)
        if method == "POST" and path == f"{v}/{phone}/media":
            media_id = str(3000 + self._next())
            start = body.find(b"\r\n\r\n", body.find(b'name="file"')) + 4
            end = body.find(b"\r\n--", start)
            self.media[media_id] = (body[start:end], "image/png")
            return 200, {"id": media_id}, "application/json"
        if len(parts) == 3 and parts[2].isdigit() and parts[2] in self.media:
            if method == "GET":
                data, mime = self.media[parts[2]]
                url = f"https://{self.MEDIA_HOST}/whatsapp_business/attachments/?mid={parts[2]}"
                return 200, {"url": url, "mime_type": mime, "file_size": len(data), "id": parts[2]}, "application/json"
            if method == "DELETE":
                del self.media[parts[2]]
                return 200, {"success": True}, "application/json"
        if len(parts) == 3 and method == "POST" and any(t["id"] == parts[2] for t in self.templates.values()):
            return 200, {"success": True}, "application/json"
        if len(parts) >= 3 and parts[2].isdigit() and len(parts[2]) >= 15:
            if method == "DELETE" and parts[3:] == ["participants"]:
                return 200, {"success": True}, "application/json"
            if method == "POST" and parts[3:] == ["invite_link"]:
                return 200, {"invite_link": f"https://chat.whatsapp.com/Fake{self._next()}"}, "application/json"
            if method == "POST" and parts[3:] == []:
                return 200, {"success": True}, "application/json"
        self.unknown_routes.append((method, path))
        return self._error(400, 2500, "Unknown path components")

    def _send(self, message):
        if message.get("status") == "read":  # mark-as-read (comms v0.3 D14): no message is sent
            if message.get("messaging_product") != "whatsapp" or not message.get("message_id"):
                return self._error(400, 100, "Invalid parameter")
            self.read.append(message["message_id"])
            return 200, {"success": True}, "application/json"
        if message.get("messaging_product") != "whatsapp" or "to" not in message or "type" not in message:
            return self._error(400, 100, "Invalid parameter")
        wamid = f"wamid.FAKE{self._next():08d}"
        self.sent.append(message)
        for status in ("sent", "delivered"):
            self._pending_statuses.append(
                {"id": wamid, "status": status, "timestamp": str(self.timestamp), "recipient_id": message["to"]}
            )
        return 200, {
            "messaging_product": "whatsapp",
            "contacts": [{"input": message["to"], "wa_id": message["to"]}],
            "messages": [{"id": wamid}],
        }, "application/json"

    def _templates(self, method, query, body):
        if method == "GET":
            name = query.get("name")
            data = [t for t in self.templates.values() if name in (None, t["name"])]
            return 200, {"data": data, "paging": {"cursors": {"before": "A", "after": "B"}}}, "application/json"
        if method == "POST":
            template = _json.loads(body)
            if (template["name"], template["language"]) in self.templates:
                return self._error(400, 100, "Content in this language already exists")
            template_id = str(1000 + self._next())
            self.templates[(template["name"], template["language"])] = {**template, "id": template_id, "status": "PENDING"}
            return 200, {"id": template_id, "status": "PENDING", "category": template["category"]}, "application/json"
        if method == "DELETE":
            doomed = [k for k in self.templates if k[0] == query.get("name")]
            for key in doomed:
                del self.templates[key]
            return 200, {"success": True}, "application/json"
        return self._error(400, 2500, "Unknown path components")

    def webhook_for_statuses(self):
        """The pending statuses as one signed webhook body; drains them."""
        statuses, self._pending_statuses = self._pending_statuses, []
        payload = {
            "object": "whatsapp_business_account",
            "entry": [
                {
                    "id": self.waba_id,
                    "changes": [
                        {
                            "field": "messages",
                            "value": {
                                "messaging_product": "whatsapp",
                                "metadata": {"phone_number_id": self.phone_number_id},
                                "statuses": statuses,
                            },
                        }
                    ],
                }
            ],
        }
        raw = _json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
        signature = "sha256=" + _hmac.new(self.app_secret, raw, _hashlib.sha256).hexdigest()
        return raw, {"X-Hub-Signature-256": signature, "Content-Type": "application/json"}
