import pytest

from telegram_mcp.contract import load_contracts
from telegram_mcp.validation import ArgumentError, validate_arguments


def test_messages_default_is_thirty_without_mutating_caller():
    args = {"project_ref": "tpr_" + "a" * 26, "peer_ref": "tgp_" + "b" * 26}
    validated = validate_arguments(load_contracts()["telegram_get_messages"], args)
    assert validated["limit"] == 30
    assert "limit" not in args


@pytest.mark.parametrize("args", [None, [], {"limit": 1}, {"project_ref": "all"}])
def test_messages_never_infers_project(args):
    with pytest.raises(ArgumentError) as exc:
        validate_arguments(load_contracts()["telegram_get_messages"], args)
    assert exc.value.code == "INVALID_ARGUMENT"


TPR = "tpr_" + "a" * 26
TGP = "tgp_" + "b" * 26


def _msgs(**over):
    args = {"project_ref": TPR, "peer_ref": TGP}
    args.update(over)
    return validate_arguments(load_contracts()["telegram_get_messages"], args)


@pytest.mark.parametrize("limit", [1, 100])
def test_messages_limit_edges_accepted(limit):
    assert _msgs(limit=limit)["limit"] == limit


@pytest.mark.parametrize("limit", [0, 101, True, False, "30", 1.5])
def test_messages_limit_edges_rejected(limit):
    with pytest.raises(ArgumentError) as exc:
        _msgs(limit=limit)
    assert exc.value.code == "INVALID_ARGUMENT"


def test_extra_keys_rejected():
    with pytest.raises(ArgumentError):
        _msgs(synthetic_unknown=1)


def test_absent_cursor_stays_absent_null_preserved():
    assert "cursor" not in _msgs()
    assert _msgs(cursor=None)["cursor"] is None


@pytest.mark.parametrize(
    "ref", ["TPR_" + "A" * 26, "tpr_short", "tgp_" + "b" * 26, "tpr_" + "a" * 25, "tpr_" + "8" * 26]
)
def test_malformed_refs_rejected(ref):
    with pytest.raises(ArgumentError):
        _msgs(project_ref=ref)


def _xsearch(n, **over):
    refs = ["tpr_" + chr(ord("a") + i) * 26 for i in range(n)]
    args = {"project_refs": refs, "query": "synthetic"}
    args.update(over)
    return validate_arguments(load_contracts()["telegram_cross_project_search"], args)


@pytest.mark.parametrize("n", [2, 8])
def test_cross_project_counts_accepted(n):
    assert len(_xsearch(n)["project_refs"]) == n


@pytest.mark.parametrize("n", [1, 9])
def test_cross_project_counts_rejected(n):
    with pytest.raises(ArgumentError):
        _xsearch(n)


def test_cross_project_duplicates_rejected():
    with pytest.raises(ArgumentError):
        validate_arguments(
            load_contracts()["telegram_cross_project_search"],
            {"project_refs": [TPR, TPR], "query": "synthetic"},
        )


def test_status_empty_accepted_nonobject_rejected():
    assert validate_arguments(load_contracts()["telegram_status"], {}) == {}
    for bad in (None, [], "x"):
        with pytest.raises(ArgumentError):
            validate_arguments(load_contracts()["telegram_status"], bad)


def test_naive_offset_rejected():
    c = load_contracts()["telegram_search_messages"]
    with pytest.raises(ArgumentError) as exc:
        validate_arguments(c, {"project_ref": TPR, "query": "q", "since": "2026-09-22T00:00:00"})
    assert exc.value.code == "INVALID_TIME"


def test_reversed_and_equal_ranges_rejected():
    c = load_contracts()["telegram_search_messages"]
    base = {"project_ref": TPR, "query": "q"}
    with pytest.raises(ArgumentError) as exc:
        validate_arguments(
            c, {**base, "since": "2026-09-22T01:00:00Z", "until": "2026-09-22T00:00:00Z"}
        )
    assert exc.value.code == "INVALID_TIME"
    with pytest.raises(ArgumentError) as exc:
        validate_arguments(
            c, {**base, "since": "2026-09-22T00:00:00Z", "until": "2026-09-22T00:00:00Z"}
        )
    assert exc.value.code == "INVALID_TIME"


def test_offset_equivalent_instants():
    c = load_contracts()["telegram_search_messages"]
    ok = validate_arguments(
        c,
        {
            "project_ref": TPR,
            "query": "q",
            "since": "2026-09-22T00:00:00Z",
            "until": "2026-09-22T02:00:00+01:00",
        },
    )
    assert ok["since"] == "2026-09-22T00:00:00Z"  # original strings preserved
    with pytest.raises(ArgumentError):
        validate_arguments(
            c,
            {
                "project_ref": TPR,
                "query": "q",
                "since": "2026-09-22T01:00:00+01:00",
                "until": "2026-09-22T00:00:00Z",
            },
        )


def test_leap_second_is_invalid_time():
    c = load_contracts()["telegram_search_messages"]
    with pytest.raises(ArgumentError) as exc:
        validate_arguments(c, {"project_ref": TPR, "query": "q", "since": "2016-12-31T23:59:60Z"})
    assert exc.value.code == "INVALID_TIME"


def test_unicode_preserved_byte_for_byte():
    c = load_contracts()["telegram_search_messages"]
    query = "می\u200cخواهم ☕️ e\u0301"
    out = validate_arguments(c, {"project_ref": TPR, "query": query})
    assert out["query"] == query
    assert out["query"].encode("utf-8") == query.encode("utf-8")


def test_list_chats_defaults():
    out = validate_arguments(load_contracts()["telegram_list_chats"], {"project_ref": TPR})
    assert out["limit"] == 20
    assert out["chat_type"] == "any"
    assert out["archived"] == "exclude"


def test_public_error_carries_code_only():
    try:
        _msgs(limit=0)
    except ArgumentError as exc:
        assert str(exc) == "INVALID_ARGUMENT"
        assert exc.code == "INVALID_ARGUMENT"
    else:
        raise AssertionError("expected ArgumentError")
