"""finalize node — compute per-invoice and document status, build PipelineResult.

Fix 5: per-invoice status; document status = worst of invoices
  (failed > needs_review > validated_ok). Zero invoices -> needs_review + NO_INVOICE_DETECTED.

Stores serialisable PipelineResult dict in state["result"]; process() validates it.
"""
from __future__ import annotations

from app.pipeline.state import State, node
from app.schemas.models import (
    DocStatus, InvoiceStatus, IssueCode, Severity,
    PipelineResult, Invoice, Issue, Tier,
)


_STATUS_RANK = {
    DocStatus.failed.value: 2,
    DocStatus.needs_review.value: 1,
    DocStatus.validated_ok.value: 0,
}


def _invoice_status(inv: dict, val_errors: list[dict]) -> str:
    # Prefer status written directly by the validate node; fall back to val_errors scan.
    direct = inv.get("status")
    if direct is not None:
        return direct
    unit_id = inv.get("unit_id")
    for e in val_errors:
        if e.get("unit_id") == unit_id and e.get("severity") == Severity.error.value:
            return InvoiceStatus.needs_review.value
    return InvoiceStatus.validated_ok.value


def _doc_status(invoice_statuses: list[str]) -> str:
    if not invoice_statuses:
        return DocStatus.needs_review.value
    worst = max(invoice_statuses, key=lambda s: _STATUS_RANK.get(s, 0))
    # map InvoiceStatus values to DocStatus
    return DocStatus.needs_review.value if worst == InvoiceStatus.needs_review.value else DocStatus.validated_ok.value


@node("finalize", skip_on_error=False)
def finalize(s: State) -> dict:
    if s.get("error"):
        return {}  # fail node handles the error path

    val_errors = s.get("validation_errors", [])
    raw_invoices = s.get("invoices", [])
    warnings = s.get("warnings", [])

    invoice_statuses: list[str] = []
    for inv in raw_invoices:
        ist = _invoice_status(inv, val_errors)
        invoice_statuses.append(ist)

    doc_status: str
    extra_warnings: list[dict] = []

    if not raw_invoices:
        doc_status = DocStatus.needs_review.value
        if s.get("parse_ok") is False:
            extra_warnings.append({
                "code": IssueCode.MAPPING_FAILED.value,
                "severity": Severity.error.value,
                "message": "Sheet column mapping exhausted — no invoices extracted",
                "field": None, "expected": None, "got": None, "location": {},
            })
        else:
            extra_warnings.append({
                "code": IssueCode.NO_INVOICE_DETECTED.value,
                "severity": Severity.warning.value,
                "message": "No invoice detected in document",
                "field": None, "expected": None, "got": None, "location": {},
            })
    else:
        doc_status = _doc_status(invoice_statuses)

    # Compute timings from history
    history = s.get("history", [])
    by_node: dict[str, float] = {}
    for h in history:
        name = h.get("node", "unknown")
        by_node[name] = round(by_node.get(name, 0.0) + h.get("seconds", 0.0), 5)
    total_secs = round(sum(by_node.values()), 5)
    timings = {"total": total_secs, "by_node": by_node}

    # sheet_mapping: only when a sheet doc produced a usable column mapping
    sheet_mapping = None
    header_hash = None
    if s.get("parse_ok") is True and s.get("mapping"):
        sheet_mapping = s.get("mapping")
        primary = s.get("primary_sheet")
        for sm in (s.get("sheets") or []):
            if isinstance(sm, dict) and sm.get("name") == primary:
                header_hash = sm.get("header_hash")
                break

    result = {
        "status": doc_status,
        "error_code": s.get("error_code"),
        "error": s.get("error"),
        "file_hash": s.get("file_hash", ""),
        "file_type": s.get("file_type", ""),
        "size_bytes": s.get("size_bytes", 0),
        "page_count": s.get("page_count", 0),
        "invoices": raw_invoices,
        "warnings": (warnings or []) + extra_warnings,
        "page_text": s.get("page_text", {}),
        "page_source": s.get("page_source", {}),
        "ocr_conf": s.get("ocr_conf", {}),
        "timings": timings,
        "history": history,
        "llm_usage": s.get("llm_usage", {}),
        "pipeline_version": "0.1.0-m8a",
        "sheet_mapping": sheet_mapping,
        "header_hash": header_hash,
    }

    return {"status": doc_status, "result": result}
