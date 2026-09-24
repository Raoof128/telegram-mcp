from telethon.tl import functions, types

from comms.transports.telegram.telegram.telethon_adapter import qualified
from tests.telegram.recorder import PHASE, violations


def test_invoke_wrappers_unwrap_to_the_inner_request():
    inner = functions.messages.GetHistoryRequest(types.InputPeerEmpty(), 0, None, 0, 1, 0, 0, 0)
    wrapped = functions.InvokeWithoutUpdatesRequest(query=inner)
    assert qualified(wrapped) == "messages.GetHistoryRequest"
    assert qualified(functions.PingRequest(ping_id=1)) == "functions.PingRequest"


def test_a_login_request_during_retrieval_is_a_violation():
    log = [
        ("admin.login", "auth.SignInRequest"),
        ("mcp.retrieval", "messages.GetHistoryRequest"),
        ("mcp.retrieval", "updates.GetDifferenceRequest"),
        ("mcp.retrieval", "messages.ReadHistoryRequest"),
        ("mcp.retrieval", "functions.PingRequest"),
    ]
    assert violations(log) == [
        ("mcp.retrieval", "updates.GetDifferenceRequest"),
        ("mcp.retrieval", "messages.ReadHistoryRequest"),
    ]
    assert violations(
        [("admin.login", "updates.GetDifferenceRequest")]
    )  # login no longer pulls updates
    assert PHASE.get() == "unscoped"
