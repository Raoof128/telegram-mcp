from whatsvault.importers import fingerprint as F


def test_content_fp_preserves_yeh_distinction():
    # Evidence identity uses ORIGINAL content, never search-normalised text (§3.9):
    # Arabic Yeh vs Persian Yeh must NOT collide.
    a = F.content_fingerprint("text", "علي")  # Arabic Yeh
    b = F.content_fingerprint("text", "علی")  # Persian Yeh
    assert a != b


def test_occurrence_index_distinguishes_identical():
    cf = F.content_fingerprint("text", "ok")
    a = F.import_fingerprint(1, "cnv", 100, "Mona", "text", cf, 0)
    b = F.import_fingerprint(1, "cnv", 100, "Mona", "text", cf, 1)
    assert a != b


def test_same_inputs_same_fp():
    cf = F.content_fingerprint("text", "ok")
    a = F.import_fingerprint(1, "cnv", 100, "Mona", "text", cf, 0)
    b = F.import_fingerprint(1, "cnv", 100, "Mona", "text", cf, 0)
    assert a == b


def test_sender_key_matters():
    cf = F.content_fingerprint("text", "ok")
    a = F.import_fingerprint(1, "cnv", 100, "Mona", "text", cf, 0)
    b = F.import_fingerprint(1, "cnv", 100, "You", "text", cf, 0)
    assert a != b
