"""vision_extract node — re-extract queued units via vision LLM.

Real implementation for M6. Uses make_vision_extract(deps) factory.
Fixed arbitration: runs run_all on BOTH candidates with same ctx.
Never runs for sheets. Never runs twice (escalated guard).
"""
from __future__ import annotations

import io
from pathlib import Path

from app.pipeline.config import get_config
from app.pipeline.llm.client import LLMConfigError, Usage
from app.pipeline.llm.prompts import get_vision_system
from app.pipeline.state import Deps, State, node
from app.pipeline.utils.dates import parse_date
from app.pipeline.utils.money import parse_amount
from app.pipeline.validation import ESCALATION_CODES, ValidationCtx, run_all
from app.schemas.models import (
    IssueCode, Invoice, Severity, Tier, VisionExtraction,
)


def _money(val: str | None):
    if val is None:
        return None
    return parse_amount(val)


def _date(val: str | None):
    if val is None:
        return None
    try:
        return parse_date(val).value
    except Exception:  # noqa: BLE001
        return None


def _normalize_vision(raw: VisionExtraction, unit_id: int, pages: list[int]) -> dict:
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
        "unit_id": unit_id,
        "tier": Tier.vision.value,
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
        "source_pages": pages,
        "uncertain_fields": raw.uncertain_fields,
        "source_text": raw.source_text,
        "alternatives": [],
        "raw": {
            "invoice_number": raw.invoice_number,
            "invoice_date": raw.invoice_date,
            "due_date": raw.due_date,
            "subtotal": raw.subtotal,
            "tax": raw.tax,
            "total": raw.total,
        },
    }


def _render_page_jpeg(page_idx: int, s: State, cfg) -> bytes | None:
    """Return JPEG bytes for a page: use existing PNG if available, else render PDF."""
    page_images: dict[int, str] = s.get("page_images") or {}
    if page_idx in page_images:
        img_path = page_images[page_idx]
        try:
            from PIL import Image
            img = Image.open(img_path).convert("RGB")
            if max(img.size) > cfg.VISION_MAX_SIDE_PX:
                ratio = cfg.VISION_MAX_SIDE_PX / max(img.size)
                img = img.resize(
                    (int(img.size[0] * ratio), int(img.size[1] * ratio)),
                    Image.LANCZOS,
                )
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=85)
            return buf.getvalue()
        except Exception:  # noqa: BLE001
            return None

    # Render PDF page on demand
    file_path = s.get("file_path", "")
    if not file_path or not file_path.lower().endswith(".pdf"):
        return None
    try:
        import fitz  # PyMuPDF
        doc = fitz.open(file_path)
        if page_idx >= len(doc):
            return None
        page = doc[page_idx]
        mat = fitz.Matrix(cfg.VISION_RENDER_DPI / 72, cfg.VISION_RENDER_DPI / 72)
        pix = page.get_pixmap(matrix=mat)
        from PIL import Image
        img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
        if max(img.size) > cfg.VISION_MAX_SIDE_PX:
            ratio = cfg.VISION_MAX_SIDE_PX / max(img.size)
            img = img.resize(
                (int(img.size[0] * ratio), int(img.size[1] * ratio)),
                Image.LANCZOS,
            )
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=85)
        return buf.getvalue()
    except Exception:  # noqa: BLE001
        return None


def _count_errors(invoice_dict: dict) -> int:
    """Count error-severity issues in an invoice dict."""
    return sum(
        1 for iss in (invoice_dict.get("issues") or [])
        if (isinstance(iss, dict) and iss.get("severity") == Severity.error.value)
        or (hasattr(iss, "severity") and iss.severity == Severity.error)
    )


def _count_errors_via_run_all(invoice_dict: dict, s: State, cfg) -> int:
    """Run validation on a candidate invoice dict, return error count."""
    try:
        inv = Invoice.model_validate(invoice_dict)
        page_text: dict[int, str] = s.get("page_text") or {}
        source_text = " ".join(page_text.get(p, "") for p in inv.source_pages)
        ctx = ValidationCtx(
            tier=inv.tier.value,
            file_type=s.get("file_type") or "pdf",
            file_hash=s.get("file_hash") or "",
            source_text=source_text,
            seen_store=None,
            escalated=True,
        )
        issues = run_all(inv, ctx)
        return sum(1 for iss in issues if iss.severity == Severity.error)
    except Exception:  # noqa: BLE001
        return 0


def make_vision_extract(deps: Deps | None = None):
    """Return a compiled vision_extract node with deps closed over."""
    _deps = deps or {}
    llm = _deps.get("llm")
    cfg = _deps.get("config") or get_config()

    if llm is None:
        from app.pipeline.graph import NullLLM
        llm = NullLLM()

    system = get_vision_system()

    @node("vision_extract")
    def vision_extract(s: State) -> dict:
        file_type = s.get("file_type", "pdf")
        queue: list[int] = list(s.get("vision_queue") or [])
        escalated: bool = s.get("escalated") or False

        # Never runs for sheets; never runs twice
        if file_type == "sheet" or escalated or not queue:
            return {}

        invoices_in: list[dict] = list(s.get("invoices") or [])
        page_text: dict[int, str] = dict(s.get("page_text") or {})
        page_source: dict[int, str] = dict(s.get("page_source") or {})
        warnings: list[dict] = list(s.get("warnings") or [])
        val_errors: list[dict] = list(s.get("validation_errors") or [])
        prior_usage: dict = dict(s.get("llm_usage") or {})

        # Build lookup: unit_id → unit dict
        units_by_id: dict[int, dict] = {
            u["id"]: u for u in (s.get("units") or [])
        }

        kept_invoices: list[dict] = [i for i in invoices_in if i.get("unit_id") not in set(queue)]
        new_invoices: list[dict] = []
        total_usage = Usage()

        for uid in sorted(queue):
            unit = units_by_id.get(uid, {"id": uid, "pages": [uid]})
            pages = unit.get("pages", [uid])

            # Cap pages
            if len(pages) > cfg.VISION_MAX_PAGES:
                warnings.append({
                    "code": IssueCode.VISION_PAGES_CAPPED.value,
                    "severity": Severity.warning.value,
                    "message": (
                        f"unit {uid}: capped to {cfg.VISION_MAX_PAGES} pages "
                        f"(had {len(pages)})"
                    ),
                    "field": None, "expected": None, "got": None, "location": {},
                })
                pages = pages[: cfg.VISION_MAX_PAGES]

            # Render pages to JPEG
            jpeg_list = []
            for p in pages:
                img = _render_page_jpeg(p, s, cfg)
                if img is not None:
                    jpeg_list.append(img)

            user_text = f"Extract invoice from the following document (unit {uid})."

            try:
                raw, usage = llm.extract(
                    VisionExtraction,
                    system,
                    user_text,
                    images=jpeg_list if jpeg_list else None,
                    model=cfg.LLM_MODEL_VISION,
                )
                total_usage = total_usage + usage
                vision_inv = _normalize_vision(raw, uid, pages)
            except LLMConfigError:
                raise
            except Exception as exc:  # noqa: BLE001
                warnings.append({
                    "code": IssueCode.VISION_FAILED.value,
                    "severity": Severity.error.value,
                    "message": f"unit {uid}: vision extraction failed: {exc}",
                    "field": None, "expected": None, "got": None, "location": {},
                })
                # Keep text-tier invoice if present
                text_inv = next(
                    (i for i in invoices_in if i.get("unit_id") == uid), None
                )
                if text_inv:
                    new_invoices.append(text_inv)
                continue

            # Update page_text with source_text if available (for low-conf pages)
            if raw.source_text:
                for p in pages:
                    if p in (unit.get("low_conf_pages") or []) or page_source.get(p) in ("ocr",):
                        page_text[p] = raw.source_text
                        page_source[p] = "vision"

            # Arbitration: compare against text-tier candidate (if any)
            text_inv = next(
                (i for i in invoices_in if i.get("unit_id") == uid), None
            )

            if text_inv is not None:
                # Re-run validation on BOTH with same ctx (fixed arbitration)
                text_errors = _count_errors_via_run_all(text_inv, s, cfg)
                vision_errors = _count_errors_via_run_all(vision_inv, s, cfg)

                if text_errors < vision_errors:
                    # Text wins; store vision as alternative
                    winner = dict(text_inv)
                    winner.setdefault("alternatives", []).append({
                        "tier": "vision",
                        "reason": "arbitration: text had fewer errors",
                        "invoice": vision_inv,
                    })
                    new_invoices.append(winner)
                else:
                    # Vision wins (tie or fewer errors)
                    vision_inv["alternatives"].append({
                        "tier": text_inv.get("tier", "parser"),
                        "reason": "arbitration: vision selected (tie or fewer errors)",
                        "invoice": text_inv,
                    })
                    new_invoices.append(vision_inv)
            else:
                new_invoices.append(vision_inv)

        # Merge llm_usage
        merged_usage = {
            "calls": prior_usage.get("calls", 0) + total_usage.calls,
            "input_tokens": prior_usage.get("input_tokens", 0) + total_usage.input_tokens,
            "output_tokens": prior_usage.get("output_tokens", 0) + total_usage.output_tokens,
        }

        return {
            "invoices": kept_invoices + new_invoices,
            "validation_errors": val_errors,
            "warnings": warnings,
            "page_text": page_text,
            "page_source": page_source,
            "escalated": True,
            "vision_queue": [],
            "llm_usage": merged_usage,
        }

    return vision_extract
