# 04. Validation rules and normalisation

Code: `pipeline/validation/` (one function per rule family, pure, unit-tested) + `pipeline/utils/money.py`, `dates.py`, `text_norm.py`.
Severity: **error** -> invoice `needs_review`; **warning** -> shown, does not block; **info** -> shown only.
Tolerance base `TOL_ABS = Decimal("0.01")`. All arithmetic in `Decimal`; compare with `abs(a-b) <= tol`.

## Rule catalogue
| Code | Sev | Check | Tolerance / detail | Escalates to vision? |
|---|---|---|---|---|
| MISSING_REQUIRED | error | `invoice_number, invoice_date, supplier_name, total` is null | one issue per field | yes |
| MISSING_OPTIONAL | info | `customer_name, currency, subtotal, tax` null | - | no |
| BAD_NUMBER | error | numeric raw string unparseable | field set to None, raw kept | yes |
| NEGATIVE_AMOUNT | warning | amount < 0 or quantity <= 0 | credit notes are legitimate | no |
| BAD_DATE | error | date raw string unparseable | - | yes |
| DATE_ORDER | error | `due_date < invoice_date` | - | no |
| DATE_RANGE | warning | date < `DATE_MIN` or > today + `DATE_MAX_DAYS_AHEAD` | - | no |
| BAD_CURRENCY | error | not an ISO 4217 code | upper-case, strip | yes |
| ARITH_LINE | error | `quantity * unit_price` vs `amount` (all three present) | `max(0.01, 0.005 * quantity)` (printed unit price may be rounded) | yes |
| ARITH_SUBTOTAL | error | `sum(line.amount)` vs `subtotal` (needs >= 1 line with amount) | `max(0.01, 0.005 * n_lines)` | yes |
| ARITH_TOTAL | error | `subtotal + tax` vs `total`; if `subtotal` null: `lines_sum + tax` vs `total` | `0.01`; if `tax` null treat as skipped (info) | yes |
| LINES_MISSING | warning | no line items | - | no |
| NOT_GROUNDED | error (parser) / warning (ocr) / skipped (vision, sheet) | value not found in source text | see below | yes (when error) |
| DUPLICATE_INVOICE | error | key seen in store or earlier in same file | key below | no |
| UNCERTAIN_FIELD | warning | model listed field in `uncertain_fields` | - | no |
| LOW_OCR_CONF / LOW_IMAGE_QUALITY | warning | from `ocr` / `image_prep` | - | handled at unit level |
| NO_INVOICE_DETECTED | error | all header fields null and no lines | - | yes (once) |
| EXTRACTION_FAILED, VISION_FAILED, CONTEXT_TRUNCATED, MAPPING_FAILED | error | produced by nodes | - | n/a |
| UNGROUPED_ROWS | warning | sheet rows without group key | count + first rows | no |
| SHEET_GROUP_INCONSISTENT, SHEET_SKIPPED, FORMULA_NO_CACHE, TRUNCATED_IMAGE, VISION_PAGES_CAPPED | warning | produced by nodes | - | no |
| EXT_MISMATCH, SEGMENT_FALLBACK | info | produced by nodes | - | no |

`ESCALATION_CODES` = the "yes" rows. A missing invoice number that is truly absent costs one vision call and still ends `needs_review`; acceptable.

## Grounding (hallucination guard)
Applies to header fields when the source text came from the parser (error) or OCR (warning).
- **Amounts** (`subtotal, tax, total`, line amounts): parse every numeric token in the source with `money.parse` into a set of `Decimal`; the extracted value must be in the set.
- **Invoice number**: normalise both (casefold, strip non-alphanumerics); extracted must be a substring of normalised source.
- **Dates**: the raw printed string (whitespace-collapsed) must appear in the source.
- **Supplier/customer**: `rapidfuzz.partial_ratio(casefold)` >= `GROUND_FUZZY_MIN`.
Vision: skipped (its `source_text` is model-made); arithmetic is the guard there.

## Money parsing (`utils/money.py`, table-driven tests)
| Raw | Result | Rule |
|---|---|---|
| `1,234.56` | 1234.56 | last separator is `.` with 1-2 digits after -> decimal point |
| `1.234,56` | 1234.56 | last separator is `,` with 1-2 digits after -> decimal comma |
| `1 234,56` / `1'234.56` | 1234.56 | spaces/apostrophes are thousands |
| `1,234` | 1234 | one separator followed by exactly 3 digits and no decimals -> thousands |
| `12,5` | 12.5 | one `,` followed by 1-2 digits -> decimal |
| `(45.00)` / `-45.00` / `45.00-` | -45.00 | negatives |
| `$ 1,200.00`, `EUR 99`, `99 EUR`, `¥1,200` | strip currency symbols/codes | JPY etc. have 0 decimals; parse as printed |
| `abc`, `` | `None` | caller raises `BAD_NUMBER` only if raw was non-empty |
Ambiguous cases (`1,234` in a comma-decimal locale) -> sheets use the mapping's explicit separators; documents add `UNCERTAIN_FIELD` when the rule is a guess.

## Dates (`utils/dates.py`)
Try, in order: ISO `YYYY-MM-DD`; named month (`3 Nov 2026`, `November 3, 2026`); numeric with 4-digit year using the **document's inferred order** (if any date in the unit has a part > 12, that decides day/month order; else default day-first and add `UNCERTAIN_FIELD`); two-digit years pivot 2000-2069 / 1970-1999. Sheets use the mapping's explicit format.

## Currency
Upper-case ISO 4217 from a bundled list. Symbol-only (`$`, `£`) is not accepted as currency unless the prompt produced an ISO code from explicit context.

## Duplicate key
`casefold(strip(supplier_name)) + "|" + re.sub(r"[^A-Z0-9]", "", invoice_number.upper()) + "|" + str(total.quantize(Decimal("0.01")))`. Any null part -> no duplicate check. The DB has a partial unique index on this key for invoices not `failed`; services re-check at persist time (race between concurrent uploads).

## Output contract
`Issue(code, severity, field, expected, got, message, location)`. `expected/got` are strings (`"100.00"`, `"90.00"`); message is human-readable, e.g. `Total 100.00 does not match subtotal + tax 90.00 + 5.00 = 95.00 (diff 5.00)`.

## Test table (each row = one unit test, minimal invoice)
Required missing x4 fields · optional missing is info only · bad number · negative amount ·
bad date · due before issue · date out of range · bad currency · ARITH_LINE pass/fail/edge 0.01 ·
ARITH_SUBTOTAL pass/fail · ARITH_TOTAL pass/fail/subtotal-null path · lines missing · grounding amount/number/date/name for parser vs ocr vs vision ·
duplicate in store / in file / null part skips · escalation set contents · sheet never escalates.
