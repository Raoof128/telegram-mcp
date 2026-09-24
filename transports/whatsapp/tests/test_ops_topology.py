from whatsvault.ops import topology


def test_real_topology_satisfies_invariants():
    assert all(f["ok"] for f in topology.check_invariants())


def test_two_token_holders_flagged():
    bad = [*topology.PROCESSES, {"name": "rogue", "holds_meta_token": True, "can_approve": False}]
    f = {x["check"]: x["ok"] for x in topology.check_invariants(bad)}
    assert f["single_meta_token_holder"] is False


def test_an_approver_flagged():
    bad = [{"name": "mcp", "holds_meta_token": False, "can_approve": True}]
    f = {x["check"]: x["ok"] for x in topology.check_invariants(bad)}
    assert f["no_process_can_approve"] is False
