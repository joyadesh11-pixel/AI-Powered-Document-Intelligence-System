"""Pydantic v2 schemas for the Document Intelligence pipeline.

All extracted fields default to None. Decimal serialises as string in JSON.
"""
from __future__ import annotations

import json
from datetime import date
from decimal import Decimal
from enum import Enum
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, field_serializer, model_serializer


# ------------------------------------------------------------------ enums

class DocStatus(str, Enum):
    queued = "queued"
    processing = "processing"
    validated_ok = "validated_ok"
    needs_review = "needs_review"
    failed = "failed"


class InvoiceStatus(str, Enum):
    validated_ok = "validated_ok"
    needs_review = "needs_review"


class Severity(str, Enum):
    error = "error"
    warning = "warning"
    info = "info"


class Tier(str, Enum):
    sheet = "sheet"
    parser = "parser"
    ocr = "ocr"
    vision = "vision"


class ErrorCode(str, Enum):
    CORRUPT_FILE = "CORRUPT_FILE"
    UNSUPPORTED_TYPE = "UNSUPPORTED_TYPE"
    ENCRYPTED = "ENCRYPTED"
    TOO_LARGE = "TOO_LARGE"
    TOO_MANY_PAGES = "TOO_MANY_PAGES"
    EMPTY_FILE = "EMPTY_FILE"
    LLM_CONFIG_ERROR = "LLM_CONFIG_ERROR"
    INTERNAL_ERROR = "INTERNAL_ERROR"


class IssueCode(str, Enum):
    MISSING_REQUIRED = "MISSING_REQUIRED"
    MISSING_OPTIONAL = "MISSING_OPTIONAL"
    BAD_NUMBER = "BAD_NUMBER"
    BAD_DATE = "BAD_DATE"
    DATE_ORDER = "DATE_ORDER"
    DATE_RANGE = "DATE_RANGE"
    BAD_CURRENCY = "BAD_CURRENCY"
    NEGATIVE_AMOUNT = "NEGATIVE_AMOUNT"
    ARITH_LINE = "ARITH_LINE"
    ARITH_SUBTOTAL = "ARITH_SUBTOTAL"
    ARITH_TOTAL = "ARITH_TOTAL"
    LINES_MISSING = "LINES_MISSING"
    NOT_GROUNDED = "NOT_GROUNDED"
    DUPLICATE_INVOICE = "DUPLICATE_INVOICE"
    UNCERTAIN_FIELD = "UNCERTAIN_FIELD"
    LOW_OCR_CONF = "LOW_OCR_CONF"
    LOW_IMAGE_QUALITY = "LOW_IMAGE_QUALITY"
    NO_INVOICE_DETECTED = "NO_INVOICE_DETECTED"
    EXTRACTION_FAILED = "EXTRACTION_FAILED"
    VISION_FAILED = "VISION_FAILED"
    VISION_PAGES_CAPPED = "VISION_PAGES_CAPPED"
    CONTEXT_TRUNCATED = "CONTEXT_TRUNCATED"
    MAPPING_FAILED = "MAPPING_FAILED"
    UNGROUPED_ROWS = "UNGROUPED_ROWS"
    SHEET_GROUP_INCONSISTENT = "SHEET_GROUP_INCONSISTENT"
    SHEET_SKIPPED = "SHEET_SKIPPED"
    FORMULA_NO_CACHE = "FORMULA_NO_CACHE"
    TRUNCATED_IMAGE = "TRUNCATED_IMAGE"
    EXT_MISMATCH = "EXT_MISMATCH"
    SEGMENT_FALLBACK = "SEGMENT_FALLBACK"
    ARITH_MISMATCH = "ARITH_MISMATCH"  # used in dry-run stubs


# --------------------------------------------------------- LLM-facing models

class LineItemLLM(BaseModel):
    description: str | None = None
    quantity: str | None = None
    unit_price: str | None = None
    amount: str | None = None


class ExtractedInvoice(BaseModel):
    invoice_number: str | None = None
    invoice_date: str | None = None
    due_date: str | None = None
    supplier_name: str | None = None
    customer_name: str | None = None
    subtotal: str | None = None
    tax: str | None = None
    total: str | None = None
    currency: str | None = None
    line_items: list[LineItemLLM] = []
    uncertain_fields: list[str] = []


class VisionExtraction(ExtractedInvoice):
    source_text: str | None = None


# -------------------------------------------------------- normalised models

def _decimal_json(v: Decimal | None) -> str | None:
    if v is None:
        return None
    return str(v)


class LineItem(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    position: int
    description: str | None = None
    quantity: Decimal | None = None
    unit_price: Decimal | None = None
    amount: Decimal | None = None

    @field_serializer("quantity", "unit_price", "amount")
    def _ser_decimal(self, v: Decimal | None) -> str | None:
        return _decimal_json(v)


class Issue(BaseModel):
    code: IssueCode
    severity: Severity
    field: str | None = None
    expected: str | None = None
    got: str | None = None
    message: str
    location: dict = {}


class Invoice(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    unit_id: int | None = None
    invoice_number: str | None = None
    invoice_date: date | None = None
    due_date: date | None = None
    supplier_name: str | None = None
    customer_name: str | None = None
    subtotal: Decimal | None = None
    tax: Decimal | None = None
    total: Decimal | None = None
    currency: str | None = None
    line_items: list[LineItem] = []
    uncertain_fields: list[str] = []
    raw: dict = {}
    derived: dict = {}
    extra: dict = {}
    tier: Tier
    source_pages: list[int] = []
    source_rows: list[int] = []
    status: InvoiceStatus = InvoiceStatus.needs_review
    issues: list[Issue] = []
    alternatives: list[dict] = []
    corrected: bool = False

    @field_serializer("subtotal", "tax", "total")
    def _ser_decimal(self, v: Decimal | None) -> str | None:
        return _decimal_json(v)


class PipelineResult(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    status: DocStatus
    error_code: ErrorCode | None = None
    error: str | None = None
    file_hash: str
    file_type: str
    size_bytes: int
    page_count: int
    invoices: list[Invoice] = []
    warnings: list[Issue] = []
    page_text: dict[int, str] = {}
    page_source: dict[int, str] = {}
    ocr_conf: dict[int, float] = {}
    timings: dict = {}
    history: list[dict] = []
    llm_usage: dict = {}
    pipeline_version: str


# ---------------------------------------------------------- sheet mapping

class SheetMapping(BaseModel):
    grain: Literal["line_item", "invoice"]
    group_by: str | None = None
    invoice_fields: dict[str, str] = {}
    line_fields: dict[str, str] = {}
    date_format: str | None = None
    decimal_separator: Literal[".", ","] = "."
    thousands_separator: Literal[",", ".", " ", ""] = ","
    currency: str | None = None
    currency_column: str | None = None
    unmapped_columns: list[str] = []
