"""Display guard (spec §6 INV-DISPLAY, ledger #15). The signature binds bytes; the
human approves glyphs — so bodies with hidden/spoofed content are flagged before the
one-tap path. Persian-aware: U+200C ZWNJ is legitimate inside Arabic-script runs and
is NOT flagged; bidi overrides/isolates are high-risk; other invisibles warn; a
Latin/Cyrillic homoglyph mix is flagged. Display-only; never mutates the bytes."""

_BIDI = {0x202A, 0x202B, 0x202C, 0x202D, 0x202E, 0x2066, 0x2067, 0x2068, 0x2069, 0x200E, 0x200F}
_ZWNJ = 0x200C
_OTHER_INVISIBLE = {0x200B, 0x2060, 0xFEFF, 0x2061, 0x2062, 0x2063}


def _script(ch: str):
    o = ord(ch)
    if 0x0600 <= o <= 0x06FF or 0x0750 <= o <= 0x077F or 0xFB50 <= o <= 0xFDFF:
        return "arabic"
    if 0x0400 <= o <= 0x04FF:
        return "cyrillic"
    if 0x41 <= o <= 0x5A or 0x61 <= o <= 0x7A:
        return "latin"
    return None


def scan(text: str) -> dict:
    reasons = set()
    scripts = set()
    for i, ch in enumerate(text):
        o = ord(ch)
        if o in _BIDI:
            reasons.add("bidi_control")
        elif o in _OTHER_INVISIBLE:
            reasons.add("zero_width_invisible")
        elif o == _ZWNJ:
            left = _script(text[i - 1]) if i > 0 else None
            right = _script(text[i + 1]) if i + 1 < len(text) else None
            if left != "arabic" and right != "arabic":
                reasons.add("zwnj_outside_persian")
        sc = _script(ch)
        if sc:
            scripts.add(sc)
    if "latin" in scripts and "cyrillic" in scripts:
        reasons.add("mixed_latin_cyrillic")
    return {"safe": len(reasons) == 0, "reasons": sorted(reasons)}
