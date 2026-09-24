"""Persian + English search normalisation (spec §4.4), index-only and disposable.

One per-codepoint core drives all outputs so the index, the query, and the snippet
span-mapping stay byte-for-byte consistent. Folds (Yeh/Kaf/hamza/digits) and strips
(combining marks, tatweel, bidi controls) are lossy BY DESIGN — recall over
precision. NEVER used for storage, dedup identity, or display (INV-SEARCH)."""

import unicodedata

NORMALISER_VERSION = 1

_YEH = {"ي": "ی", "ى": "ی"}  # Arabic Yeh, Alef Maksura -> Persian Yeh
_KAF = {"ك": "ک"}  # Arabic Kaf -> Persian Kaf
_HAMZA = {"أ": "ا", "إ": "ا", "آ": "ا", "ٱ": "ا", "ؤ": "و", "ئ": "ی"}
_DIGITS = {}
for _i in range(10):
    _DIGITS[chr(0x0660 + _i)] = str(_i)  # Arabic-Indic
    _DIGITS[chr(0x06F0 + _i)] = str(_i)  # Extended (Persian)
_COMBINING = set(range(0x064B, 0x0660)) | {0x0670}
_TATWEEL = 0x0640
_BIDI = {0x200E, 0x200F, 0x202A, 0x202B, 0x202C, 0x202D, 0x202E, 0x2066, 0x2067, 0x2068, 0x2069}
_ZWNJ = "‌"


def _fold(ch: str) -> str:
    if ch in _YEH:
        return _YEH[ch]
    if ch in _KAF:
        return _KAF[ch]
    if ch in _HAMZA:
        return _HAMZA[ch]
    if ch in _DIGITS:
        return _DIGITS[ch]
    cp = ord(ch)
    if cp in _COMBINING or cp == _TATWEEL or cp in _BIDI:
        return ""
    return ch


def _core(text: str, sep: str, mapped: bool):
    out: list[str] = []
    origin: list[int] = []
    for i, ch0 in enumerate(text):
        for ch in unicodedata.normalize("NFC", ch0):
            if ch == _ZWNJ or ch.isspace():
                piece = sep
            else:
                piece = _fold(ch).casefold()
            for c in piece:
                out.append(c)
                origin.append(i)
    s = "".join(out)
    return (s, origin) if mapped else s


def to_search(text: str) -> str:
    return _core(text, " ", False)


def to_compact(text: str) -> str:
    return _core(text, "", False)


def normalise_query(term: str) -> tuple[str, str]:
    return to_search(term), to_compact(term)


def normalise_mapped(text: str, *, joined: bool) -> tuple[str, list[int]]:
    return _core(text, "" if joined else " ", True)
