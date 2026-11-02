"""sheet_map node — map column names to invoice fields.

M4: Full implementation. Factory function make_sheet_map(deps) returns a @node.
"""
from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from app.pipeline.config import get_config
from app.pipeline.llm.prompts import SHEET_MAP_PROMPT_VERSION, SYSTEM_SHEET_MAP_V1
from app.pipeline.state import Deps, State, node
from app.pipeline.utils.synonyms import SYNONYMS, _normalise
from app.schemas.models import SheetMapping


# ------------------------------------------------------------------ helpers

_INVOICE_FIELDS = frozenset(
    ["invoice_number", "invoice_date", "due_date", "supplier_name", "customer_name",
     "subtotal", "tax", "total", "currency"]
)
_LINE_FIELDS = frozenset(["description", "quantity", "unit_price", "amount"])

_DATE_CANDIDATES = ["%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y", "%d-%m-%Y", "%m-%d-%Y",
                    "%Y/%m/%d", "%d.%m.%Y", "%m.%d.%Y"]
_MAX_DATE_SAMPLES = 200


def _try_fmt(s: str, fmt: str) -> bool:
    try:
        datetime.strptime(s.strip(), fmt)
        return True
    except (ValueError, AttributeError):
        return False


def _detect_date_format(samples: list[Any]) -> str | None:
    """Pick date_format if one candidate parses 100% of non-null samples unambiguously."""
    non_null = [str(v) for v in samples if v is not None and str(v).strip()][:_MAX_DATE_SAMPLES]
    if not non_null:
        return None
    passing = []
    for fmt in _DATE_CANDIDATES:
        if all(_try_fmt(s, fmt) for s in non_null):
            passing.append(fmt)
    if len(passing) == 1:
        return passing[0]
    # Ambiguous: both %d/%m/%Y and %m/%d/%Y match → all days ≤ 12 → return None
    if len(passing) > 1:
        return None
    return None


def _is_date_like_col(samples: list[Any]) -> bool:
    """True if ≥ 80% of non-null samples parse with any candidate or ISO."""
    non_null = [str(v) for v in samples if v is not None and str(v).strip()][:_MAX_DATE_SAMPLES]
    if not non_null:
        return False
    threshold = int(len(non_null) * 0.8)
    for fmt in _DATE_CANDIDATES:
        count = sum(1 for s in non_null if _try_fmt(s, fmt))
        if count >= threshold:
            return True
    return False


def _detect_grain(df_preview: list[dict], inv_num_col: str | None) -> str:
    """line_item if invoice_number column has duplicates, else invoice."""
    if inv_num_col is None:
        return "line_item"
    vals = [r.get(inv_num_col) for r in df_preview if r.get(inv_num_col) is not None]
    if not vals:
        return "line_item"
    return "line_item" if len(vals) > len(set(vals)) else "invoice"


def _detect_separators(samples: list[Any]) -> tuple[str, str]:
    """Vote on decimal and thousands separators from numeric string samples."""
    num_re = re.compile(r"^[-+]?\d[\d.,\s']*\d$")
    numeric = [str(v) for v in samples if v is not None and num_re.match(str(v).strip())]
    if not numeric:
        return ".", ","
    # Count formats
    both = sum(1 for s in numeric if "." in s and "," in s)
    comma_only = sum(1 for s in numeric if "," in s and "." not in s)
    dot_only = sum(1 for s in numeric if "." in s and "," not in s)
    if both > 0:
        # last separator determines decimal
        dot_last = sum(1 for s in numeric if "." in s and "," in s and s.rindex(".") > s.rindex(","))
        comma_last = sum(1 for s in numeric if "." in s and "," in s and s.rindex(",") > s.rindex("."))
        if comma_last > dot_last:
            return ",", "."
        return ".", ","
    if comma_only > dot_only:
        # comma is decimal: "1234,56"
        # Check if only 3 digits after comma (could be thousands)
        for s in numeric[:5]:
            after = s.split(",")[1] if "," in s else ""
            if len(after) == 3:
                return ".", ","  # comma = thousands
        return ",", "."
    return ".", ","


def _deterministic_pass(columns: list[str], preview: dict) -> dict:
    """Resolve as many fields as possible without LLM."""
    resolved_inv: dict[str, str] = {}
    resolved_line: dict[str, str] = {}
    ambiguous_targets: set[str] = set()

    for col in columns:
        norm = _normalise(col)
        target = SYNONYMS.get(norm)
        if target is None:
            continue
        if target in _INVOICE_FIELDS:
            if target not in ambiguous_targets:
                if target in resolved_inv:
                    ambiguous_targets.add(target)
                    del resolved_inv[target]
                else:
                    resolved_inv[target] = col
        elif target in _LINE_FIELDS:
            if target not in ambiguous_targets:
                if target in resolved_line:
                    ambiguous_targets.add(target)
                    # Prefer "line total" / "line amount" over "amount"
                    existing_col = resolved_line[target]
                    existing_norm = _normalise(existing_col)
                    new_norm = _normalise(col)
                    if new_norm in ("line total", "line amount", "extended price"):
                        resolved_line[target] = col
                    elif existing_norm not in ("line total", "line amount", "extended price"):
                        del resolved_line[target]
                    # else keep existing
                else:
                    resolved_line[target] = col

    # Date format detection from invoice_date column
    date_format: str | None = None
    inv_date_col = resolved_inv.get("invoice_date")
    if inv_date_col:
        preview_rows = preview.get("rows", [])
        samples = [r.get(inv_date_col) for r in preview_rows]
        date_format = _detect_date_format(samples)

    # Separator detection from amount column
    amt_col = resolved_line.get("amount") or resolved_inv.get("subtotal") or resolved_inv.get("total")
    dec_sep, thou_sep = ".", ","
    if amt_col:
        preview_rows = preview.get("rows", [])
        samples = [r.get(amt_col) for r in preview_rows]
        dec_sep, thou_sep = _detect_separators(samples)

    # Grain detection
    preview_rows = preview.get("rows", [])
    inv_num_col = resolved_inv.get("invoice_number")
    grain = _detect_grain(preview_rows, inv_num_col)

    # group_by: invoice_number column (original name before any rename)
    group_by = inv_num_col if grain == "line_item" else None

    # Check for unmapped date-like columns (unresolved)
    unmapped_date_like = False
    mapped_cols = set(resolved_inv.values()) | set(resolved_line.values())
    for col in columns:
        if col in mapped_cols:
            continue
        preview_rows = preview.get("rows", [])
        samples = [r.get(col) for r in preview_rows]
        if _is_date_like_col(samples):
            unmapped_date_like = True
            break

    return {
        "invoice_fields": resolved_inv,
        "line_fields": resolved_line,
        "date_format": date_format,
        "decimal_separator": dec_sep,
        "thousands_separator": thou_sep,
        "grain": grain,
        "group_by": group_by,
        "_unmapped_date_like": unmapped_date_like,
    }


def _is_complete(resolved: dict, columns: list[str]) -> bool:
    """True if deterministic pass resolved enough to skip LLM."""
    inv_fields = resolved.get("invoice_fields", {})
    line_fields = resolved.get("line_fields", {})
    has_inv_num = "invoice_number" in inv_fields
    has_amount = "amount" in line_fields or "subtotal" in inv_fields or "total" in inv_fields
    has_date_col = "invoice_date" in inv_fields or "due_date" in inv_fields
    has_date_fmt = resolved.get("date_format") is not None
    unmapped_date_like = resolved.get("_unmapped_date_like", False)
    # If there is a mapped date column but format is ambiguous → need LLM
    if has_date_col and not has_date_fmt:
        return False
    # If any column looks date-like but wasn't mapped → need LLM
    if unmapped_date_like:
        return False
    return has_inv_num and has_amount


def _bad_columns(mapping: SheetMapping, valid_cols: list[str]) -> list[str]:
    """Return list of column names in mapping that don't exist in valid_cols."""
    valid_set = set(valid_cols)
    bad = []
    for col in mapping.invoice_fields.values():
        if col and col not in valid_set:
            bad.append(col)
    for col in mapping.line_fields.values():
        if col and col not in valid_set:
            bad.append(col)
    if mapping.group_by and mapping.group_by not in valid_set:
        bad.append(mapping.group_by)
    if mapping.currency_column and mapping.currency_column not in valid_set:
        bad.append(mapping.currency_column)
    return list(set(bad))


def _merge(llm_result: SheetMapping, resolved: dict) -> SheetMapping:
    """Merge deterministic results onto LLM output. Deterministic wins on first pass."""
    data = llm_result.model_dump()
    # Override with deterministic results
    inv_f = resolved.get("invoice_fields", {})
    line_f = resolved.get("line_fields", {})
    if inv_f:
        data["invoice_fields"] = {**data.get("invoice_fields", {}), **inv_f}
    if line_f:
        data["line_fields"] = {**data.get("line_fields", {}), **line_f}
    if resolved.get("date_format") is not None:
        data["date_format"] = resolved["date_format"]
    if resolved.get("decimal_separator"):
        data["decimal_separator"] = resolved["decimal_separator"]
    if resolved.get("thousands_separator") is not None:
        data["thousands_separator"] = resolved["thousands_separator"]
    if resolved.get("grain"):
        data["grain"] = resolved["grain"]
    if resolved.get("group_by") is not None:
        data["group_by"] = resolved["group_by"]
    return SheetMapping.model_validate(data)


def _build_prompt(sheet_meta: dict, resolved: dict, feedback: str | None) -> str:
    cols = sheet_meta.get("columns", [])
    preview = sheet_meta.get("preview", {})
    preview_rows = preview.get("rows", [])[:5]

    lines = [
        f"Sheet columns: {cols}",
        f"Sample rows (first 5):",
    ]
    for row in preview_rows:
        lines.append(f"  {row}")

    if resolved and not feedback:
        already = {k: v for k, v in {
            **resolved.get("invoice_fields", {}),
            **resolved.get("line_fields", {}),
        }.items()}
        if already:
            lines.append(f"\nAlready resolved: {already}")
            lines.append("Fill only the remaining fields.")

    if feedback:
        lines.append(f"\nPrevious mapping failed to parse:\n{feedback}")
        lines.append("Provide a corrected mapping.")

    return "\n".join(lines)


def make_sheet_map(deps: Deps):
    """Factory: returns a @node-wrapped sheet_map node closed over deps."""
    cfg = (deps or {}).get("config") or get_config()
    llm = (deps or {}).get("llm")
    cache = (deps or {}).get("mapping_cache")

    @node("sheet_map")
    def sheet_map(s: State) -> dict:
        sheets = s.get("sheets", [])
        primary_name = s.get("primary_sheet")
        if not sheets or not primary_name:
            return {"mapping": None}

        sheet_meta = next(
            (sh for sh in sheets if sh["name"] == primary_name), sheets[0]
        )
        columns = sheet_meta["columns"]
        header_hash = sheet_meta["header_hash"]
        feedback = s.get("mapping_feedback")

        # 1. Cache check (read-only; pipeline never writes it)
        if cache and not feedback:
            cached = cache(header_hash)
            if cached is not None:
                cached_dict = cached.model_dump() if hasattr(cached, "model_dump") else cached
                return {"mapping": cached_dict}

        # 2. Deterministic pass (skip on feedback retry)
        resolved = {} if feedback else _deterministic_pass(columns, sheet_meta.get("preview", {}))

        if not feedback and _is_complete(resolved, columns):
            m = SheetMapping(
                grain=resolved.get("grain", "line_item"),
                group_by=resolved.get("group_by"),
                invoice_fields=resolved.get("invoice_fields", {}),
                line_fields=resolved.get("line_fields", {}),
                date_format=resolved.get("date_format"),
                decimal_separator=resolved.get("decimal_separator", "."),
                thousands_separator=resolved.get("thousands_separator", ","),
            )
            return {"mapping": m.model_dump(), "llm_usage": {}}

        # 3. LLM call with column-validation retry
        user_prompt = _build_prompt(sheet_meta, resolved if not feedback else {}, feedback)
        existing_usage = s.get("llm_usage") or {}
        total_calls = existing_usage.get("calls", 0)
        total_in = existing_usage.get("input_tokens", 0)
        total_out = existing_usage.get("output_tokens", 0)

        from app.pipeline.llm.client import Usage
        total_u = Usage(total_calls, total_in, total_out)

        for attempt in range(cfg.LLM_MAX_SCHEMA_RETRIES + 1):
            raw, u = llm.extract(
                SheetMapping, SYSTEM_SHEET_MAP_V1, user_prompt, model=cfg.LLM_MODEL_TEXT
            )
            total_u = total_u + u

            # Merge deterministic on first pass; feedback retry is LLM-authoritative
            if feedback:
                merged = raw
            else:
                merged = _merge(raw, resolved)

            bad = _bad_columns(merged, columns)
            if not bad:
                return {
                    "mapping": merged.model_dump(),
                    "llm_usage": {
                        "calls": total_u.calls,
                        "input_tokens": total_u.input_tokens,
                        "output_tokens": total_u.output_tokens,
                    },
                }
            user_prompt = (
                user_prompt
                + f"\n\nCORRECTION: columns {bad} do not exist in this sheet. "
                f"Use only: {columns}"
            )

        # 4. Exhausted
        return {
            "mapping": None,
            "mapping_retries": cfg.MAPPING_MAX_RETRIES + 1,
            "llm_usage": {
                "calls": total_u.calls,
                "input_tokens": total_u.input_tokens,
                "output_tokens": total_u.output_tokens,
            },
        }

    return sheet_map
