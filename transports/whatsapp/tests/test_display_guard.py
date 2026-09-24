from whatsvault.approval import display_guard as G


def test_plain_body_safe():
    assert G.scan("hello world")["safe"] is True


def test_legit_persian_zwnj_is_safe():
    assert G.scan("می‌روم")["safe"] is True  # U+200C ZWNJ inside Persian must NOT be flagged (#15)


def test_bidi_override_flagged():
    r = G.scan("hello‮world")
    assert r["safe"] is False and "bidi_control" in r["reasons"]


def test_latin_cyrillic_homoglyph_mix_flagged():
    r = G.scan("pаypal")  # Cyrillic 'а'
    assert r["safe"] is False and "mixed_latin_cyrillic" in r["reasons"]


def test_zwnj_between_latin_flagged():
    r = G.scan("ab‌cd")
    assert r["safe"] is False and "zwnj_outside_persian" in r["reasons"]


def test_scan_does_not_mutate_and_returns_dict():
    t = "hello‮world"
    out = G.scan(t)
    assert out["reasons"] and t == "hello‮world"
