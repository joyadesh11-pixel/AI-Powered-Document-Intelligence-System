"""Response and request models. Money is always serialised as a string, never a float."""
from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Annotated, Any, Generic, TypeVar

from pydantic import BaseModel, ConfigDict, PlainSerializer, StrictStr

T = TypeVar("T")

Money = Annotated[
    Decimal | None,
    PlainSerializer(lambda v: None if v is None else format(v, "f"), return_type=str | None),
]


class _Out(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class Page(BaseModel, Generic[T]):
    items: list[T]
    total: int
    limit: int
    offset: int


class ErrorBody(BaseModel):
    code: str
    message: str


class UploadRow(BaseModel):
    document_id: uuid.UUID | None = None
    filename: str
    status: str | None = None
    cached: bool = False
    error: ErrorBody | None = None


class IssueOut(_Out):
    id: uuid.UUID
    invoice_id: uuid.UUID | None
    code: str
    severity: str
    field: str | None
    expected: str | None
    got: str | None
    message: str
    location: dict | None
    resolved: bool


class LineItemOut(_Out):
    position: int
    description: str | None
    quantity: Money
    unit_price: Money
    amount: Money


class CorrectionOut(_Out):
    field: str
    old_value: str | None
    new_value: str | None
    at: datetime


class EventOut(_Out):
    id: int
    attempt: int
    node: str
    seconds: float | None
    ok: bool | None
    meta: dict | None
    at: datetime


class InvoiceOut(_Out):
    id: uuid.UUID
    document_id: uuid.UUID
    unit_index: int | None
    invoice_number: str | None
    invoice_date: str | None
    due_date: str | None
    supplier_name: str | None
    customer_name: str | None
    subtotal: Money
    tax: Money
    total: Money
    currency: str | None
    status: str
    corrected: bool
    tier: str | None
    uncertain_fields: list | None
    source_pages: list[int] | None
    source_rows: list[int] | None
    dup_of: uuid.UUID | None
    created_at: datetime
    updated_at: datetime
    line_items: list[LineItemOut]
    issues: list[IssueOut]
    corrections: list[CorrectionOut]


class DocumentBase(_Out):
    id: uuid.UUID
    filename: str
    file_type: str | None
    size_bytes: int | None
    page_count: int | None
    status: str
    current_node: str | None
    attempt: int
    error_code: str | None
    error: str | None
    created_at: datetime
    updated_at: datetime
    finished_at: datetime | None


class DocumentRowOut(DocumentBase):
    invoice_count: int = 0
    issue_count: int = 0
    error_count: int = 0


class DocumentDetailOut(DocumentBase):
    tier_summary: dict | None = None
    timings: dict | None = None
    llm_usage: dict | None = None
    events: list[EventOut]
    invoices: list[InvoiceOut]
    issues: list[IssueOut]


class PageOut(_Out):
    page_no: int
    text: str | None
    source: str | None
    ocr_conf: float | None


class SearchResultOut(_Out):
    document_id: uuid.UUID
    invoice_id: uuid.UUID | None
    kind: str
    filename: str | None
    supplier_name: str | None
    invoice_number: str | None
    invoice_date: str | None
    total: Money
    currency: str | None
    status: str | None
    snippet: str | None
    score: float
    matched_by: str


class SearchPageOut(Page[SearchResultOut]):
    degraded: bool = False


class LineItemPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    position: int
    description: StrictStr | None = None
    quantity: StrictStr | None = None
    unit_price: StrictStr | None = None
    amount: StrictStr | None = None


class PatchInvoice(BaseModel):
    """Values are strings; the server parses them. A key sent as null clears the field."""
    model_config = ConfigDict(extra="forbid")

    fields: dict[str, StrictStr | None] | None = None
    line_items: list[LineItemPatch] | None = None

    def to_patch(self) -> dict[str, Any]:
        patch: dict[str, Any] = dict(self.fields or {})
        if self.line_items is not None:
            patch["line_items"] = [
                {k: getattr(li, k) for k in li.model_fields_set} for li in self.line_items
            ]
        return patch
