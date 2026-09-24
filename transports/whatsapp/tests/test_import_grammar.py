from whatsvault.importers import grammar as G

DMY = "13/04/2026, 5:32 pm - Mona: hi there\n14/04/2026, 6:01 pm - You: hello\n"
AMBIG = "03/04/2026, 5:32 pm - Mona: hi\n05/04/2026, 6:01 pm - You: yo\n"  # all days <=12 -> both DMY/MDY


def test_dmy_validates_and_mdy_rejects_day_over_12():
    assert G.validate_family(DMY, "DMY", "UTC")["ok"] is True
    assert G.validate_family(DMY, "MDY", "UTC")["ok"] is False  # 13 is not a month


def test_suggest_returns_single_when_unambiguous():
    assert G.suggest_families(DMY) == ["DMY"]


def test_suggest_returns_multiple_when_ambiguous():
    assert set(G.suggest_families(AMBIG)) == {"DMY", "MDY"}


def test_header_match_extracts_sender():
    pat = G.build_header_regex("DMY")
    m = pat.match("13/04/2026, 5:32 pm - Mona: hi there")
    assert m and m.group("sender") == "Mona"
    assert m.group("date") == "13/04/2026"


def test_first_bad_line_reported():
    r = G.validate_family(DMY, "MDY", "UTC")
    assert r["first_bad_line"] == 1
    assert r["header_count"] == 2


def test_bracket_form_and_24h_time():
    pat = G.build_header_regex("DMY")
    m = pat.match("[13/04/2026, 17:32:01] Mona: hi")
    assert m and m.group("sender") == "Mona"
