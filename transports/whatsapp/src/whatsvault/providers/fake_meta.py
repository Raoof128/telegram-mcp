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
