"""Search service: keyword, semantic, and hybrid (RRF) search over chunks.

Keyword:  plainto_tsquery('simple') on chunks.tsv, plus ILIKE on invoice
          number / supplier (escaped, so % and _ in the query are literal).
Semantic: embed the query (RETRIEVAL_QUERY), cosine distance via pgvector,
          only chunks with indexed=true.
Hybrid:   Reciprocal Rank Fusion (k=60).  Each list is first grouped by result
          key (invoice for summary chunks, document for page chunks) so a key
          found by both lists is fused into one result with matched_by="both".
          If the query cannot be embedded, hybrid degrades to keyword.

Invoice filters (supplier, status, currency, dates, totals) apply to the
chunk's invoice; a page chunk passes only if its document has an invoice that
matches.  All SQL is built from SQLAlchemy expressions with bound parameters.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from sqlalchemy import and_, case, exists, func, literal_column, or_, select, true
from sqlalchemy.orm import Session, aliased

from app.db.models import Chunk, Document, Invoice
from app.services.errors import ServiceError

if TYPE_CHECKING:
    from app.services.embedder import Embedder

log = logging.getLogger(__name__)

_RRF_K = 60
_KEYWORD_POOL = 200
_SEMANTIC_LIMIT = 50
_HEADLINE_OPTS = "MaxWords=20,MinWords=5,ShortWord=3,MaxFragments=1"
# a constant regconfig, never user input
_SIMPLE = literal_column("'simple'::regconfig")


class EmbeddingUnavailable(ServiceError):
    code = "EMBEDDING_UNAVAILABLE"


@dataclass
class SearchResult:
    document_id: Any
    invoice_id: Any | None
    kind: str                      # 'invoice_summary' | 'page'
    filename: str | None
    supplier_name: str | None
    invoice_number: str | None
    invoice_date: str | None
    total: Decimal | None
    currency: str | None
    status: str | None             # invoice status, or document status for page hits
    snippet: str | None
    score: float
    matched_by: str                # 'keyword' | 'semantic' | 'both'


@dataclass
class SearchPage:
    items: list[SearchResult] = field(default_factory=list)
    total: int = 0
    degraded: bool = False         # hybrid fell back to keyword (embedding unavailable)


def escape_like(s: str) -> str:
    """Escape LIKE metacharacters; pair with escape='\\\\'."""
    return s.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def query(
    q: str,
    mode: str = "hybrid",
    *,
    session: Session,
    embedder: "Embedder | None" = None,
    supplier: str | None = None,
    status: str | None = None,
    currency: str | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    min_total: Decimal | None = None,
    max_total: Decimal | None = None,
    limit: int = 20,
    offset: int = 0,
) -> SearchPage:
    filters = dict(supplier=supplier, status=status, currency=currency, date_from=date_from,
                   date_to=date_to, min_total=min_total, max_total=max_total)
    flt = _filter_clause(filters)
    degraded = False

    if mode == "keyword":
        ranked = _group(_keyword_rows(q, session, flt), "keyword")
    elif mode == "semantic":
        ranked = _group(_semantic_rows(_embed_query(q, embedder), q, session, flt), "semantic")
    elif mode == "hybrid":
        kw = _group(_keyword_rows(q, session, flt), "keyword")
        try:
            sem = _group(_semantic_rows(_embed_query(q, embedder), q, session, flt), "semantic")
        except EmbeddingUnavailable:
            ranked, degraded = kw, True
        else:
            ranked = _rrf(kw, sem)
    else:
        raise ValueError(f"Unknown search mode: {mode!r}")

    return SearchPage(items=ranked[offset: offset + limit], total=len(ranked), degraded=degraded)


# ---------------------------------------------------------------------------
# Filters
# ---------------------------------------------------------------------------

def _invoice_conditions(inv: Any, filters: dict[str, Any]) -> list:
    conds = []
    if filters.get("status"):
        conds.append(inv.status == filters["status"])
    if filters.get("currency"):
        conds.append(inv.currency == filters["currency"].upper())
    if filters.get("supplier"):
        conds.append(inv.supplier_name.ilike(f"%{escape_like(filters['supplier'])}%", escape="\\"))
    # invoice_date is stored as ISO text, so string comparison orders correctly
    if filters.get("date_from"):
        conds.append(inv.invoice_date >= filters["date_from"].isoformat())
    if filters.get("date_to"):
        conds.append(inv.invoice_date <= filters["date_to"].isoformat())
    if filters.get("min_total") is not None:
        conds.append(inv.total >= filters["min_total"])
    if filters.get("max_total") is not None:
        conds.append(inv.total <= filters["max_total"])
    return conds


def _filter_clause(filters: dict[str, Any]):
    """Summary chunks: their invoice must match.  Page chunks: some invoice of the document must."""
    if not _invoice_conditions(Invoice, filters):
        return true()
    other = aliased(Invoice)
    doc_has_match = exists().where(other.document_id == Chunk.document_id,
                                   *_invoice_conditions(other, filters))
    return or_(
        and_(Chunk.invoice_id.is_not(None), *_invoice_conditions(Invoice, filters)),
        and_(Chunk.invoice_id.is_(None), doc_has_match),
    )


# ---------------------------------------------------------------------------
# Candidate lists
# ---------------------------------------------------------------------------

def _select(score, snippet):
    return (
        select(
            Chunk.document_id, Chunk.invoice_id, Chunk.kind,
            score.label("score"), snippet.label("snippet"),
            Document.filename, Document.status.label("doc_status"),
            Invoice.supplier_name, Invoice.invoice_number, Invoice.invoice_date,
            Invoice.total, Invoice.currency, Invoice.status.label("inv_status"),
        )
        .select_from(Chunk)
        .join(Document, Chunk.document_id == Document.id)
        .outerjoin(Invoice, Chunk.invoice_id == Invoice.id)
    )


def _keyword_rows(q: str, session: Session, flt) -> list[Any]:
    tsq = func.plainto_tsquery(_SIMPLE, q)
    like = f"%{escape_like(q)}%"
    field_hit = or_(Invoice.invoice_number.ilike(like, escape="\\"),
                    Invoice.supplier_name.ilike(like, escape="\\"))
    # an exact field hit outranks any ts_rank (which is < 1 for short chunks)
    score = func.ts_rank(Chunk.tsv, tsq) + case((field_hit, 1.0), else_=0.0)
    stmt = (
        _select(score, func.ts_headline(_SIMPLE, Chunk.text, tsq, _HEADLINE_OPTS))
        .where(or_(Chunk.tsv.op("@@")(tsq), field_hit), flt)
        .order_by(score.desc(), Chunk.id)
        .limit(_KEYWORD_POOL)
    )
    return list(session.execute(stmt))


def _embed_query(q: str, embedder: "Embedder | None") -> list[float]:
    if embedder is None:
        raise EmbeddingUnavailable("No embedder configured")
    try:
        return embedder.embed([q], task_type="RETRIEVAL_QUERY")[0]
    except Exception as exc:
        log.warning("Query embedding failed: %s", type(exc).__name__)
        raise EmbeddingUnavailable("Semantic search is temporarily unavailable") from exc


def _semantic_rows(q_vec: list[float], q: str, session: Session, flt) -> list[Any]:
    distance = Chunk.embedding.cosine_distance(q_vec)
    tsq = func.plainto_tsquery(_SIMPLE, q)
    stmt = (
        _select(1 - distance, func.ts_headline(_SIMPLE, Chunk.text, tsq, _HEADLINE_OPTS))
        .where(Chunk.indexed.is_(True), Chunk.embedding.is_not(None), flt)
        .order_by(distance, Chunk.id)
        .limit(_SEMANTIC_LIMIT)
    )
    return list(session.execute(stmt))


# ---------------------------------------------------------------------------
# Grouping and fusion
# ---------------------------------------------------------------------------

def _key(row: Any) -> tuple:
    return ("inv", row.invoice_id) if row.invoice_id else ("doc", row.document_id)


def _to_result(row: Any, matched_by: str) -> SearchResult:
    return SearchResult(
        document_id=row.document_id,
        invoice_id=row.invoice_id,
        kind=row.kind,
        filename=row.filename,
        supplier_name=row.supplier_name,
        invoice_number=row.invoice_number,
        invoice_date=row.invoice_date,
        total=row.total,
        currency=row.currency,
        status=row.inv_status or row.doc_status,
        snippet=row.snippet,
        score=float(row.score or 0.0),
        matched_by=matched_by,
    )


def _group(rows: list[Any], matched_by: str) -> list[SearchResult]:
    """Keep the best-ranked row per result key, preserving rank order."""
    seen: dict[tuple, SearchResult] = {}
    for row in rows:
        k = _key(row)
        if k not in seen:
            seen[k] = _to_result(row, matched_by)
    return list(seen.values())


def _rrf(kw: list[SearchResult], sem: list[SearchResult], k: int = _RRF_K) -> list[SearchResult]:
    """Reciprocal Rank Fusion over grouped lists (ranks start at 1)."""
    def key(r: SearchResult) -> tuple:
        return ("inv", r.invoice_id) if r.invoice_id else ("doc", r.document_id)

    fused: dict[tuple, SearchResult] = {}
    scores: dict[tuple, float] = {}
    for rank, r in enumerate(kw, start=1):
        fused[key(r)] = r          # keyword row first: its snippet is highlighted
        scores[key(r)] = 1.0 / (k + rank)
    for rank, r in enumerate(sem, start=1):
        kk = key(r)
        scores[kk] = scores.get(kk, 0.0) + 1.0 / (k + rank)
        if kk in fused:
            fused[kk].matched_by = "both"
        else:
            fused[kk] = r
    for kk, r in fused.items():
        r.score = scores[kk]
    return sorted(fused.values(), key=lambda r: r.score, reverse=True)
