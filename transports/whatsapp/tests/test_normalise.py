from whatsvault.search import normalise as N


def test_yeh_and_kaf_unified():
    assert N.to_search("علي") == N.to_search("علی")  # Arabic vs Persian Yeh
    assert N.to_search("كتاب") == N.to_search("کتاب")  # Arabic vs Persian Kaf


def test_zwnj_becomes_space_in_lexical():
    assert N.to_search("می‌روم") == N.to_search("می روم")


def test_separators_removed_in_compact():
    assert N.to_compact("می‌روم") == N.to_compact("میروم") == N.to_compact("می روم")


def test_digits_folded_to_ascii():
    assert N.to_search("۱۲۳") == N.to_search("١٢٣") == N.to_search("123")


def test_hamza_folding_is_lossy_by_design():
    assert N.to_search("آزاد") == N.to_search("ازاد")  # accepted false-positive


def test_latin_case_folded():
    assert N.to_search("SALAM Raouf") == N.to_search("salam raouf")


def test_query_uses_same_pipeline():
    s, c = N.normalise_query("می‌روم")
    assert s == N.to_search("می‌روم") and c == N.to_compact("می‌روم")


def test_mapping_spans_original_including_internal_tatweel():
    orig = "این کتـاب است"  # tatweel U+0640 inside کتاب
    norm, origin = N.normalise_mapped(orig, joined=False)
    term = N.to_search("کتاب")
    j = norm.find(term)
    assert j != -1
    span = (origin[j], origin[j + len(term) - 1] + 1)
    assert orig[span[0] : span[1]] == "کتـاب"  # maps back to ORIGINAL incl. the stripped tatweel


def test_version_present():
    assert N.NORMALISER_VERSION == 1
