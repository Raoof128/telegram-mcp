from whatsvault.importers import parse as P


def test_multiline_body_assembled():
    text = "13/04/2026, 5:32 pm - Mona: line one\ncontinued line two\n14/04/2026, 6:01 pm - You: next\n"
    recs = P.parse_transcript(text, "DMY", "UTC")
    msgs = [r for r in recs if r["kind"] == "message"]
    assert len(msgs) == 2
    assert msgs[0]["body"] == "line one\ncontinued line two"
    assert msgs[0]["sender"] == "Mona"
    assert (msgs[0]["hour"], msgs[0]["minute"]) == (17, 32)  # 5:32 pm -> 17:32


def test_media_placeholder():
    recs = P.parse_transcript("13/04/2026, 5:32 pm - Mona: <Media omitted>\n", "DMY", "UTC")
    assert recs[0]["kind"] == "message" and recs[0]["media_state"] == "MEDIA_PLACEHOLDER"


def test_system_line_classified():
    text = "13/04/2026, 5:32 pm - Messages and calls are end-to-end encrypted.\n"
    recs = P.parse_transcript(text, "DMY", "UTC")
    assert recs[0]["kind"] == "system"


def test_message_containing_system_word_stays_message():
    # "added" appears in the body but there IS a sender -> must remain a message.
    recs = P.parse_transcript("13/04/2026, 5:32 pm - Mona: I added sugar\n", "DMY", "UTC")
    assert recs[0]["kind"] == "message" and recs[0]["sender"] == "Mona"


def test_headerlike_but_invalid_date_is_boundary_not_message():
    text = "13/04/2026, 5:32 pm - Mona: real one\n99/99/2026, 0:00 am - Ghost: not a real header\n"
    recs = P.parse_transcript(text, "DMY", "UTC")
    assert "ambiguous_boundary" in [r["kind"] for r in recs]
    senders = [r.get("sender") for r in recs if r["kind"] == "message"]
    assert "Ghost" not in senders


def test_dst_fold_flagged():
    # First Sunday of April 2026 = Apr 5; Australia/Sydney DST ends 03:00->02:00, so 02:30 is a fold.
    recs = P.parse_transcript("05/04/2026, 2:30 am - Mona: hi\n", "DMY", "Australia/Sydney")
    assert recs[0]["dst_class"] == "fold"


def test_plain_message_is_unambiguous():
    recs = P.parse_transcript("14/04/2026, 6:01 pm - You: hello\n", "DMY", "UTC")
    assert recs[0]["dst_class"] == "unambiguous"
    assert recs[0]["source_ordinal"] == 0
