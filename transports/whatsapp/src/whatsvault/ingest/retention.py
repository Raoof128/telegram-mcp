"""Queue-retention monitor (spec §7). Warns as the oldest unconsumed event approaches
the Cloudflare retention horizon so a sleeping Mac does not silently lose data."""


def assess(oldest_message_ms, now_ms, retention_days: int = 14) -> str:
    if oldest_message_ms is None:
        return "OK"
    frac = (now_ms - oldest_message_ms) / (retention_days * 24 * 3600 * 1000)
    if frac >= 0.90:
        return "CRITICAL"
    if frac >= 0.75:
        return "HIGH"
    if frac >= 0.50:
        return "WARNING"
    return "OK"
