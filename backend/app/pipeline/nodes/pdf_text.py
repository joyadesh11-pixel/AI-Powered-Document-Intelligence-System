"""pdf_text node — extract plain text and tables from digital PDF pages.

page_text[p] = plain sorted text only (no === PAGE === prefix, no embedded tables).
tables[p]    = list of markdown table strings extracted by PyMuPDF or pdfplumber.
"""
from __future__ import annotations

import re

import pymupdf

from app.pipeline.config import get_config
from app.pipeline.state import State, node


def _table_to_markdown(tbl) -> str:
    rows = tbl.extract()
    if not rows:
        return ""
    # First row is header; insert separator after it
    def _cell(v) -> str:
        return str(v or "").replace("|", "\\|").strip()

    lines = ["| " + " | ".join(_cell(c) for c in rows[0]) + " |"]
    lines.append("| " + " | ".join("---" for _ in rows[0]) + " |")
    for row in rows[1:]:
        lines.append("| " + " | ".join(_cell(c) for c in row) + " |")
    return "\n".join(lines)


def _looks_tabular(text: str, cfg) -> bool:
    """True when enough lines have >= TABULAR_MIN_NUMERIC_TOKENS tokens containing a digit."""
    qualifying = 0
    for line in text.splitlines():
        tokens_with_digit = sum(1 for t in line.split() if re.search(r"\d", t))
        if tokens_with_digit >= cfg.TABULAR_MIN_NUMERIC_TOKENS:
            qualifying += 1
    return qualifying >= cfg.TABULAR_MIN_LINES


def _pdfplumber_tables(path: str, page_num: int) -> list[str]:
    """Extract tables from a page using pdfplumber text strategy."""
    try:
        import pdfplumber
        settings = {"vertical_strategy": "text", "horizontal_strategy": "text"}
        with pdfplumber.open(path) as pdf:
            pg = pdf.pages[page_num]
            raw_tables = pg.extract_tables(settings)
        results = []
        for rows in raw_tables:
            if not rows:
                continue
            lines = ["| " + " | ".join(str(c or "").strip() for c in rows[0]) + " |"]
            lines.append("| " + " | ".join("---" for _ in rows[0]) + " |")
            for row in rows[1:]:
                lines.append("| " + " | ".join(str(c or "").strip() for c in row) + " |")
            results.append("\n".join(lines))
        return results
    except Exception:
        return []


def _extract_page(doc: pymupdf.Document, path: str, p: int, cfg) -> tuple[str, list[str]]:
    text = doc[p].get_text("text", sort=True)

    # Try PyMuPDF table detection first
    found = doc[p].find_tables()
    md_tables: list[str] = []
    if found.tables:
        for tbl in found.tables:
            md = _table_to_markdown(tbl)
            if md:
                md_tables.append(md)
    elif _looks_tabular(text, cfg):
        # No vector-bordered tables but text looks tabular → try pdfplumber
        md_tables = _pdfplumber_tables(path, p)

    return text, md_tables


@node("pdf_text")
def pdf_text(s: State) -> dict:
    cfg = get_config()
    path = s["file_path"]
    digital = list(s.get("digital_pages", []))

    page_text: dict[int, str] = {}
    page_source: dict[int, str] = {}
    tables_out: dict[int, list[str]] = {}
    new_scanned: list[int] = []

    try:
        with pymupdf.open(path) as doc:
            for p in digital:
                try:
                    text, tbls = _extract_page(doc, path, p, cfg)
                    if len(text.strip()) < cfg.PAGE_MIN_CHARS:
                        new_scanned.append(p)
                        continue
                    page_text[p] = text
                    page_source[p] = "digital"
                    if tbls:
                        tables_out[p] = tbls
                except Exception:
                    new_scanned.append(p)
    except Exception:
        new_scanned = list(digital)

    updated_scanned = sorted(set(s.get("scanned_pages", [])) | set(new_scanned))
    return {
        "page_text": page_text,
        "page_source": page_source,
        "tables": tables_out,
        "scanned_pages": updated_scanned,
    }
