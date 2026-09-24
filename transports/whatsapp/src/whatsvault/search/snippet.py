"""Snippets rendered from the ORIGINAL text (spec §4, ledger #34, INV-SEARCH).

Never uses FTS5 snippet()/highlight() (those return marked-up copies of the
NORMALISED column). Instead we normalise the original in mapping mode, locate the
normalised query terms, and project each match span back to ORIGINAL character
offsets via the per-codepoint origin map — so display_text is byte-identical to
text_original and internal stripped chars (tatweel/combining/ZWNJ) fall inside
the span."""

from . import normalise as N


def _merge(spans: list[tuple[int, int]]) -> list[tuple[int, int]]:
    out: list[tuple[int, int]] = []
    for s, e in sorted(spans):
        if out and s <= out[-1][1]:
            out[-1] = (out[-1][0], max(out[-1][1], e))
        else:
            out.append((s, e))
    return out


def render(text_original: str, query_terms: list[str], *, window: int = 40) -> dict:
    norm, origin = N.normalise_mapped(text_original, joined=False)
    spans: list[tuple[int, int]] = []
    for term in query_terms:
        t = N.to_search(term).strip()
        if not t:
            continue
        start = 0
        while True:
            j = norm.find(t, start)
            if j == -1:
                break
            end = j + len(t)
            spans.append((origin[j], origin[end - 1] + 1))
            start = j + 1
    return {"display_text": text_original, "spans": _merge(spans)}
