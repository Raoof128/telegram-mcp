"""No sensitive byte crosses the boundary before the anchor (design §2)."""

from tests.coordinator_fixtures import build_coordinator


class RecordingTransport:
    def __init__(self) -> None:
        self.writes: list[bytes] = []

    def write(self, payload: bytes) -> None:
        self.writes.append(payload)


async def test_a_failed_anchor_lets_no_byte_reach_the_transport(tmp_path):
    transport = RecordingTransport()
    coordinator, _conn, adapter = build_coordinator(
        tmp_path, crash_at="refresh_anchor", transport=transport
    )

    outcome = await coordinator.disclose(
        tool_name="telegram_get_messages", arguments={}, adapter=adapter
    )

    assert not outcome.released
    # The anchor failed, so not one byte may have reached the transport --
    # even though the receipt and the ledger rows are durably committed.
    assert transport.writes == []
    assert outcome.data is None


async def test_a_successful_call_writes_only_after_the_anchor(tmp_path):
    transport = RecordingTransport()
    coordinator, conn, adapter = build_coordinator(tmp_path, transport=transport)

    outcome = await coordinator.disclose(
        tool_name="telegram_get_messages", arguments={}, adapter=adapter
    )

    assert outcome.released
    assert len(transport.writes) == 1
    # The anchor exists and names the committed head, so the write happened
    # after it rather than before.
    from telegram_mcp.disclosure.audit.anchor import read_anchor
    from telegram_mcp.keys.store import load_key

    anchor = read_anchor(tmp_path / "anchor" / "anchor.json", load_key("audit-chain-key"))
    head = conn.execute(
        "SELECT chain_seq, event_mac FROM audit_events ORDER BY chain_seq DESC LIMIT 1"
    ).fetchone()
    assert (anchor["chain_seq"], anchor["event_mac"]) == (head[0], head[1])


async def test_a_refusal_before_retrieval_never_calls_the_adapter(tmp_path):
    calls: list[str] = []

    coordinator, _conn, adapter = build_coordinator(tmp_path, approve=False)
    original = adapter.retrieve

    async def counted(*, tool_name: str, arguments: object):
        calls.append(tool_name)
        return await original(tool_name=tool_name, arguments=arguments)

    adapter.retrieve = counted  # type: ignore[method-assign]

    outcome = await coordinator.disclose(
        tool_name="telegram_get_messages", arguments={}, adapter=adapter
    )

    assert outcome.error_code == "CONSENT_DENIED"
    assert calls == [], "the security barrier means no retrieval without consent"
