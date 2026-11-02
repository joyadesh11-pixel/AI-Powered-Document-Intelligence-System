# 02. Schemas, statuses, issue codes, result shape

All in `backend/app/schemas/`. Pydantic v2. **Every extracted field is optional, default `None`.**

## Status enums
```python
class DocStatus(str, Enum):      # pipeline + DB
    queued="queued"; processing="processing"
    validated_ok="validated_ok"; needs_review="needs_review"; failed="failed"
class InvoiceStatus(str, Enum):
    validated_ok="validated_ok"; needs_review="needs_review"
class Severity(str, Enum):  error="error"; warning="warning"; info="info"
class Tier(str, Enum):      sheet="sheet"; parser="parser"; ocr="ocr"; vision="vision"
```
`queued`/`processing` exist only in DB. "Corrected" is a boolean on the invoice (`corrected=true`),
not a status; the UI shows a badge. Document status = worst of its invoices
(`failed` > `needs_review` > `validated_ok`).

## LLM-facing schema (what the model returns; strings exactly as printed)
```python
class LineItemLLM(BaseModel):
    description: str | None = None
    quantity: str | None = None
    unit_price: str | None = None
    amount: str | None = None

class ExtractedInvoice(BaseModel):
    invoice_number: str | None = None
    invoice_date: str | None = None        # as printed, e.g. "03/11/2026"
    due_date: str | None = None
    supplier_name: str | None = None
    customer_name: str | None = None
    subtotal: str | None = None            # as printed, e.g. "1,234.50"
    tax: str | None = None
    total: str | None = None
    currency: str | None = None            # ISO code only if printed (code or unambiguous symbol context)
    line_items: list[LineItemLLM] = []
    uncertain_fields: list[str] = []       # model lists fields it is not sure about

class VisionExtraction(ExtractedInvoice):
    source_text: str | None = None         # verbatim transcription of the pages
```
Why strings: grounding check compares printed text with source; no float/locale bugs; code owns parsing (D3).

## Normalised invoice (what we store and validate)
```python
class LineItem(BaseModel):
    position: int
    description: str | None = None
    quantity: Decimal | None = None
    unit_price: Decimal | None = None
    amount: Decimal | None = None

class Invoice(BaseModel):
    unit_id: int | None                    # segment unit; None for sheets
    invoice_number: str | None = None
    invoice_date: date | None = None
    due_date: date | None = None
    supplier_name: str | None = None
    customer_name: str | None = None
    subtotal: Decimal | None = None
    tax: Decimal | None = None
    total: Decimal | None = None
    currency: str | None = None            # ISO 4217 upper-case
    line_items: list[LineItem] = []
    uncertain_fields: list[str] = []
    raw: dict = {}                         # the strings as printed, for the UI and grounding
    derived: dict = {}                     # e.g. {"lines_sum": "90.00"}. Never copied into real fields
    extra: dict = {}                       # unmapped sheet columns
    tier: Tier
    source_pages: list[int] = []
    source_rows: list[int] = []            # sheets: row numbers
    status: InvoiceStatus = InvoiceStatus.needs_review
    issues: list[Issue] = []
    alternatives: list[dict] = []          # losing arbitration candidates (audit)
```
Money serialises as **string** in JSON (`"1234.50"`), never a float.

## Issue
```python
class Issue(BaseModel):
    code: IssueCode
    severity: Severity
    field: str | None = None               # "total", "line_items[2].amount"
    expected: str | None = None
    got: str | None = None
    message: str
    location: dict = {}                    # {"page":2} or {"row":14,"column":"Line Amt"}
```

## Document result (pipeline output, JSON-serialisable)
```python
class PipelineResult(BaseModel):
    status: DocStatus                      # validated_ok | needs_review | failed
    error_code: ErrorCode | None = None
    error: str | None = None
    file_hash: str; file_type: str; size_bytes: int; page_count: int
    invoices: list[Invoice] = []
    warnings: list[Issue] = []             # document-level issues
    page_text: dict[int, str] = {}         # stored for search; key = page index
    page_source: dict[int, str] = {}       # digital|ocr|vision|sheet
    ocr_conf: dict[int, float] = {}
    timings: dict = {}                     # {"total": s, "by_node": {...}}
    history: list[dict] = []               # [{node, seconds, ok, attempt, meta}]
    llm_usage: dict = {}                   # calls, input_tokens, output_tokens, by_model
    pipeline_version: str
```

## Error codes (document `failed`)
`CORRUPT_FILE, UNSUPPORTED_TYPE, ENCRYPTED, TOO_LARGE, TOO_MANY_PAGES, EMPTY_FILE,
LLM_CONFIG_ERROR, INTERNAL_ERROR`

## Issue codes
Full table with severity, check and escalation in `docs/04-validation.md`.
Codes: `MISSING_REQUIRED, MISSING_OPTIONAL, BAD_NUMBER, BAD_DATE, DATE_ORDER, DATE_RANGE,
BAD_CURRENCY, NEGATIVE_AMOUNT, ARITH_LINE, ARITH_SUBTOTAL, ARITH_TOTAL, LINES_MISSING,
NOT_GROUNDED, DUPLICATE_INVOICE, UNCERTAIN_FIELD, LOW_OCR_CONF, LOW_IMAGE_QUALITY,
NO_INVOICE_DETECTED, EXTRACTION_FAILED, VISION_FAILED, VISION_PAGES_CAPPED, CONTEXT_TRUNCATED,
MAPPING_FAILED, UNGROUPED_ROWS, SHEET_GROUP_INCONSISTENT, SHEET_SKIPPED, FORMULA_NO_CACHE,
TRUNCATED_IMAGE, EXT_MISMATCH, SEGMENT_FALLBACK`

## Sheet mapping schema (`sheet_map` output)
```python
class SheetMapping(BaseModel):
    grain: Literal["line_item","invoice"]
    group_by: str | None                   # column name; required when grain == "line_item"
    invoice_fields: dict[str,str]          # target field -> source column
    line_fields: dict[str,str]             # description/quantity/unit_price/amount -> column
    date_format: str | None                # strptime format, e.g. "%d/%m/%Y"
    decimal_separator: Literal[".",","] = "."
    thousands_separator: Literal[",",".", " ", ""] = ","
    currency: str | None = None            # ISO, only if stated in sheet
    currency_column: str | None = None
    unmapped_columns: list[str] = []
```
Validator: every column named must exist in the sheet header (else `ValidationError` -> retry with feedback).

## Invariants (tests must assert)
1. No `float` anywhere in `Invoice` or `PipelineResult`.
2. A field not present in the source text is `None`, never `""` or `"N/A"`.
3. `status == validated_ok` implies zero error-severity issues.
4. `PipelineResult.model_dump_json()` round-trips.
