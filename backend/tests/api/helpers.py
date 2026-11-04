"""Builders shared by the API tests.

The fake extraction only contains values that are printed on the PDF page, so
grounding passes and the pipeline makes exactly one LLM call per document.
"""
from __future__ import annotations

import io

from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas

from app.schemas.models import ExtractedInvoice


def make_pdf(number: str = "INV-001", supplier: str = "Acme Corp", total: str = "100.00",
             subtotal: str | None = None, tax: str | None = None) -> bytes:
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=letter, invariant=1)   # no timestamp: same args, same bytes
    lines = [f"Invoice {number}", f"Supplier: {supplier}", "Date: 2026-11-03",
             "Thank you for your business with us"]
    if subtotal is not None:
        lines.append(f"Subtotal: {subtotal}")
    if tax is not None:
        lines.append(f"Tax: {tax}")
    lines.append(f"Total: {total}")
    for i, line in enumerate(lines):
        c.drawString(100, 700 - 20 * i, line)
    c.save()
    return buf.getvalue()


def fake_invoice(number: str = "INV-001", supplier: str = "Acme Corp", total: str = "100.00",
                 subtotal: str | None = None, tax: str | None = None) -> ExtractedInvoice:
    return ExtractedInvoice(
        invoice_number=number, invoice_date="2026-11-03", supplier_name=supplier, total=total, subtotal=subtotal, tax=tax,
        currency="USD", line_items=[], uncertain_fields=[],
    )


def pdf_file(name: str = "a.pdf", **kw) -> tuple[str, tuple[str, bytes, str]]:
    return ("files", (name, make_pdf(**kw), "application/pdf"))
