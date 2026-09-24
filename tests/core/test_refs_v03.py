"""comms v0.3 Task A1b: every v0.3 prefix is registered once, disjoint from all others (design D.4, A31)."""

from comms.core import refs

V03 = {
    "group": "grp_",
    "message": "cmg_",
    "invite": "inv_",
    "template": "ctp_",
    "topic": "top_",
    "media": "med_",
    "context": "ctx_",
    "cursor": "cur_",
    "operation": "op_",
    "request": "req_",
    "cutover": "cut_",
    "client": "cli_",
}


def test_v03_prefixes_are_registered():
    for kind, prefix in V03.items():
        assert refs.CORE_PREFIXES[kind] == prefix
        assert refs.kind_of(refs.mint(kind)) == kind


def test_v03_prefixes_disjoint_from_telegram_whatsvault_and_5b4():
    from whatsvault.ids import PREFIXES

    from comms.transports.telegram.authority.refs import REF_PREFIXES

    values = list(refs.CORE_PREFIXES.values())
    assert len(values) == len(set(values)) == 25
    others = set(REF_PREFIXES) | {"tgu_"} | {p + "_" for p in PREFIXES}
    assert not set(values) & others


def test_no_usr_prefix():
    assert "usr_" not in refs.CORE_PREFIXES.values() and "user" not in refs.CORE_PREFIXES
