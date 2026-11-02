"""pdf_text node — extract text and tables from digital PDF pages.

Stub: real logic (PyMuPDF + pdfplumber fallback) implemented in M3.
"""
from __future__ import annotations

from app.pipeline.state import State, node


@node("pdf_text")
def pdf_text(s: State) -> dict:
    page_text = {p: f"<text p{p}>" for p in s["digital_pages"]}
    page_source = {p: "digital" for p in s["digital_pages"]}
    return {"page_text": page_text, "page_source": page_source}
