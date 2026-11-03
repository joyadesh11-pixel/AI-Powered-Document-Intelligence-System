"""validate node — run pure validation rules against extracted invoices.

No LLM, no DB. Calls pipeline/validation/ rules which are pure Python.
make_validate(deps) is a factory — deps.seen_store is closed over so State
stays JSON-serialisable (Fix 6).
"""
from __future__ import annotations

from typing import Any

from app.pipeline.state import Deps, State, node
from app.pipeline.validation import ESCALATION_CODES, ValidationCtx, run_all
from app.schemas.models import Invoice, InvoiceStatus, Severity


def make_validate(deps: Deps | None = None):
    """Return a compiled validate node with seen_store closed over."""
    seen_store = (deps or {}).get("seen_store")

    @node("validate")
    def validate(s: State) -> dict:
        return _run_validate(s, seen_store=seen_store)

    return validate


def _run_validate(s: State, *, seen_store: Any) -> dict:
    invoices_in = s.get("invoices") or []
    page_text: dict[int, str] = s.get("page_text") or {}
    file_type: str = s.get("file_type") or "pdf"
    file_hash: str = s.get("file_hash") or ""
    escalated: bool = s.get("escalated") or False
    vision_queue: list[int] = list(s.get("vision_queue") or [])

    seen_in_file: dict = {}
    new_invoices: list[dict] = []
    new_val_errors: list[dict] = []
    new_vision_queue: list[int] = list(vision_queue)

    for inv_dict in invoices_in:
        invoice = Invoice.model_validate(inv_dict)

        source_text = " ".join(page_text.get(p, "") for p in invoice.source_pages)

        ctx = ValidationCtx(
            tier=invoice.tier.value,
            file_type=file_type,
            file_hash=file_hash,
            source_text=source_text,
            seen_store=seen_store,
            seen_in_file=seen_in_file,
            escalated=escalated,
        )

        issues = run_all(invoice, ctx)

        has_error = any(iss.severity == Severity.error for iss in issues)
        status = InvoiceStatus.needs_review.value if has_error else InvoiceStatus.validated_ok.value

        should_escalate = (
            has_error
            and any(
                iss.severity == Severity.error and iss.code in ESCALATION_CODES
                for iss in issues
            )
            and invoice.tier.value in ("parser", "ocr")
            and file_type != "sheet"
            and not escalated
            and invoice.unit_id not in new_vision_queue
        )
        if should_escalate and invoice.unit_id is not None:
            new_vision_queue.append(invoice.unit_id)

        updated = dict(inv_dict)
        updated["status"] = status
        updated["issues"] = [iss.model_dump() for iss in issues]
        new_invoices.append(updated)

        for iss in issues:
            new_val_errors.append({
                "unit_id": invoice.unit_id,
                "tier": invoice.tier.value,
                **iss.model_dump(),
            })

    return {
        "invoices": new_invoices,
        "validation_errors": new_val_errors,
        "vision_queue": new_vision_queue,
    }
