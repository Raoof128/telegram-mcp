"""Injection-safe search query AST + dual-tier compiler (spec §4, ledger #32/#33).

A query is a typed AST, never raw MATCH text. Each term is normalised (same
pipeline as the index) then re-quoted as an FTS5 phrase (embedded quotes doubled),
so FTS operators inside user text are literal, never syntax. Two MATCH forms:
lexical (unicode61, spaces preserved) and compact (trigram, separators removed,
>=3 chars). Filters are SQL predicates, never MATCH terms; time filters use
uncertainty-interval OVERLAP (#33), never a lower bound."""

from dataclasses import dataclass, field

from . import normalise as N

# The two FTS5 virtual tables this module may query. Identifiers cannot be bound.
_FTS_TABLES = frozenset({"fts_lexical", "fts_compact"})

MAX_TERMS = 16
MAX_QUERY_BYTES = 512
MAX_NEAR = 10
MAX_LIMIT = 200
DEFAULT_LIMIT = 50


class QueryTooComplex(Exception):
    pass


@dataclass
class SearchQuery:
    terms: list = field(default_factory=list)
    phrase: str | None = None
    prefix: str | None = None
    near: tuple | None = None  # (list[str], distance)
    conversations: list = field(default_factory=list)
    contacts: list = field(default_factory=list)
    direction: str | None = None
    from_ms: int | None = None
    to_ms: int | None = None
    origins: list = field(default_factory=list)
    limit: int = DEFAULT_LIMIT


def _quote(tok: str) -> str:
    return '"' + tok.replace('"', '""') + '"'


def _caps(q: SearchQuery) -> None:
    if len(q.terms) > MAX_TERMS:
        raise QueryTooComplex("too many terms")
    if q.near and len(q.near[0]) > MAX_NEAR:
        raise QueryTooComplex("NEAR list too wide")
    if q.limit and q.limit > MAX_LIMIT:
        raise QueryTooComplex("limit too high")


def _guard_len(match: str) -> str:
    if len(match.encode("utf-8")) > MAX_QUERY_BYTES:
        raise QueryTooComplex("compiled query too long")
    return match


def compile_lexical(q: SearchQuery) -> str:
    _caps(q)
    parts: list[str] = []
    for t in q.terms:
        s = N.normalise_query(t)[0].strip()
        if s:
            parts.append(_quote(s))
    if q.phrase:
        s = N.normalise_query(q.phrase)[0].strip()
        if s:
            parts.append(_quote(s))
    if q.prefix:
        s = N.normalise_query(q.prefix)[0].strip()
        if s:
            parts.append(_quote(s) + "*")
    if q.near:
        terms, dist = q.near
        toks = [_quote(N.normalise_query(t)[0].strip()) for t in terms if N.normalise_query(t)[0].strip()]
        if toks:
            parts.append(f"NEAR({' '.join(toks)}, {int(dist)})")
    return _guard_len(" ".join(parts))


def compile_compact(q: SearchQuery) -> str:
    _caps(q)
    parts: list[str] = []
    sources = list(q.terms) + ([q.phrase] if q.phrase else [])
    for t in sources:
        c = N.normalise_query(t)[1].strip()
        if len(c) >= 3:  # trigram needs >=3 chars
            parts.append(_quote(c))
    return _guard_len(" ".join(parts))


def _filters(q: SearchQuery):
    preds, params = [], []
    if q.conversations:
        preds.append("m.conversation_id IN ({})".format(",".join("?" * len(q.conversations))))
        params += list(q.conversations)
    if q.contacts:
        preds.append("m.sender_contact_id IN ({})".format(",".join("?" * len(q.contacts))))
        params += list(q.contacts)
    if q.direction:
        preds.append("m.direction=?")
        params.append(q.direction)
    if q.origins:
        preds.append("m.origin IN ({})".format(",".join("?" * len(q.origins))))
        params += list(q.origins)
    if q.from_ms is not None:  # interval overlap (#33), never a lower bound
        preds.append("m.ts_upper_ms_exclusive > ?")
        params.append(q.from_ms)
    if q.to_ms is not None:
        preds.append("m.ts_lower_ms < ?")
        params.append(q.to_ms)
    return preds, params


def _tier(conn, fts: str, match: str, q: SearchQuery, lim: int):
    if fts not in _FTS_TABLES:  # table name is interpolated; never caller-supplied
        raise ValueError(f"unknown FTS table {fts!r}")
    preds, params = _filters(q)
    where = " AND ".join([f"{fts} MATCH ?", *preds])
    sql = (
        f"SELECT sd.message_id, m.text_original, m.conversation_id, bm25({fts}) AS rank "
        f"FROM {fts} f JOIN search_documents sd ON sd.rowid=f.rowid "
        f"JOIN messages m ON m.id=sd.message_id WHERE {where} ORDER BY rank LIMIT ?"
    )
    return conn.execute(sql, [match, *params, lim]).fetchall()


def run(conn, q: SearchQuery) -> list[dict]:
    lim = min(q.limit or DEFAULT_LIMIT, MAX_LIMIT)
    results: list[dict] = []
    seen: set = set()
    lex = compile_lexical(q)
    if lex:
        for r in _tier(conn, "fts_lexical", lex, q, lim):
            if r["message_id"] in seen:
                continue
            seen.add(r["message_id"])
            results.append(
                {
                    "message_id": r["message_id"],
                    "text_original": r["text_original"],
                    "conversation_id": r["conversation_id"],
                    "rank": r["rank"],
                    "tier": "lexical",
                }
            )
    comp = compile_compact(q)
    if comp and len(results) < lim:
        for r in _tier(conn, "fts_compact", comp, q, lim):
            if r["message_id"] in seen:
                continue
            seen.add(r["message_id"])
            results.append(
                {
                    "message_id": r["message_id"],
                    "text_original": r["text_original"],
                    "conversation_id": r["conversation_id"],
                    "rank": r["rank"],
                    "tier": "compact",
                }
            )
    return results[:lim]
