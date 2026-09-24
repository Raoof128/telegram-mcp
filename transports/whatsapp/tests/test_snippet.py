from whatsvault.search import snippet as S


def test_snippet_maps_kaf_to_original():
    orig = "این كتاب است"  # Arabic Kaf in the ORIGINAL
    r = S.render(orig, ["کتاب"])  # query uses Persian Kaf
    assert r["display_text"] == orig
    assert len(r["spans"]) == 1
    st, en = r["spans"][0]
    assert orig[st:en] == "كتاب"  # span covers the original Arabic-Kaf slice


def test_snippet_length_changing_tatweel():
    orig = "این کتـاب است"  # tatweel U+0640 inside the word
    r = S.render(orig, ["کتاب"])
    assert r["display_text"] == orig
    st, en = r["spans"][0]
    assert orig[st:en] == "کتـاب"  # maps back to ORIGINAL incl. the stripped tatweel


def test_no_match_empty_spans():
    r = S.render("hello world", ["xyz"])
    assert r["spans"] == [] and r["display_text"] == "hello world"


def test_multiple_matches_kept_separate():
    r = S.render("salam salam", ["salam"])
    assert len(r["spans"]) == 2
    assert all(r["display_text"][s:e] == "salam" for s, e in r["spans"])
