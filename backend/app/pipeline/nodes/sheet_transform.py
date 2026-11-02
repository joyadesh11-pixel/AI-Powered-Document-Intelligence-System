"""sheet_transform node — turn a SheetMapping + frames into Invoice objects.

M4: Full implementation.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

import pandas as pd

from app.pipeline.config import get_config
from app.pipeline.state import State, node
from app.pipeline.utils.money import parse_amount
from app.schemas.models import (
    Invoice,
    IssueCode,
    LineItem,
    Severity,
    SheetMapping,
    Tier,
)

_WARNING_KWARGS = {"field": None, "expected": None, "got": None, "location": {}}

_INVOICE_SCHEMA_FIELDS = frozenset(
    ["invoice_number", "invoice_date", "due_date", "supplier_name", "customer_name",
     "subtotal", "tax", "total", "currency"]
)
_LINE_SCHEMA_FIELDS = frozenset(["description", "quantity", "unit_price", "amount"])
_MONEY_FIELDS = frozenset(["subtotal", "tax", "total", "quantity", "unit_price", "amount"])
_DATE_FIELDS = frozenset(["invoice_date", "due_date"])


def _parse_decimal(v: Any, dec_sep: str, thou_sep: str) -> Decimal | None:
    if v is None:
        return None
    s = str(v).strip()
    if not s:
        return None
    return parse_amount(s, decimal_sep=dec_sep, thousands_sep=thou_sep)


def _parse_date(v: Any, fmt: str | None) -> date | None:
    if v is None:
        return None
    s = str(v).strip()
    if not s:
        return None
    if fmt:
        try:
            return pd.to_datetime(s, format=fmt, errors="coerce").date()
        except Exception:
            return None
    # Try ISO fallback
    try:
        return pd.to_datetime(s, errors="coerce").date()
    except Exception:
        return None


def _parse_date_series(series: pd.Series, fmt: str | None) -> pd.Series:
    """Parse a Series of date strings; return Series of date | None."""
    if fmt:
        parsed = pd.to_datetime(series, format=fmt, errors="coerce")
    else:
        parsed = pd.to_datetime(series, errors="coerce")
    return parsed.apply(lambda x: x.date() if pd.notna(x) else None)


@node("sheet_transform")
def sheet_transform(s: State) -> dict:
    mapping_raw = s.get("mapping")
    if mapping_raw is None:
        return {
            "parse_ok": False,
            "mapping_retries": s.get("mapping_retries", 0),
        }

    mapping = SheetMapping.model_validate(mapping_raw)
    cfg = get_config()
    frames = s.get("frames", {})
    primary_name = s.get("primary_sheet")
    warnings = list(s.get("warnings") or [])

    if not primary_name or primary_name not in frames:
        return {"parse_ok": False, "mapping_retries": s.get("mapping_retries", 0)}

    df = frames[primary_name].copy()

    # Drop fully-empty rows; blank cells → None
    df = df.dropna(how="all")
    df = df.replace("", None)

    # ---- 1. Build rename map (original col → schema dest) ----
    rename_map: dict[str, str] = {}
    for dest, src in mapping.invoice_fields.items():
        if src and src in df.columns:
            rename_map[src] = dest
    for dest, src in mapping.line_fields.items():
        if src and src in df.columns:
            rename_map[src] = dest

    # Record extra columns BEFORE rename (cols not in mapping)
    mapped_src_cols = set(mapping.invoice_fields.values()) | set(mapping.line_fields.values())
    extra_src_cols = [c for c in df.columns if c not in mapped_src_cols and c not in ("_row", "_sheet")]

    df = df.rename(columns=rename_map)

    # ---- 2. Determine group-by column (after rename) ----
    if mapping.grain == "line_item":
        # group_by is an original source col name (before rename)
        if mapping.group_by:
            group_by_col = rename_map.get(mapping.group_by, mapping.group_by)
        else:
            group_by_col = "invoice_number" if "invoice_number" in df.columns else None
    else:
        group_by_col = "invoice_number" if "invoice_number" in df.columns else None

    # ---- 3. Identify ungrouped rows ----
    ungrouped_indices: list[int] = []
    if group_by_col and group_by_col in df.columns:
        ungrouped_mask = df[group_by_col].isna() | (
            df[group_by_col].astype(str).str.strip() == ""
        )
        ungrouped_df = df[ungrouped_mask]
        data_df = df[~ungrouped_mask]
        ungrouped_indices = list(ungrouped_df["_row"].dropna().astype(int)) if "_row" in ungrouped_df.columns else []
    else:
        data_df = df
        ungrouped_df = df.iloc[:0]

    if not ungrouped_df.empty:
        warnings.append({
            "code": IssueCode.UNGROUPED_ROWS.value,
            "severity": Severity.warning.value,
            "message": (
                f"{len(ungrouped_df)} row(s) with no invoice number (ungrouped); "
                f"first rows: {ungrouped_indices[:5]}"
            ),
            **_WARNING_KWARGS,
        })

    # ---- 4. Parse dates and amounts on data_df; check parse rate ----
    dec_sep = mapping.decimal_separator or "."
    thou_sep = mapping.thousands_separator or ","
    date_fmt = mapping.date_format

    parse_errors: list[str] = []

    for field_name in _DATE_FIELDS:
        if field_name not in data_df.columns:
            continue
        col = data_df[field_name]
        raw_non_null = col.notna() & (col.astype(str).str.strip() != "")
        raw_count = raw_non_null.sum()
        if raw_count == 0:
            continue
        parsed = _parse_date_series(col, date_fmt)
        parsed_count = parsed.notna().sum()
        rate = parsed_count / raw_count
        if rate < cfg.PARSE_RATE_MIN:
            failing = col[raw_non_null & parsed.isna()].dropna().head(3).tolist()
            feedback = (
                f"column '{field_name}': {rate:.0%} parsed with format "
                f"'{date_fmt}'; failing examples: {failing}"
            )
            parse_errors.append(feedback)
        else:
            data_df = data_df.copy()
            data_df[field_name] = parsed

    for field_name in _MONEY_FIELDS:
        if field_name not in data_df.columns:
            continue
        col = data_df[field_name]
        raw_non_null = col.notna() & (col.astype(str).str.strip() != "")
        raw_count = raw_non_null.sum()
        if raw_count == 0:
            continue
        parsed = col.apply(
            lambda v: _parse_decimal(v, dec_sep, thou_sep) if (v is not None and str(v).strip()) else None
        )
        parsed_count = parsed.notna().sum()
        rate = parsed_count / raw_count
        if rate < cfg.PARSE_RATE_MIN:
            failing = col[raw_non_null & parsed.isna()].dropna().head(3).tolist()
            feedback = (
                f"column '{field_name}': {rate:.0%} parsed as amount; "
                f"failing examples: {failing}"
            )
            parse_errors.append(feedback)
        else:
            data_df = data_df.copy()
            data_df[field_name] = parsed

    if parse_errors:
        retries = s.get("mapping_retries", 0)
        return {
            "parse_ok": False,
            "mapping_feedback": "; ".join(parse_errors),
            "mapping_retries": retries + 1,
            "warnings": warnings,
        }

    # ---- 5. Group and build Invoice objects ----
    invoices: list[dict] = []
    inconsistency_seen: set[str] = set()

    if mapping.grain == "invoice":
        # Each row is one invoice
        for _, row in data_df.iterrows():
            inv = _build_invoice_from_row(
                row, mapping, extra_src_cols, dec_sep, thou_sep, date_fmt,
                warnings, inconsistency_seen
            )
            invoices.append(inv)
    else:
        # Group by invoice_number (line_item grain)
        if group_by_col and group_by_col in data_df.columns:
            groups = data_df.groupby(group_by_col, sort=False, dropna=True)
            for inv_key, grp in groups:
                inv = _build_invoice_from_group(
                    str(inv_key), grp, mapping, group_by_col,
                    extra_src_cols, dec_sep, thou_sep, date_fmt,
                    warnings, inconsistency_seen
                )
                invoices.append(inv)
        else:
            # No group_by — treat all rows as lines of one invoice
            inv = _build_invoice_from_group(
                None, data_df, mapping, None,
                extra_src_cols, dec_sep, thou_sep, date_fmt,
                warnings, inconsistency_seen
            )
            invoices.append(inv)

    return {
        "invoices": invoices,
        "parse_ok": True,
        "mapping_feedback": None,
        "warnings": warnings,
    }


# ------------------------------------------------------------------ builders

def _first_non_null(series: pd.Series) -> Any:
    for v in series:
        if v is not None and (not isinstance(v, float) or v == v):
            return v
    return None


def _build_source_rows(grp: pd.DataFrame) -> list[int]:
    if "_row" not in grp.columns:
        return []
    return sorted(int(v) for v in grp["_row"].dropna())


def _build_extra(row_or_grp: Any, extra_cols: list[str]) -> dict:
    """Build extra dict from unmapped columns."""
    extra: dict = {}
    if isinstance(row_or_grp, pd.Series):
        for col in extra_cols:
            v = row_or_grp.get(col)
            if v is not None and str(v).strip():
                key = f"unmapped_{col}" if col in _INVOICE_SCHEMA_FIELDS | _LINE_SCHEMA_FIELDS else col
                extra[key] = str(v)
    else:
        for col in extra_cols:
            if col in row_or_grp.columns:
                v = _first_non_null(row_or_grp[col])
                if v is not None and str(v).strip():
                    key = f"unmapped_{col}" if col in _INVOICE_SCHEMA_FIELDS | _LINE_SCHEMA_FIELDS else col
                    extra[key] = str(v)
    return extra


def _build_raw(row_or_grp: Any, mapping: SheetMapping) -> dict:
    """Collect original string values for mapped fields."""
    raw: dict = {}
    all_mapped = {**mapping.invoice_fields, **mapping.line_fields}
    if isinstance(row_or_grp, pd.Series):
        for dest, src in all_mapped.items():
            v = row_or_grp.get(dest)
            if v is not None:
                raw[src] = str(v)
    else:
        for dest in all_mapped:
            if dest in row_or_grp.columns:
                v = _first_non_null(row_or_grp[dest])
                if v is not None:
                    raw[dest] = str(v)
    return raw


def _money_from_group(
    grp: pd.DataFrame,
    field: str,
    dec_sep: str,
    thou_sep: str,
    warnings: list,
    inconsistency_seen: set,
) -> Decimal | None:
    """Extract a header money field from a group.
    If all non-null values equal → return that value.
    If they differ → SHEET_GROUP_INCONSISTENT warning, return None.
    """
    if field not in grp.columns:
        return None
    vals = [v for v in grp[field] if v is not None and (not isinstance(v, float) or v == v)]
    if not vals:
        return None
    # vals may already be Decimal from the parse step
    decimals: list[Decimal] = []
    for v in vals:
        if isinstance(v, Decimal):
            decimals.append(v)
        else:
            d = _parse_decimal(v, dec_sep, thou_sep)
            if d is not None:
                decimals.append(d)
    if not decimals:
        return None
    unique = set(decimals)
    if len(unique) == 1:
        return decimals[0]
    if field not in inconsistency_seen:
        inconsistency_seen.add(field)
        warnings.append({
            "code": IssueCode.SHEET_GROUP_INCONSISTENT.value,
            "severity": Severity.warning.value,
            "message": f"Header field '{field}' has inconsistent values within an invoice group",
            **_WARNING_KWARGS,
        })
    return None


def _build_invoice_from_row(
    row: pd.Series,
    mapping: SheetMapping,
    extra_cols: list[str],
    dec_sep: str,
    thou_sep: str,
    date_fmt: str | None,
    warnings: list,
    inconsistency_seen: set,
) -> dict:
    """Build an Invoice dict from a single row (grain=invoice)."""
    source_rows = []
    if "_row" in row.index and row["_row"] is not None:
        try:
            source_rows = [int(row["_row"])]
        except (ValueError, TypeError):
            pass

    def _get(field: str) -> Any:
        return row.get(field) if field in row.index else None

    inv_number = _get("invoice_number")
    inv_number = str(inv_number).strip() if inv_number is not None else None
    if inv_number == "":
        inv_number = None

    inv_date_raw = _get("invoice_date")
    inv_date = inv_date_raw if isinstance(inv_date_raw, date) else _parse_date(inv_date_raw, date_fmt)

    due_date_raw = _get("due_date")
    due_date = due_date_raw if isinstance(due_date_raw, date) else _parse_date(due_date_raw, date_fmt)

    subtotal = _get("subtotal")
    subtotal = subtotal if isinstance(subtotal, Decimal) else _parse_decimal(subtotal, dec_sep, thou_sep)
    tax = _get("tax")
    tax = tax if isinstance(tax, Decimal) else _parse_decimal(tax, dec_sep, thou_sep)
    total = _get("total")
    total = total if isinstance(total, Decimal) else _parse_decimal(total, dec_sep, thou_sep)

    inv = Invoice(
        invoice_number=inv_number,
        invoice_date=inv_date,
        due_date=due_date,
        supplier_name=str(_get("supplier_name")).strip() if _get("supplier_name") is not None else None,
        customer_name=str(_get("customer_name")).strip() if _get("customer_name") is not None else None,
        subtotal=subtotal,
        tax=tax,
        total=total,
        currency=str(_get("currency")).strip() if _get("currency") is not None else None,
        line_items=[],
        tier=Tier.sheet,
        source_rows=source_rows,
        extra=_build_extra(row, extra_cols),
        raw=_build_raw(row, mapping),
        derived={},
    )
    return inv.model_dump()


def _build_invoice_from_group(
    inv_key: str | None,
    grp: pd.DataFrame,
    mapping: SheetMapping,
    group_by_col: str | None,
    extra_cols: list[str],
    dec_sep: str,
    thou_sep: str,
    date_fmt: str | None,
    warnings: list,
    inconsistency_seen: set,
) -> dict:
    """Build an Invoice dict from a group of rows (grain=line_item)."""
    source_rows = _build_source_rows(grp)

    def _header(field: str) -> Any:
        if field not in grp.columns:
            return None
        return _first_non_null(grp[field])

    inv_number = inv_key
    if inv_number is not None:
        inv_number = str(inv_number).strip() or None

    inv_date_raw = _header("invoice_date")
    inv_date = inv_date_raw if isinstance(inv_date_raw, date) else _parse_date(inv_date_raw, date_fmt)

    due_date_raw = _header("due_date")
    due_date = due_date_raw if isinstance(due_date_raw, date) else _parse_date(due_date_raw, date_fmt)

    supplier = _header("supplier_name")
    customer = _header("customer_name")
    currency = _header("currency")

    subtotal = _money_from_group(grp, "subtotal", dec_sep, thou_sep, warnings, inconsistency_seen)
    tax = _money_from_group(grp, "tax", dec_sep, thou_sep, warnings, inconsistency_seen)
    total = _money_from_group(grp, "total", dec_sep, thou_sep, warnings, inconsistency_seen)

    # Build line items
    line_items: list[LineItem] = []
    for pos, (_, row) in enumerate(grp.iterrows(), start=1):
        amt_raw = row.get("amount") if "amount" in grp.columns else None
        amt = amt_raw if isinstance(amt_raw, Decimal) else _parse_decimal(amt_raw, dec_sep, thou_sep)
        qty_raw = row.get("quantity") if "quantity" in grp.columns else None
        qty = qty_raw if isinstance(qty_raw, Decimal) else _parse_decimal(qty_raw, dec_sep, thou_sep)
        up_raw = row.get("unit_price") if "unit_price" in grp.columns else None
        up = up_raw if isinstance(up_raw, Decimal) else _parse_decimal(up_raw, dec_sep, thou_sep)
        desc = row.get("description") if "description" in grp.columns else None
        desc = str(desc).strip() if desc is not None else None
        line_items.append(LineItem(position=pos, description=desc, quantity=qty, unit_price=up, amount=amt))

    # Derived lines_sum
    line_amounts = [li.amount for li in line_items if li.amount is not None]
    lines_sum = str(sum(line_amounts)) if line_amounts else None

    inv = Invoice(
        invoice_number=inv_number,
        invoice_date=inv_date,
        due_date=due_date,
        supplier_name=str(supplier).strip() if supplier is not None else None,
        customer_name=str(customer).strip() if customer is not None else None,
        subtotal=subtotal,
        tax=tax,
        total=total,
        currency=str(currency).strip() if currency is not None else None,
        line_items=line_items,
        tier=Tier.sheet,
        source_rows=source_rows,
        extra=_build_extra(grp, extra_cols),
        raw=_build_raw(grp, mapping),
        derived={"lines_sum": lines_sum} if lines_sum is not None else {},
    )
    return inv.model_dump()
