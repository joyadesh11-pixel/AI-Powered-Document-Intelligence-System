"""Search service: keyword, semantic, and hybrid (RRF) search over chunks.

Keyword mode: plainto_tsquery on chunks.tsv + ILIKE on invoice fields.
Semantic mode: embed query, find nearest neighbours by cosine distance.
Hybrid mode: Reciprocal Rank Fusion (k=60) of both result lists.

Filters (status, supplier, currency, date_from/to, min/max_total) apply to invoices.
For page chunks (no invoice_id), filters are skipped unless they would exclude
the chunk's parent document — this is implemented via a LEFT JOIN.
Results are grouped by invoice_id (or document_id for page-only hits) so one
document never floods the result list.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from sqlalchemy import func, text
from sqlalchemy.orm import Session

from app.db.models import Chunk, Document, Invoice

if TYPE_CHECKING:
    from app.services.embedder import Embedder

_RRF_K = 60
_KEYWORD_LIMIT = 50
_SEMANTIC_LIMIT = 50


def _escape_ilike(s: str) -> str:
    """Escape % and _ in ILIKE patterns (PostgreSQL uses \\ as escape)."""
    return s.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


@dataclass
class SearchResult:
    document_id: Any
    invoice_id: Any | None
    supplier_name: str | None
    invoice_number: str | None
    invoice_date: str | None
    total: Any | None
    status: str | None
    snippet: str | None
    score: float
    matched_by: str  # 'keyword' | 'semantic' | 'hybrid'


def query(
    q: str,
    mode: str = "hybrid",
    *,
    session: Session,
    embedder: "Embedder | None" = None,
    supplier: str | None = None,
    status: str | None = None,
    currency: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    min_total: Decimal | None = None,
    max_total: Decimal | None = None,
    limit: int = 20,
    offset: int = 0,
) -> list[SearchResult]:
    if mode == "keyword":
        rows = _keyword_search(q, session, supplier=supplier, status=status, currency=currency,
                               date_from=date_from, date_to=date_to,
                               min_total=min_total, max_total=max_total, limit=_KEYWORD_LIMIT)
        results = _rows_to_results(rows, "keyword")
    elif mode == "semantic":
        if embedder is None:
            raise ValueError("embedder required for semantic search")
        results = _semantic_search(q, embedder, session, supplier=supplier, status=status,
                                   currency=currency, date_from=date_from, date_to=date_to,
                                   min_total=min_total, max_total=max_total, limit=_SEMANTIC_LIMIT)
    elif mode == "hybrid":
        if embedder is None:
            raise ValueError("embedder required for hybrid search")
        kw_rows = _keyword_search(q, session, supplier=supplier, status=status, currency=currency,
                                  date_from=date_from, date_to=date_to,
                                  min_total=min_total, max_total=max_total, limit=_KEYWORD_LIMIT)
        sem_results = _semantic_search(q, embedder, session, supplier=supplier, status=status,
                                       currency=currency, date_from=date_from, date_to=date_to,
                                       min_total=min_total, max_total=max_total, limit=_SEMANTIC_LIMIT)
        results = _rrf_fusion(kw_rows, sem_results)
    else:
        raise ValueError(f"Unknown search mode: {mode!r}")

    # Deduplicate by invoice_id / document_id, keep highest score
    seen: dict[str, SearchResult] = {}
    for r in results:
        key = str(r.invoice_id) if r.invoice_id else str(r.document_id)
        if key not in seen or r.score > seen[key].score:
            seen[key] = r

    deduped = sorted(seen.values(), key=lambda r: r.score, reverse=True)
    return deduped[offset: offset + limit]


# ---------------------------------------------------------------------------
# Keyword search
# ---------------------------------------------------------------------------

def _keyword_search(
    q: str,
    session: Session,
    **filters: Any,
) -> list[dict]:
    limit = filters.pop("limit", _KEYWORD_LIMIT)

    # Build the raw SQL query for keyword search
    # We use text() for the tsquery/ts_rank expressions
    q_escaped = _escape_ilike(q)
    q_like = f"%{q_escaped}%"

    sql = text("""
        SELECT
            c.id          AS chunk_id,
            c.document_id,
            c.invoice_id,
            c.kind,
            c.text        AS chunk_text,
            ts_rank(c.tsv, plainto_tsquery('simple', :q)) AS rank,
            ts_headline(
                'simple', c.text,
                plainto_tsquery('simple', :q),
                'MaxWords=20,MinWords=5,ShortWord=3,MaxFragments=1'
            )             AS snippet,
            i.supplier_name,
            i.invoice_number,
            i.invoice_date,
            i.total,
            i.status      AS inv_status,
            i.currency
        FROM chunks c
        LEFT JOIN invoices i ON c.invoice_id = i.id
        WHERE (
            c.tsv @@ plainto_tsquery('simple', :q)
            OR i.invoice_number ILIKE :q_like ESCAPE '\\'
            OR i.supplier_name  ILIKE :q_like ESCAPE '\\'
        )
        {filter_clause}
        ORDER BY rank DESC
        LIMIT :lim
    """)

    filter_parts, params = _build_filter_sql(filters, table_prefix="i")
    filter_clause = ("AND " + " AND ".join(filter_parts)) if filter_parts else ""

    params.update({"q": q, "q_like": q_like, "lim": limit})
    # Re-inject filter clause into sql
    sql = text(sql.text.format(filter_clause=filter_clause))

    rows = session.execute(sql, params).mappings().all()
    return [dict(r) for r in rows]


def _rows_to_results(rows: list[dict], matched_by: str) -> list[SearchResult]:
    results = []
    for r in rows:
        results.append(SearchResult(
            document_id=r["document_id"],
            invoice_id=r.get("invoice_id"),
            supplier_name=r.get("supplier_name"),
            invoice_number=r.get("invoice_number"),
            invoice_date=r.get("invoice_date"),
            total=r.get("total"),
            status=r.get("inv_status"),
            snippet=r.get("snippet"),
            score=float(r.get("rank") or 0.0),
            matched_by=matched_by,
        ))
    return results


# ---------------------------------------------------------------------------
# Semantic search
# ---------------------------------------------------------------------------

def _semantic_search(
    q: str,
    embedder: "Embedder",
    session: Session,
    **filters: Any,
) -> list[SearchResult]:
    limit = filters.pop("limit", _SEMANTIC_LIMIT)

    q_vec = embedder.embed([q], task_type="RETRIEVAL_QUERY")[0]

    # Build filter clause — applied before cosine ranking
    filter_parts, params = _build_filter_sql(filters, table_prefix="i")
    filter_clause = ("AND " + " AND ".join(filter_parts)) if filter_parts else ""

    sql = text(f"""
        SELECT
            c.id          AS chunk_id,
            c.document_id,
            c.invoice_id,
            c.kind,
            c.text        AS chunk_text,
            (1 - (c.embedding <=> CAST(:q_vec AS vector))) AS score,
            NULL::text    AS snippet,
            i.supplier_name,
            i.invoice_number,
            i.invoice_date,
            i.total,
            i.status      AS inv_status,
            i.currency
        FROM chunks c
        LEFT JOIN invoices i ON c.invoice_id = i.id
        WHERE c.indexed = true
        {filter_clause}
        ORDER BY c.embedding <=> CAST(:q_vec AS vector)
        LIMIT :lim
    """)

    params["q_vec"] = str(q_vec)
    params["lim"] = limit

    rows = session.execute(sql, params).mappings().all()
    return [
        SearchResult(
            document_id=r["document_id"],
            invoice_id=r.get("invoice_id"),
            supplier_name=r.get("supplier_name"),
            invoice_number=r.get("invoice_number"),
            invoice_date=r.get("invoice_date"),
            total=r.get("total"),
            status=r.get("inv_status"),
            snippet=r.get("snippet"),
            score=float(r.get("score") or 0.0),
            matched_by="semantic",
        )
        for r in rows
    ]


# ---------------------------------------------------------------------------
# Hybrid RRF fusion
# ---------------------------------------------------------------------------

def _rrf_fusion(
    kw_rows: list[dict],
    sem_results: list[SearchResult],
    k: int = _RRF_K,
) -> list[SearchResult]:
    """Reciprocal Rank Fusion of keyword and semantic result lists."""
    # Build chunk_id → rank maps
    kw_rank: dict[Any, int] = {r["chunk_id"]: i for i, r in enumerate(kw_rows)}
    sem_rank: dict[Any, int] = {
        (r.invoice_id or r.document_id): i for i, r in enumerate(sem_results)
    }

    # Collect all unique keys
    all_kw_chunk_ids = {r["chunk_id"] for r in kw_rows}
    all_sem_keys = {(r.invoice_id or r.document_id) for r in sem_results}

    # Score by chunk_id for keyword; key for semantic
    # We'll use chunk-level scoring for KW, then map to deduplicated SearchResult
    scores: dict[Any, float] = {}

    chunk_to_result: dict[Any, dict] = {r["chunk_id"]: r for r in kw_rows}
    key_to_sem: dict[Any, SearchResult] = {
        (r.invoice_id or r.document_id): r for r in sem_results
    }

    for chunk_id, rank in kw_rank.items():
        scores[chunk_id] = scores.get(chunk_id, 0.0) + 1.0 / (k + rank + 1)

    for key, rank in sem_rank.items():
        scores[key] = scores.get(key, 0.0) + 1.0 / (k + rank + 1)

    # Build merged results
    merged: list[SearchResult] = []
    seen_chunk_ids: set[Any] = set()
    seen_sem_keys: set[Any] = set()

    # Add keyword results
    for chunk_id, row in chunk_to_result.items():
        if chunk_id in seen_chunk_ids:
            continue
        seen_chunk_ids.add(chunk_id)
        merged.append(SearchResult(
            document_id=row["document_id"],
            invoice_id=row.get("invoice_id"),
            supplier_name=row.get("supplier_name"),
            invoice_number=row.get("invoice_number"),
            invoice_date=row.get("invoice_date"),
            total=row.get("total"),
            status=row.get("inv_status"),
            snippet=row.get("snippet"),
            score=scores.get(chunk_id, 0.0),
            matched_by="hybrid",
        ))

    # Add semantic results not already in keyword
    for r in sem_results:
        key = r.invoice_id or r.document_id
        if key in seen_sem_keys:
            continue
        seen_sem_keys.add(key)
        merged.append(SearchResult(
            document_id=r.document_id,
            invoice_id=r.invoice_id,
            supplier_name=r.supplier_name,
            invoice_number=r.invoice_number,
            invoice_date=r.invoice_date,
            total=r.total,
            status=r.status,
            snippet=r.snippet,
            score=scores.get(key, 0.0),
            matched_by="hybrid",
        ))

    merged.sort(key=lambda r: r.score, reverse=True)
    return merged


# ---------------------------------------------------------------------------
# Filter SQL builder
# ---------------------------------------------------------------------------

def _build_filter_sql(
    filters: dict[str, Any],
    table_prefix: str = "i",
) -> tuple[list[str], dict[str, Any]]:
    """Build WHERE clause fragments and parameters for invoice-level filters.

    Returns (filter_parts, params).  Filters on NULL invoice_id (page chunks)
    are skipped via IS NULL check so page chunks are always included when no
    invoice filter applies.
    """
    parts: list[str] = []
    params: dict[str, Any] = {}
    p = table_prefix

    if filters.get("status"):
        parts.append(f"({p}.status = :f_status OR {p}.status IS NULL)")
        params["f_status"] = filters["status"]

    if filters.get("currency"):
        parts.append(f"({p}.currency = :f_currency OR {p}.currency IS NULL)")
        params["f_currency"] = filters["currency"]

    if filters.get("supplier"):
        esc = _escape_ilike(filters["supplier"])
        parts.append(f"({p}.supplier_name ILIKE :f_supplier ESCAPE '\\\\' OR {p}.supplier_name IS NULL)")
        params["f_supplier"] = f"%{esc}%"

    if filters.get("date_from"):
        parts.append(f"({p}.invoice_date >= :f_date_from OR {p}.invoice_date IS NULL)")
        params["f_date_from"] = filters["date_from"]

    if filters.get("date_to"):
        parts.append(f"({p}.invoice_date <= :f_date_to OR {p}.invoice_date IS NULL)")
        params["f_date_to"] = filters["date_to"]

    if filters.get("min_total") is not None:
        parts.append(f"({p}.total >= :f_min_total OR {p}.total IS NULL)")
        params["f_min_total"] = str(filters["min_total"])

    if filters.get("max_total") is not None:
        parts.append(f"({p}.total <= :f_max_total OR {p}.total IS NULL)")
        params["f_max_total"] = str(filters["max_total"])

    return parts, params
