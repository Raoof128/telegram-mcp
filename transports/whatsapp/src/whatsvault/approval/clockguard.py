"""Clock integrity (spec §6 H2, ledger #12). The SENDER owns clock trust — there is no
clock_ok caller argument to bypass. A backward wall-clock jump makes time untrusted and
send is refused."""


class ClockUntrusted(Exception):
    pass


class ClockGuard:
    def __init__(self, now_fn):
        self._now = now_fn
        self._last = None

    def trusted_now(self) -> int:
        n = self._now()
        if self._last is not None and n < self._last:
            raise ClockUntrusted(f"backward clock jump {self._last} -> {n}")
        self._last = n
        return n
