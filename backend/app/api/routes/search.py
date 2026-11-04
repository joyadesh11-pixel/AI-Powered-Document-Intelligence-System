"""/search: keyword, semantic or hybrid search over invoices and page text."""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any, Literal

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.deps import get_embedder, get_session
from app.api.schemas import SearchPageOut, SearchResultOut
from app.services import search

router = APIRouter(tags=["search"])


@router.get("/search", response_model=SearchPageOut)
def search_endpoint(
    q: str = Query(min_length=1, max_length=500),
    mode: Literal["keyword", "semantic", "hybrid"] = "hybrid",
    supplier: str | None = Query(default=None, max_length=200),
    status: str | None = None,
    currency: str | None = Query(default=None, min_length=3, max_length=3),
    date_from: date | None = None,
    date_to: date | None = None,
    min_total: Decimal | None = None,
    max_total: Decimal | None = None,
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    session: Session = Depends(get_session),
    embedder: Any = Depends(get_embedder),
) -> SearchPageOut:
    page = search.query(
        q, mode, session=session, embedder=embedder, supplier=supplier, status=status,
        currency=currency, date_from=date_from, date_to=date_to, min_total=min_total,
        max_total=max_total, limit=limit, offset=offset,
    )
    return SearchPageOut(
        items=[SearchResultOut.model_validate(r) for r in page.items],
        total=page.total, limit=limit, offset=offset, degraded=page.degraded,
    )
