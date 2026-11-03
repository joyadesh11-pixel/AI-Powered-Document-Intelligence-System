"""llm_extract node — per-unit structured extraction via LLM.

Real implementation for M6. Uses make_llm_extract(deps) factory.
Tier.sheet is used for sheet units; Tier.parser for digital, Tier.ocr otherwise.
LLMConfigError propagates (document fails). Other LLM errors → EXTRACTION_FAILED,
unit added to vision_queue.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

from app.pipeline.config import get_config
from app.pipeline.llm.client import LLMConfigError, Usage
from app.pipeline.llm.prompts import get_extract_system
from app.pipeline.state import Deps, State, node
from app.pipeline.utils.dates import parse_date
from app.pipeline.utils.money import parse_amount
from app.schemas.models import (
    ExtractedInvoice, IssueCode, Severity, Tier,
)


def _tier(unit_source: str) -> str:
    if unit_source == "sheet":
        return Tier.sheet.value
    if unit_source == "digital":
        return Tier.parser.value
    return Tier.ocr.value


def _build_context(unit: dict, page_text: dict[int, str], page_source: dict[int, str],
                   tables: dict[int, list]) -> str:
    parts = []
    for p in unit.get("pages", []):
        src = page_source.get(p, "unknown")
        text = page_text.get(p, "")
        page_tables = tables.get(p, [])
        chunk = f"=== PAGE {p + 1} ({src}) ===\n{text}"
        if page_tables:
            chunk += "\n" + "\n".join(str(t) for t in page_tables)
        parts.append(chunk)
    return "\n\n".join(parts)


def _normalize(raw: ExtractedInvoice, unit: dict, page_source: dict[int, str]) -> dict:
    """Convert raw LLM output to a normalised invoice dict (no issues; validate handles them)."""
    def _money(val: str | None):
        if val is None:
            return None
        return parse_amount(val)

    def _date(val: str | None):
        if val is None:
            return None
        try:
            return parse_date(val).value
        except (ValueError, Exception):
            return None

    line_items = []
    for i, li in enumerate(raw.line_items):
        line_items.append({
            "position": i + 1,
            "description": li.description,
            "quantity": _money(li.quantity),
            "unit_price": _money(li.unit_price),
            "amount": _money(li.amount),
        })

    return {
        "unit_id": unit["id"],
        "tier": _tier(unit.get("source", "digital")),
        "invoice_number": raw.invoice_number,
        "invoice_date": _date(raw.invoice_date),
        "due_date": _date(raw.due_date),
        "supplier_name": raw.supplier_name,
        "customer_name": raw.customer_name,
        "subtotal": _money(raw.subtotal),
        "tax": _money(raw.tax),
        "total": _money(raw.total),
        "currency": raw.currency,
        "line_items": line_items,
        "source_pages": unit.get("pages", []),
        "uncertain_fields": raw.uncertain_fields,
        "raw": {
            "invoice_number": raw.invoice_number,
            "invoice_date": raw.invoice_date,
            "due_date": raw.due_date,
            "subtotal": raw.subtotal,
            "tax": raw.tax,
            "total": raw.total,
        },
    }


def _extraction_failed_issue(unit_id: int, msg: str) -> dict:
    return {
        "unit_id": unit_id,
        "code": IssueCode.EXTRACTION_FAILED.value,
        "severity": Severity.error.value,
        "message": msg,
        "field": None, "expected": None, "got": None, "location": {},
    }


def make_llm_extract(deps: Deps | None = None):
    """Return a compiled llm_extract node with deps closed over."""
    _deps = deps or {}
    llm = _deps.get("llm")
    cfg = _deps.get("config") or get_config()

    if llm is None:
        from app.pipeline.graph import NullLLM  # lazy import avoids circular
        llm = NullLLM()

    system = get_extract_system()

    @node("llm_extract")
    def llm_extract(s: State) -> dict:
        page_text: dict[int, str] = s.get("page_text") or {}
        page_source: dict[int, str] = s.get("page_source") or {}
        tables: dict[int, list] = s.get("tables") or {}
        units: list[dict] = s.get("units") or []
        vision_queue: list[int] = list(s.get("vision_queue") or [])
        existing_invoices: list[dict] = list(s.get("invoices") or [])
        warnings: list[dict] = list(s.get("warnings") or [])
        prior_usage: dict = dict(s.get("llm_usage") or {})

        # Units to process (skip those already in vision_queue or needs_vision)
        queue_set = set(vision_queue)
        to_extract: list[dict] = []
        for u in units:
            if u.get("needs_vision") or u["id"] in queue_set:
                queue_set.add(u["id"])
            else:
                to_extract.append(u)

        # Submit units concurrently, preserving order via dict
        new_invoices: list[dict] = []
        new_vision_queue: list[int] = sorted(queue_set)
        extra_val_errors: list[dict] = []
        total_usage = Usage()

        def _extract_unit(unit: dict) -> tuple[dict | None, Usage, dict | None]:
            """Return (invoice, usage, error_issue) — one of invoice or error_issue is None."""
            context = _build_context(unit, page_text, page_source, tables)

            if len(context) > cfg.MAX_CONTEXT_CHARS:
                context = context[: cfg.MAX_CONTEXT_CHARS]
                truncation_warn = {
                    "code": IssueCode.CONTEXT_TRUNCATED.value,
                    "severity": Severity.error.value,
                    "message": f"unit {unit['id']}: context truncated at {cfg.MAX_CONTEXT_CHARS} chars",
                    "field": None, "expected": None, "got": None, "location": {},
                }
                # returned via side channel; we'll collect via the return value
                return None, Usage(), {"type": "truncation_warn", "data": truncation_warn}

            try:
                raw, usage = llm.extract(
                    ExtractedInvoice,
                    system,
                    context,
                    model=cfg.LLM_MODEL_TEXT,
                )
                inv = _normalize(raw, unit, page_source)
                return inv, usage, None
            except LLMConfigError:
                raise  # propagate immediately
            except Exception as exc:  # noqa: BLE001
                err_issue = _extraction_failed_issue(
                    unit["id"], f"unit {unit['id']}: {exc}"
                )
                return None, Usage(), {"type": "error", "data": err_issue, "uid": unit["id"]}

        if to_extract:
            futures: dict[Any, dict] = {}
            with ThreadPoolExecutor(max_workers=cfg.LLM_CONCURRENCY) as pool:
                for u in to_extract:
                    futures[pool.submit(_extract_unit, u)] = u

                # Collect results preserving input order
                id_to_result: dict[int, tuple] = {}
                for fut, u in futures.items():
                    try:
                        inv, usage, side = fut.result()
                        id_to_result[u["id"]] = (inv, usage, side)
                    except LLMConfigError:
                        raise  # bubble up immediately

            for u in to_extract:
                uid = u["id"]
                inv, usage, side = id_to_result[uid]
                total_usage = total_usage + usage

                if side is not None:
                    if side["type"] == "truncation_warn":
                        warnings.append(side["data"])
                    elif side["type"] == "error":
                        extra_val_errors.append(side["data"])
                        new_vision_queue.append(uid)
                    continue

                if inv is not None:
                    new_invoices.append(inv)

        # Merge warnings for uncertain fields
        for inv in new_invoices:
            for uf in inv.pop("uncertain_fields", []):
                warnings.append({
                    "code": IssueCode.UNCERTAIN_FIELD.value,
                    "severity": Severity.warning.value,
                    "message": f"uncertain field in unit {inv['unit_id']}: {uf}",
                    "field": uf, "expected": None, "got": None, "location": {},
                })

        # Merge llm_usage
        merged_usage = {
            "calls": prior_usage.get("calls", 0) + total_usage.calls,
            "input_tokens": prior_usage.get("input_tokens", 0) + total_usage.input_tokens,
            "output_tokens": prior_usage.get("output_tokens", 0) + total_usage.output_tokens,
        }

        return {
            "invoices": existing_invoices + new_invoices,
            "vision_queue": sorted(set(new_vision_queue)),
            "warnings": warnings,
            "validation_errors": extra_val_errors,
            "llm_usage": merged_usage,
        }

    return llm_extract
