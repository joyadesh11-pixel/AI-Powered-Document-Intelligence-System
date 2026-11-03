"""Generate deterministic invoice fixture files for testing.

Usage:
    python -m scripts.generate_fixtures --seed 42 --out data/fixtures

Produces files + eval/ground_truth.json (relative to project root) and
ground_truth.json next to the fixtures.
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import os
import re
import struct
import warnings
import zipfile
from pathlib import Path
from typing import Any

import numpy as np
import cv2
import fitz  # PyMuPDF
from faker import Faker
from PIL import Image, ImageDraw, ImageFont
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet


# ------------------------------------------------------------------ helpers

def _rng(seed: int):
    """Return a simple deterministic random helper using Python's built-in."""
    import random
    r = random.Random(seed)
    return r


def _fmt_amount_us(v: str) -> str:
    """Format Decimal-string as US: 1,234.56"""
    d, _, cents = v.partition(".")
    cents = cents.ljust(2, "0")[:2]
    # add thousands separator
    parts = []
    while len(d) > 3:
        parts.insert(0, d[-3:])
        d = d[:-3]
    parts.insert(0, d)
    return ",".join(parts) + "." + cents


def _fmt_amount_eu(v: str) -> str:
    """Format Decimal-string as EU: 1.234,56"""
    us = _fmt_amount_us(v)
    return us.replace(",", "X").replace(".", ",").replace("X", ".")


def _fmt_amount_plain(v: str) -> str:
    """Format as plain integer with thousands comma, no decimals: 1,234"""
    d = v.partition(".")[0]
    parts = []
    while len(d) > 3:
        parts.insert(0, d[-3:])
        d = d[:-3]
    parts.insert(0, d)
    return ",".join(parts)


# ------------------------------------------------------------------ PDF templates


def _make_pdf_template_A(
    path: Path,
    inv_number: str,
    inv_date: str,
    supplier: str,
    customer: str,
    line_items: list[dict],
    currency: str,
    tax_rate: int,
    amount_fmt: str = "us",
) -> dict:
    """Template A: classic bordered table. Returns ground-truth invoice dict."""
    doc = SimpleDocTemplate(str(path), pagesize=A4,
                            leftMargin=20*mm, rightMargin=20*mm,
                            topMargin=20*mm, bottomMargin=20*mm)
    styles = getSampleStyleSheet()
    story = []

    def fmt(v):
        s = f"{v:.2f}"
        if amount_fmt == "eu":
            return _fmt_amount_eu(s)
        elif amount_fmt == "plain":
            return _fmt_amount_plain(s)
        return _fmt_amount_us(s)

    story.append(Paragraph(f"<b>INVOICE</b>", styles["Title"]))
    story.append(Spacer(1, 4*mm))
    story.append(Paragraph(f"Invoice Number: {inv_number}", styles["Normal"]))
    story.append(Paragraph(f"Invoice Date: {inv_date}", styles["Normal"]))
    story.append(Paragraph(f"Supplier: {supplier}", styles["Normal"]))
    story.append(Paragraph(f"Customer: {customer}", styles["Normal"]))
    story.append(Paragraph(f"Currency: {currency}", styles["Normal"]))
    story.append(Spacer(1, 4*mm))

    subtotal = sum(it["quantity"] * it["unit_price"] for it in line_items)
    tax = subtotal * tax_rate / 100
    total = subtotal + tax

    table_data = [["#", "Description", "Qty", "Unit Price", "Amount"]]
    for i, it in enumerate(line_items, 1):
        amt = it["quantity"] * it["unit_price"]
        table_data.append([
            str(i), it["description"],
            str(it["quantity"]),
            fmt(it["unit_price"]),
            fmt(amt),
        ])
    table_data.append(["", "", "", "Subtotal:", fmt(subtotal)])
    table_data.append(["", "", "", f"Tax ({tax_rate}%):", fmt(tax)])
    table_data.append(["", "", "", "Total:", fmt(total)])

    t = Table(table_data, colWidths=[10*mm, 80*mm, 15*mm, 30*mm, 30*mm])
    t.setStyle(TableStyle([
        ("GRID", (0, 0), (-1, -1), 0.5, colors.black),
        ("BACKGROUND", (0, 0), (-1, 0), colors.lightgrey),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
    ]))
    story.append(t)
    doc.build(story)

    def _dstr(v):
        return f"{v:.2f}"

    return {
        "invoice_number": inv_number,
        "invoice_date": inv_date,
        "supplier_name": supplier,
        "customer_name": customer,
        "subtotal": _dstr(subtotal),
        "tax": _dstr(tax),
        "total": _dstr(total),
        "currency": currency,
        "line_items": [
            {
                "description": it["description"],
                "quantity": str(it["quantity"]),
                "unit_price": _dstr(it["unit_price"]),
                "amount": _dstr(it["quantity"] * it["unit_price"]),
            }
            for it in line_items
        ],
        "printed": {
            "invoice_number": inv_number,
            "invoice_date": inv_date,
            "supplier_name": supplier,
            "customer_name": customer,
            "subtotal": fmt(subtotal),
            "tax": fmt(tax),
            "total": fmt(total),
            "currency": currency,
        },
    }


def _make_pdf_template_B(
    path: Path,
    inv_number: str,
    inv_date: str,
    supplier: str,
    customer: str,
    line_items: list[dict],
    currency: str,
    tax_rate: int,
    amount_fmt: str = "us",
) -> dict:
    """Template B: borderless modern layout."""
    doc = SimpleDocTemplate(str(path), pagesize=A4,
                            leftMargin=25*mm, rightMargin=25*mm,
                            topMargin=25*mm, bottomMargin=25*mm)
    styles = getSampleStyleSheet()
    story = []

    def fmt(v):
        s = f"{v:.2f}"
        if amount_fmt == "eu":
            return _fmt_amount_eu(s)
        elif amount_fmt == "plain":
            return _fmt_amount_plain(s)
        return _fmt_amount_us(s)

    story.append(Paragraph(f"<b>{supplier}</b>", styles["Heading1"]))
    story.append(Spacer(1, 3*mm))
    story.append(Paragraph(f"Bill To: {customer}", styles["Normal"]))
    story.append(Paragraph(f"Invoice #: {inv_number}  |  Date: {inv_date}  |  Currency: {currency}", styles["Normal"]))
    story.append(Spacer(1, 6*mm))

    subtotal = sum(it["quantity"] * it["unit_price"] for it in line_items)
    tax = subtotal * tax_rate / 100
    total = subtotal + tax

    table_data = [["Description", "Qty", "Unit Price", "Amount"]]
    for it in line_items:
        amt = it["quantity"] * it["unit_price"]
        table_data.append([
            it["description"], str(it["quantity"]),
            fmt(it["unit_price"]), fmt(amt),
        ])
    table_data += [
        ["", "", "Subtotal", fmt(subtotal)],
        ["", "", f"Tax {tax_rate}%", fmt(tax)],
        ["", "", "Total Due", fmt(total)],
    ]

    t = Table(table_data, colWidths=[85*mm, 15*mm, 30*mm, 30*mm])
    t.setStyle(TableStyle([
        ("LINEBELOW", (0, 0), (-1, 0), 1, colors.black),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("LINEABOVE", (0, -3), (-1, -3), 0.5, colors.grey),
    ]))
    story.append(t)
    doc.build(story)

    def _dstr(v):
        return f"{v:.2f}"

    return {
        "invoice_number": inv_number,
        "invoice_date": inv_date,
        "supplier_name": supplier,
        "customer_name": customer,
        "subtotal": _dstr(subtotal),
        "tax": _dstr(tax),
        "total": _dstr(total),
        "currency": currency,
        "line_items": [
            {
                "description": it["description"],
                "quantity": str(it["quantity"]),
                "unit_price": _dstr(it["unit_price"]),
                "amount": _dstr(it["quantity"] * it["unit_price"]),
            }
            for it in line_items
        ],
        "printed": {
            "invoice_number": inv_number,
            "invoice_date": inv_date,
            "supplier_name": supplier,
            "customer_name": customer,
            "subtotal": fmt(subtotal),
            "tax": fmt(tax),
            "total": fmt(total),
            "currency": currency,
        },
    }


def _make_pdf_template_C(
    path: Path,
    inv_number: str,
    inv_date: str,
    supplier: str,
    customer: str,
    line_items: list[dict],
    currency: str,
    tax_rate: int,
) -> dict:
    """Template C: multi-page with 'Page x of y' footer and repeated header."""
    from reportlab.platypus import BaseDocTemplate, Frame, PageTemplate

    PAGE_W, PAGE_H = A4

    def _page_fn(canvas, doc):
        canvas.saveState()
        canvas.setFont("Helvetica", 8)
        canvas.drawRightString(
            PAGE_W - 20*mm, 10*mm,
            f"Page {doc.page} of {doc.page}",  # reportlab doesn't know total; kept for format
        )
        canvas.restoreState()

    inner_doc = SimpleDocTemplate(str(path), pagesize=A4,
                                   leftMargin=20*mm, rightMargin=20*mm,
                                   topMargin=20*mm, bottomMargin=20*mm,
                                   onFirstPage=_page_fn, onLaterPages=_page_fn)
    styles = getSampleStyleSheet()
    story = []

    def fmt(v):
        return _fmt_amount_us(f"{v:.2f}")

    header = [
        Paragraph(f"<b>INVOICE {inv_number}</b>", styles["Title"]),
        Paragraph(f"Date: {inv_date}  |  Currency: {currency}", styles["Normal"]),
        Paragraph(f"Supplier: {supplier}", styles["Normal"]),
        Paragraph(f"Customer: {customer}", styles["Normal"]),
        Spacer(1, 4*mm),
    ]
    story.extend(header)

    subtotal = sum(it["quantity"] * it["unit_price"] for it in line_items)
    tax = subtotal * tax_rate / 100
    total = subtotal + tax

    table_data = [["#", "Description", "Qty", "Unit Price", "Amount"]]
    for i, it in enumerate(line_items, 1):
        amt = it["quantity"] * it["unit_price"]
        table_data.append([str(i), it["description"], str(it["quantity"]),
                           fmt(it["unit_price"]), fmt(amt)])
    table_data += [
        ["", "", "", "Subtotal:", fmt(subtotal)],
        ["", "", "", f"Tax ({tax_rate}%):", fmt(tax)],
        ["", "", "", "Total:", fmt(total)],
    ]
    t = Table(table_data, colWidths=[10*mm, 80*mm, 15*mm, 30*mm, 30*mm])
    t.setStyle(TableStyle([
        ("GRID", (0, 0), (-1, -1), 0.5, colors.black),
        ("BACKGROUND", (0, 0), (-1, 0), colors.lightgrey),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
    ]))
    story.append(t)
    inner_doc.build(story)

    def _dstr(v):
        return f"{v:.2f}"

    return {
        "invoice_number": inv_number,
        "invoice_date": inv_date,
        "supplier_name": supplier,
        "customer_name": customer,
        "subtotal": _dstr(subtotal),
        "tax": _dstr(tax),
        "total": _dstr(total),
        "currency": currency,
        "line_items": [
            {
                "description": it["description"],
                "quantity": str(it["quantity"]),
                "unit_price": _dstr(it["unit_price"]),
                "amount": _dstr(it["quantity"] * it["unit_price"]),
            }
            for it in line_items
        ],
        "printed": {
            "invoice_number": inv_number,
            "invoice_date": inv_date,
            "supplier_name": supplier,
            "customer_name": customer,
            "subtotal": fmt(subtotal),
            "tax": fmt(tax),
            "total": fmt(total),
            "currency": currency,
        },
    }


# ------------------------------------------------------------------ sheet fixtures

def _make_xlsx_tabular(path: Path, items: list[dict], inv_number: str,
                       inv_date: str, supplier: str, customer: str,
                       currency: str, tax_rate: int) -> dict:
    """Tabular XLSX: one header row, one line per item."""
    import openpyxl
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Invoice"
    ws.append(["Invoice Number", "Invoice Date", "Supplier", "Customer",
               "Currency", "Description", "Quantity", "Unit Price", "Amount"])
    subtotal = 0.0
    rows = []
    for it in items:
        amt = it["quantity"] * it["unit_price"]
        subtotal += amt
        ws.append([inv_number, inv_date, supplier, customer, currency,
                   it["description"], it["quantity"], it["unit_price"], amt])
        rows.append(it)
    tax = subtotal * tax_rate / 100
    total = subtotal + tax
    wb.save(str(path))

    def _dstr(v):
        return f"{v:.2f}"

    return {
        "invoice_number": inv_number,
        "invoice_date": inv_date,
        "supplier_name": supplier,
        "customer_name": customer,
        "subtotal": _dstr(subtotal),
        "tax": _dstr(tax),
        "total": _dstr(total),
        "currency": currency,
        "line_items": [
            {
                "description": it["description"],
                "quantity": str(it["quantity"]),
                "unit_price": _dstr(it["unit_price"]),
                "amount": _dstr(it["quantity"] * it["unit_price"]),
            }
            for it in rows
        ],
    }


def _make_xlsx_freeform(path: Path, inv_number: str, inv_date: str,
                        supplier: str, customer: str,
                        currency: str, total: float) -> dict:
    """Free-form XLSX: label-value pairs, no standard header."""
    import openpyxl
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Invoice"
    ws["A1"] = "Invoice #"
    ws["B1"] = inv_number
    ws["A2"] = "Date"
    ws["B2"] = inv_date
    ws["A3"] = "Supplier"
    ws["B3"] = supplier
    ws["A4"] = "Customer"
    ws["B4"] = customer
    ws["A5"] = "Currency"
    ws["B5"] = currency
    ws["A6"] = "Total"
    ws["B6"] = total
    wb.save(str(path))

    return {
        "invoice_number": inv_number,
        "invoice_date": inv_date,
        "supplier_name": supplier,
        "customer_name": customer,
        "subtotal": None,
        "tax": None,
        "total": f"{total:.2f}",
        "currency": currency,
        "line_items": [],
    }


def _make_xls_legacy(path: Path, inv_number: str, inv_date: str,
                     supplier: str, customer: str,
                     currency: str, items: list[dict], tax_rate: int) -> dict:
    """Legacy XLS via xlwt."""
    import xlwt
    wb = xlwt.Workbook()
    ws = wb.add_sheet("Invoice")
    headers = ["Invoice Number", "Invoice Date", "Supplier", "Customer",
               "Currency", "Description", "Quantity", "Unit Price", "Amount"]
    for col, h in enumerate(headers):
        ws.write(0, col, h)
    subtotal = 0.0
    for row, it in enumerate(items, 1):
        amt = it["quantity"] * it["unit_price"]
        subtotal += amt
        for col, v in enumerate([inv_number, inv_date, supplier, customer, currency,
                                  it["description"], it["quantity"], it["unit_price"], amt]):
            ws.write(row, col, v)
    tax = subtotal * tax_rate / 100
    total = subtotal + tax
    wb.save(str(path))

    def _dstr(v):
        return f"{v:.2f}"

    return {
        "invoice_number": inv_number,
        "invoice_date": inv_date,
        "supplier_name": supplier,
        "customer_name": customer,
        "subtotal": _dstr(subtotal),
        "tax": _dstr(tax),
        "total": _dstr(total),
        "currency": currency,
        "line_items": [
            {
                "description": it["description"],
                "quantity": str(it["quantity"]),
                "unit_price": _dstr(it["unit_price"]),
                "amount": _dstr(it["quantity"] * it["unit_price"]),
            }
            for it in items
        ],
    }


def _make_csv_utf8(path: Path, inv_number: str, inv_date: str,
                   supplier: str, customer: str,
                   currency: str, items: list[dict], tax_rate: int) -> dict:
    """UTF-8 CSV, comma-separated, cells with commas are quoted."""
    subtotal = 0.0
    rows = []
    for it in items:
        amt = it["quantity"] * it["unit_price"]
        subtotal += amt
        rows.append({
            "Invoice Number": inv_number,
            "Invoice Date": inv_date,
            "Supplier": supplier,
            "Customer": customer,
            "Currency": currency,
            "Description": it["description"],
            "Quantity": it["quantity"],
            "Unit Price": f"{it['unit_price']:.2f}",
            "Amount": f"{amt:.2f}",
        })
    tax = subtotal * tax_rate / 100
    total = subtotal + tax

    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()), quoting=csv.QUOTE_MINIMAL)
        w.writeheader()
        w.writerows(rows)

    def _dstr(v):
        return f"{v:.2f}"

    return {
        "invoice_number": inv_number,
        "invoice_date": inv_date,
        "supplier_name": supplier,
        "customer_name": customer,
        "subtotal": _dstr(subtotal),
        "tax": _dstr(tax),
        "total": _dstr(total),
        "currency": currency,
        "line_items": [
            {
                "description": it["description"],
                "quantity": str(it["quantity"]),
                "unit_price": _dstr(it["unit_price"]),
                "amount": _dstr(it["quantity"] * it["unit_price"]),
            }
            for it in items
        ],
    }


def _make_csv_cp1252(path: Path, inv_number: str, inv_date: str,
                     supplier: str, customer: str,
                     currency: str, items: list[dict], tax_rate: int) -> dict:
    """cp1252 CSV, semicolon-separated."""
    subtotal = 0.0
    rows = []
    for it in items:
        amt = it["quantity"] * it["unit_price"]
        subtotal += amt
        rows.append({
            "Invoice Number": inv_number,
            "Invoice Date": inv_date,
            "Supplier": supplier,
            "Customer": customer,
            "Currency": currency,
            "Description": it["description"],
            "Quantity": it["quantity"],
            "Unit Price": f"{it['unit_price']:.2f}",
            "Amount": f"{amt:.2f}",
        })
    tax = subtotal * tax_rate / 100
    total = subtotal + tax

    with open(path, "w", newline="", encoding="cp1252") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()),
                           delimiter=";", quoting=csv.QUOTE_MINIMAL)
        w.writeheader()
        w.writerows(rows)

    def _dstr(v):
        return f"{v:.2f}"

    return {
        "invoice_number": inv_number,
        "invoice_date": inv_date,
        "supplier_name": supplier,
        "customer_name": customer,
        "subtotal": _dstr(subtotal),
        "tax": _dstr(tax),
        "total": _dstr(total),
        "currency": currency,
        "line_items": [
            {
                "description": it["description"],
                "quantity": str(it["quantity"]),
                "unit_price": _dstr(it["unit_price"]),
                "amount": _dstr(it["quantity"] * it["unit_price"]),
            }
            for it in items
        ],
    }


# ------------------------------------------------------------------ image fixtures

def _make_png(path: Path, width: int = 400, height: int = 300) -> None:
    """Minimal valid PNG — white rectangle."""
    img = Image.new("RGB", (width, height), color=(255, 255, 255))
    img.save(str(path), "PNG")


def _make_jpg(path: Path, width: int = 400, height: int = 300) -> None:
    """Minimal valid JPEG."""
    img = Image.new("RGB", (width, height), color=(240, 240, 240))
    img.save(str(path), "JPEG", quality=85)


def _make_tiff_multiframe(path: Path, n_frames: int = 3) -> None:
    """Multi-frame TIFF."""
    frames = [Image.new("RGB", (200, 150), color=(200 + i * 10, 200, 200))
              for i in range(n_frames)]
    frames[0].save(str(path), format="TIFF", save_all=True, append_images=frames[1:])


# ------------------------------------------------------------------ bad fixtures

def _make_bad_truncated_pdf(path: Path) -> None:
    """%PDF-1.4 header + ~100 garbage bytes — fitz cannot recover."""
    data = b"%PDF-1.4\n" + b"\x00\x01\x02" * 35  # 105 bytes of garbage
    path.write_bytes(data)
    # self-check
    try:
        doc = fitz.open(str(path))
        assert doc.page_count == 0, "truncated PDF should have 0 pages"
        doc.close()
    except Exception:
        pass  # fitz raised — fixture is correctly broken


def _make_bad_encrypted_pdf(path: Path, tmp_path: Path) -> None:
    """Create a password-protected PDF via PyMuPDF."""
    # First create a valid PDF, then re-save with encryption
    tmp = tmp_path / "_enc_src.pdf"
    doc_src = SimpleDocTemplate(str(tmp), pagesize=A4)
    doc_src.build([Paragraph("Encrypted invoice", getSampleStyleSheet()["Normal"])])

    doc = fitz.open(str(tmp))
    perm = fitz.PDF_PERM_PRINT
    enc = fitz.PDF_ENCRYPT_AES_256
    doc.save(
        str(path),
        encryption=enc,
        user_pw="userpass",
        owner_pw="ownerpass",
        permissions=perm,
    )
    doc.close()


def _make_bad_empty(path: Path) -> None:
    """0-byte file."""
    path.write_bytes(b"")


def _make_bad_docx(path: Path) -> None:
    """Minimal OOXML ZIP without xl/workbook.xml — triggers UNSUPPORTED_TYPE."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("word/document.xml", b"<document/>")
        zf.writestr("[Content_Types].xml",
                    b'<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"/>')
    path.write_bytes(buf.getvalue())


def _make_bad_corrupt_xlsx(path: Path) -> None:
    """Valid ZIP but xl/workbook.xml is garbage — openpyxl fails to load."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("xl/workbook.xml", b"<CORRUPTED>not valid xml<<<<")
        zf.writestr("[Content_Types].xml",
                    b'<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                    b'<Default Extension="xml" ContentType="application/xml"/></Types>')
        zf.writestr("_rels/.rels",
                    b'<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"/>')
    path.write_bytes(buf.getvalue())


def _make_edge_truncated_png(path: Path) -> None:
    """Valid PNG truncated inside IDAT (~60 %). PIL fails normally, loads with truncated flag."""
    import PIL.ImageFile
    buf = io.BytesIO()
    img = Image.new("RGB", (400, 400), color=(100, 150, 200))
    img.save(buf, "PNG")
    raw = buf.getvalue()
    cut = int(len(raw) * 0.60)
    path.write_bytes(raw[:cut])
    # self-check: PIL fails without flag
    try:
        im = Image.open(str(path))
        im.load()
        raise AssertionError("truncated PNG should fail to load without flag")
    except Exception as exc:
        if isinstance(exc, AssertionError):
            raise
    # self-check: PIL loads with flag
    PIL.ImageFile.LOAD_TRUNCATED_IMAGES = True
    try:
        im = Image.open(str(path))
        im.load()
    finally:
        PIL.ImageFile.LOAD_TRUNCATED_IMAGES = False


def _make_edge_pdf_as_jpg(src_pdf: Path, path: Path) -> None:
    """Copy of a PDF with .jpg extension."""
    import shutil
    shutil.copy2(str(src_pdf), str(path))


def _make_edge_zipbomb_xlsx(path: Path, unzipped_mb: int = 5) -> None:
    """XLSX-shaped ZIP; xl/workbook.xml contains unzipped_mb MB of zeros.

    T8 uses Config(MAX_UNZIPPED_MB=1), so 5 MB triggers TOO_LARGE.
    We stream the content so the actual on-disk size stays small after compression.
    """
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml",
                    b'<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                    b'<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
                    b'</Types>')
        zf.writestr("_rels/.rels",
                    b'<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                    b'<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>'
                    b'</Relationships>')
        # Stream zeros into xl/workbook.xml so it compresses well but ZipInfo.file_size is large
        with zf.open("xl/workbook.xml", "w") as f:
            chunk = b"\x00" * 65536
            remaining = unzipped_mb * 1024 * 1024
            while remaining > 0:
                n = min(len(chunk), remaining)
                f.write(chunk[:n])
                remaining -= n
    path.write_bytes(buf.getvalue())


# ------------------------------------------------------------------ M3 PDF triage / text fixtures


def _make_scanned_pdf(path: Path, n_pages: int = 3) -> None:
    """PDF with n_pages full-page raster images, no text layer."""
    img = Image.new("RGB", (612, 792), color=(255, 255, 255))
    buf = io.BytesIO()
    img.save(buf, "PNG")
    img_bytes = buf.getvalue()

    doc = fitz.open()
    for _ in range(n_pages):
        page = doc.new_page(width=612, height=792)
        page.insert_image(fitz.Rect(0, 0, 612, 792), stream=img_bytes)
    doc.save(str(path))
    doc.close()


def _make_mixed_pdf(path: Path, rng, invoice_fields: dict) -> None:
    """5 digital pages (print all invoice fields) + 1 full-page scanned image."""
    inv_number = invoice_fields["invoice_number"]
    inv_date = invoice_fields["invoice_date"]
    supplier = invoice_fields["supplier_name"]
    customer = invoice_fields["customer_name"]
    currency = invoice_fields["currency"]
    subtotal_fmt = invoice_fields["printed"]["subtotal"]
    tax_fmt = invoice_fields["printed"]["tax"]
    total_fmt = invoice_fields["printed"]["total"]

    doc = fitz.open()
    for p in range(5):
        page = doc.new_page(width=612, height=792)
        y = 50
        page.insert_text(fitz.Point(50, y), f"Invoice Number: {inv_number}", fontsize=11)
        y += 16
        page.insert_text(fitz.Point(50, y), f"Invoice Date: {inv_date}", fontsize=11)
        y += 16
        page.insert_text(fitz.Point(50, y), f"Supplier: {supplier}", fontsize=11)
        y += 16
        page.insert_text(fitz.Point(50, y), f"Customer: {customer}", fontsize=11)
        y += 16
        page.insert_text(fitz.Point(50, y), f"Currency: {currency}", fontsize=11)
        y += 16
        page.insert_text(fitz.Point(50, y), f"Subtotal: {subtotal_fmt}", fontsize=11)
        y += 16
        page.insert_text(fitz.Point(50, y), f"Tax: {tax_fmt}", fontsize=11)
        y += 16
        page.insert_text(fitz.Point(50, y), f"Total: {total_fmt}", fontsize=11)
        y += 20
        for it in invoice_fields.get("line_items", []):
            amt_fmt = _fmt_amount_us(f"{float(it['amount']):.2f}")
            page.insert_text(fitz.Point(50, y), f"  {it['description']}: {amt_fmt}", fontsize=9)
            y += 12

    # Page 5: scanned (full-page image)
    img = Image.new("RGB", (612, 792), color=(245, 245, 245))
    buf = io.BytesIO()
    img.save(buf, "PNG")
    img_bytes = buf.getvalue()
    page = doc.new_page(width=612, height=792)
    page.insert_image(fitz.Rect(0, 0, 612, 792), stream=img_bytes)

    doc.save(str(path))
    doc.close()


def _make_garbled_cid_pdf(path: Path) -> None:
    """PDF whose text layer is made of literal (cid:N) fragments."""
    doc = fitz.open()
    page = doc.new_page(width=612, height=792)
    text = " ".join(f"(cid:{i})" for i in range(1, 60))
    page.insert_text(fitz.Point(50, 100), text, fontsize=10)
    doc.save(str(path))
    doc.close()


def _garbled_ratio_check(text: str) -> float:
    """Inline copy of _garbled_ratio for use in the self-check (avoids import cycle)."""
    if not text:
        return 0.0
    cleaned = re.sub(r"\(cid:\d+\)", "�", text)
    bad = sum(
        1 for c in cleaned
        if c == "�"
        or ("" <= c <= "")
        or (ord(c) < 32 and c not in "\n\r\t")
    )
    return bad / len(cleaned) if cleaned else 0.0


def _make_garbled_pua_pdf(path: Path) -> bool:
    """Create a CIDFontType2+Identity-H PDF with no ToUnicode.
    Self-checks: extracted text >= 80 chars AND garbled_ratio > 0.20.
    Returns True if written; False if self-check fails (file is deleted)."""
    hex_glyphs = "".join(f"{i:04x}" for i in range(1, 51))
    content_stream = f"BT /F1 12 Tf 50 700 Td <{hex_glyphs}> Tj ET\n"
    content_bytes = content_stream.encode("latin-1")
    clen = len(content_bytes)

    # Build PDF objects as byte strings
    def obj(oid: int, body: bytes) -> bytes:
        return f"{oid} 0 obj\n".encode() + body + b"\nendobj\n"

    o1 = obj(1, b"<< /Type /Catalog /Pages 2 0 R >>")
    o2 = obj(2, b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>")
    o3 = obj(3, (
        b"<< /Type /Page /Parent 2 0 R "
        b"/MediaBox [0 0 612 792] "
        b"/Contents 4 0 R "
        b"/Resources << /Font << /F1 5 0 R >> >> >>"
    ))
    stream_body = (
        f"<< /Length {clen} >>\nstream\n".encode("latin-1")
        + content_bytes
        + b"\nendstream"
    )
    o4 = obj(4, stream_body)
    o5 = obj(5, (
        b"<< /Type /Font /Subtype /Type0 "
        b"/BaseFont /Helvetica "
        b"/Encoding /Identity-H "
        b"/DescendantFonts [6 0 R] >>"
    ))
    o6 = obj(6, (
        b"<< /Type /Font /Subtype /CIDFontType2 "
        b"/BaseFont /Helvetica "
        b"/CIDSystemInfo << /Registry (Adobe) /Ordering (Identity) /Supplement 0 >> "
        b"/DW 600 >>"
    ))

    header = b"%PDF-1.4\n"
    parts = [header, o1, o2, o3, o4, o5, o6]
    offsets: list[int] = []
    pos = len(header)
    for p in [o1, o2, o3, o4, o5, o6]:
        offsets.append(pos)
        pos += len(p)

    xref_pos = pos
    xref = b"xref\n0 7\n0000000000 65535 f \n"
    for off in offsets:
        xref += f"{off:010d} 00000 n \n".encode()
    trailer = (
        b"trailer\n<< /Size 7 /Root 1 0 R >>\n"
        b"startxref\n"
        + f"{xref_pos}\n".encode()
        + b"%%EOF\n"
    )

    pdf_bytes = b"".join(parts) + xref + trailer
    path.write_bytes(pdf_bytes)

    try:
        with fitz.open(str(path)) as doc:
            t = doc[0].get_text().strip()
            ratio = _garbled_ratio_check(t)
            ok = len(t) >= 80 and ratio > 0.20
    except Exception:
        ok = False

    if not ok:
        path.unlink(missing_ok=True)
        warnings.warn("garbled_pua self-check failed; fixture skipped")
        return False
    return True


def _make_blank_page_pdf(path: Path) -> None:
    """PDF with a single blank page and no content."""
    doc = fitz.open()
    doc.new_page(width=612, height=792)
    doc.save(str(path))
    doc.close()


def _make_scan_with_pagenum_pdf(path: Path) -> None:
    """Full-page image (img_cover > 0.85) + 80-120 chars stray text. Rule 3 fires."""
    img = Image.new("RGB", (612, 792), color=(240, 240, 240))
    buf = io.BytesIO()
    img.save(buf, "PNG")
    img_bytes = buf.getvalue()

    doc = fitz.open()
    page = doc.new_page(width=612, height=792)
    # Rect(11,11,601,781): area=590*770=454300; page=612*792=484704; ratio≈0.937
    page.insert_image(fitz.Rect(11, 11, 601, 781), stream=img_bytes)
    # 80-120 chars: bypasses PAGE_MIN_CHARS(50), stays under SCAN_TEXT_MAX_CHARS(200)
    label = "Page 1  " * 12  # 96 chars
    page.insert_text(fitz.Point(50, 790), label, fontsize=8)
    doc.save(str(path))
    doc.close()


def _make_scan_with_pagenum_short_pdf(path: Path) -> None:
    """Full-page image + 3-char page number. Rule 1 (chars < PAGE_MIN_CHARS) fires."""
    img = Image.new("RGB", (612, 792), color=(240, 240, 240))
    buf = io.BytesIO()
    img.save(buf, "PNG")
    img_bytes = buf.getvalue()

    doc = fitz.open()
    page = doc.new_page(width=612, height=792)
    page.insert_image(fitz.Rect(11, 11, 601, 781), stream=img_bytes)
    page.insert_text(fitz.Point(50, 790), "123", fontsize=8)
    doc.save(str(path))
    doc.close()


def _make_borderless_table_pdf(path: Path, rng, line_items: list[dict]) -> dict:
    """Text-only 15-item table, no vector strokes. GT invoice with printed block."""
    inv_number = "INV-BT-001"
    inv_date = "2026-10-25"
    supplier = "Borderless Corp"
    customer = "Table Client Ltd"
    currency = "USD"
    tax_rate = 10

    subtotal = sum(it["quantity"] * it["unit_price"] for it in line_items)
    tax = subtotal * tax_rate / 100
    total = subtotal + tax

    def fmt(v):
        return _fmt_amount_us(f"{v:.2f}")

    def _dstr(v):
        return f"{v:.2f}"

    doc = fitz.open()
    page = doc.new_page(width=612, height=792)

    y = 50
    page.insert_text(fitz.Point(50, y), f"Invoice: {inv_number}  Date: {inv_date}", fontsize=10)
    y += 18
    page.insert_text(fitz.Point(50, y), f"Supplier: {supplier}  Customer: {customer}  Currency: {currency}", fontsize=10)
    y += 25

    # Column header (plain text, no strokes)
    page.insert_text(fitz.Point(50, y), "Description", fontsize=9)
    page.insert_text(fitz.Point(260, y), "Qty", fontsize=9)
    page.insert_text(fitz.Point(320, y), "Unit Price", fontsize=9)
    page.insert_text(fitz.Point(430, y), "Amount", fontsize=9)
    y += 14

    for it in line_items:
        amt = it["quantity"] * it["unit_price"]
        page.insert_text(fitz.Point(50, y), it["description"], fontsize=9)
        page.insert_text(fitz.Point(260, y), str(it["quantity"]), fontsize=9)
        page.insert_text(fitz.Point(320, y), fmt(it["unit_price"]), fontsize=9)
        page.insert_text(fitz.Point(430, y), fmt(amt), fontsize=9)
        y += 14

    y += 4
    page.insert_text(fitz.Point(320, y), "Subtotal:", fontsize=9)
    page.insert_text(fitz.Point(430, y), fmt(subtotal), fontsize=9)
    y += 14
    page.insert_text(fitz.Point(320, y), f"Tax {tax_rate}%:", fontsize=9)
    page.insert_text(fitz.Point(430, y), fmt(tax), fontsize=9)
    y += 14
    page.insert_text(fitz.Point(320, y), "Total:", fontsize=9)
    page.insert_text(fitz.Point(430, y), fmt(total), fontsize=9)

    doc.save(str(path))
    doc.close()

    return {
        "invoice_number": inv_number,
        "invoice_date": inv_date,
        "supplier_name": supplier,
        "customer_name": customer,
        "subtotal": _dstr(subtotal),
        "tax": _dstr(tax),
        "total": _dstr(total),
        "currency": currency,
        "line_items": [
            {
                "description": it["description"],
                "quantity": str(it["quantity"]),
                "unit_price": _dstr(it["unit_price"]),
                "amount": _dstr(it["quantity"] * it["unit_price"]),
            }
            for it in line_items
        ],
        "printed": {
            "invoice_number": inv_number,
            "invoice_date": inv_date,
            "supplier_name": supplier,
            "customer_name": customer,
            "subtotal": fmt(subtotal),
            "tax": fmt(tax),
            "total": fmt(total),
            "currency": currency,
        },
    }


def _make_two_column_pdf(path: Path, invoice_fields: dict) -> dict:
    """Two-column layout: invoice header at x≈50 (LEFT_COL), totals at x≈330 (RIGHT_COL)."""
    inv_number = invoice_fields["invoice_number"]
    inv_date = invoice_fields["invoice_date"]
    supplier = invoice_fields["supplier_name"]
    customer = invoice_fields["customer_name"]
    currency = invoice_fields["currency"]
    subtotal_fmt = invoice_fields["printed"]["subtotal"]
    tax_fmt = invoice_fields["printed"]["tax"]
    total_fmt = invoice_fields["printed"]["total"]

    doc = fitz.open()
    page = doc.new_page(width=612, height=792)

    # Left column
    y = 100
    page.insert_text(fitz.Point(50, y), "LEFT_COL", fontsize=10)
    y += 18
    page.insert_text(fitz.Point(50, y), f"Invoice: {inv_number}", fontsize=10)
    y += 15
    page.insert_text(fitz.Point(50, y), f"Date: {inv_date}", fontsize=10)
    y += 15
    page.insert_text(fitz.Point(50, y), f"Supplier: {supplier}", fontsize=10)
    y += 15
    page.insert_text(fitz.Point(50, y), f"Customer: {customer}", fontsize=10)

    # Right column
    y = 100
    page.insert_text(fitz.Point(330, y), "RIGHT_COL", fontsize=10)
    y += 18
    page.insert_text(fitz.Point(330, y), f"Currency: {currency}", fontsize=10)
    y += 15
    page.insert_text(fitz.Point(330, y), f"Subtotal: {subtotal_fmt}", fontsize=10)
    y += 15
    page.insert_text(fitz.Point(330, y), f"Tax: {tax_fmt}", fontsize=10)
    y += 15
    page.insert_text(fitz.Point(330, y), f"Total: {total_fmt}", fontsize=10)

    doc.save(str(path))
    doc.close()

    return {
        "invoice_number": inv_number,
        "invoice_date": inv_date,
        "supplier_name": supplier,
        "customer_name": customer,
        "subtotal": invoice_fields["subtotal"],
        "tax": invoice_fields["tax"],
        "total": invoice_fields["total"],
        "currency": currency,
        "line_items": [],
        "printed": invoice_fields["printed"],
    }


# ------------------------------------------------------------------ invoice data generation

def _gen_items(r, n: int) -> list[dict]:
    descs = ["Consulting Services", "Software License", "Hardware Component",
             "Support Contract", "Training Session", "Cloud Hosting",
             "Data Storage", "API Access", "Custom Development", "Maintenance"]
    items = []
    for _ in range(n):
        desc = r.choice(descs)
        qty = r.randint(1, 20)
        price = round(r.uniform(5.0, 500.0), 2)
        items.append({"description": desc, "quantity": qty, "unit_price": price})
    return items


# ------------------------------------------------------------------ M4 sheet fixtures


def _make_xlsx_simple_linegrain(path: Path, r) -> list[dict]:
    """Standard headers, 3 invoices × 5 lines = 15 rows (+67 repeats = 202 total for classify)."""
    import openpyxl
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Invoice"
    headers = ["Invoice Number", "Invoice Date", "Supplier", "Customer", "Currency",
               "Description", "Quantity", "Unit Price", "Amount", "Subtotal", "Tax", "Total"]
    ws.append(headers)

    descs = ["Consulting", "Hosting", "Support", "License", "Training",
             "Audit", "Config", "Deploy", "Monitor", "Review"]
    inv_data = [
        ("INV-SL-001", "2026-10-01", "Alpha Corp", "Beta Ltd", "USD"),
        ("INV-SL-002", "2026-10-02", "Alpha Corp", "Gamma Inc", "USD"),
        ("INV-SL-003", "2026-10-03", "Delta LLC", "Epsilon Co", "EUR"),
    ]
    gt_invoices = []
    for inv_no, inv_date, supplier, customer, currency in inv_data:
        qty_base = 2
        unit_price = 10
        n_lines = 68  # ~68 lines per invoice → 204 rows total → classify as tabular
        subtotal = qty_base * unit_price * n_lines
        tax = subtotal // 10
        total = subtotal + tax
        for i in range(n_lines):
            amt = qty_base * unit_price
            ws.append([inv_no, inv_date, supplier, customer, currency,
                       descs[i % len(descs)], qty_base, unit_price, amt,
                       subtotal, tax, total])
        items = [{"description": descs[i % len(descs)], "quantity": str(qty_base),
                  "unit_price": f"{unit_price:.2f}", "amount": f"{qty_base * unit_price:.2f}"}
                 for i in range(n_lines)]
        gt_invoices.append({
            "invoice_number": inv_no, "invoice_date": inv_date,
            "supplier_name": supplier, "customer_name": customer,
            "subtotal": f"{subtotal:.2f}", "tax": f"{tax:.2f}", "total": f"{total:.2f}",
            "currency": currency, "line_items": items,
        })
    wb.save(str(path))
    return gt_invoices


def _make_xlsx_invoice_grain(path: Path, r) -> list[dict]:
    """One row per invoice (grain=invoice), standard headers, no line items."""
    import openpyxl
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Invoices"
    ws.append(["Invoice Number", "Invoice Date", "Supplier", "Customer",
               "Currency", "Subtotal", "Tax", "Total"])
    gt_invoices = []
    for i in range(1, 6):
        inv_no = f"INV-IG-{i:03d}"
        inv_date = f"2026-10-{i:02d}"
        supplier = f"Supplier {i}"
        customer = f"Customer {i}"
        currency = "USD"
        subtotal = 100 * i
        tax = subtotal // 10
        total = subtotal + tax
        ws.append([inv_no, inv_date, supplier, customer, currency, subtotal, tax, total])
        gt_invoices.append({
            "invoice_number": inv_no, "invoice_date": inv_date,
            "supplier_name": supplier, "customer_name": customer,
            "subtotal": f"{subtotal:.2f}", "tax": f"{tax:.2f}", "total": f"{total:.2f}",
            "currency": currency, "line_items": [],
        })
    wb.save(str(path))
    return gt_invoices


def _make_csv_invoice_grain(path: Path, r) -> list[dict]:
    """CSV, one row per invoice (grain=invoice)."""
    fields = ["Invoice Number", "Invoice Date", "Supplier", "Customer",
              "Currency", "Subtotal", "Tax", "Total"]
    rows = []
    gt_invoices = []
    for i in range(1, 5):
        inv_no = f"INV-CIG-{i:03d}"
        inv_date = f"2026-10-{i:02d}"
        supplier = f"Supplier {i}"
        customer = f"Customer {i}"
        subtotal = 200 * i
        tax = subtotal // 10
        total = subtotal + tax
        rows.append([inv_no, inv_date, supplier, customer, "USD",
                     f"{subtotal:.2f}", f"{tax:.2f}", f"{total:.2f}"])
        gt_invoices.append({
            "invoice_number": inv_no, "invoice_date": inv_date,
            "supplier_name": supplier, "customer_name": customer,
            "subtotal": f"{subtotal:.2f}", "tax": f"{tax:.2f}", "total": f"{total:.2f}",
            "currency": "USD", "line_items": [],
        })
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(fields)
        w.writerows(rows)
    return gt_invoices


def _make_xlsx_odd_headers(path: Path, r) -> list[dict]:
    """Odd column names; 'Issued On' is the date col (dd/mm/yyyy, days > 12).
    Includes Currency and Invoice Total columns so validate passes for scenario 7.
    """
    import openpyxl
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Sheet1"
    # "Doc Ref"=invoice_number, "Issued On"=invoice_date, "Supplier Co"=supplier_name,
    # "Customer"=customer_name, "Currency"=currency, "Description"=description,
    # "Quantity"=quantity, "Unit Price"=unit_price, "Line Amt"=amount,
    # "Invoice Total"=total (header-level repeated on every line)
    ws.append(["Doc Ref", "Issued On", "Supplier Co", "Customer", "Currency",
               "Description", "Quantity", "Unit Price", "Line Amt", "Invoice Total"])
    inv_data = [
        ("OH-001", "15/10/2026", "Widgets Inc", "Acme Corp", "USD"),
        ("OH-002", "16/10/2026", "Widgets Inc", "Beta Ltd", "GBP"),
    ]
    gt_invoices = []
    descs = ["Widget A", "Widget B", "Widget C"]
    for inv_no, inv_date_dmy, supplier, customer, currency in inv_data:
        n_lines = 4
        subtotal = sum((i + 1) * 50 for i in range(n_lines))
        for j in range(n_lines):
            qty = j + 1
            unit_price = 50
            amt = qty * unit_price
            ws.append([inv_no, inv_date_dmy, supplier, customer, currency,
                       descs[j % len(descs)], qty, unit_price, amt, subtotal])
        # Convert date for ground truth
        d, m, y = inv_date_dmy.split("/")
        gt_date = f"{y}-{m}-{d}"
        gt_invoices.append({
            "invoice_number": inv_no, "invoice_date": gt_date,
            "supplier_name": supplier, "customer_name": customer,
            "subtotal": None, "tax": None, "total": f"{subtotal:.2f}",
            "currency": currency, "line_items": [],
        })
    wb.save(str(path))
    return gt_invoices


def _make_xlsx_ambiguous_dates(path: Path, r) -> list[dict]:
    """All days <= 12 so %m/%d/%Y and %d/%m/%Y both parse -> ambiguous -> LLM needed."""
    import openpyxl
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Sheet1"
    ws.append(["Invoice Number", "Invoice Date", "Supplier", "Customer",
               "Currency", "Description", "Quantity", "Unit Price", "Amount"])
    for i in range(1, 6):
        ws.append([f"INV-AD-{i:03d}", f"0{i}/0{i}/2026", "Supplier A", "Customer B",
                   "USD", "Item", 1, 100, 100])
    wb.save(str(path))
    return []


def _make_xlsx_multisheet_same_headers(path: Path, r) -> list[dict]:
    """Two sheets with identical headers — sheet_load stacks them."""
    import openpyxl
    wb = openpyxl.Workbook()
    for sheet_idx, (sheet_name, inv_no, inv_date) in enumerate([
        ("Jan", "INV-MS-001", "2026-01-15"),
        ("Feb", "INV-MS-002", "2026-02-15"),
    ]):
        if sheet_idx == 0:
            ws = wb.active
            ws.title = sheet_name
        else:
            ws = wb.create_sheet(sheet_name)
        ws.append(["Invoice Number", "Invoice Date", "Supplier", "Customer",
                   "Description", "Amount"])
        for j in range(3):
            ws.append([inv_no, inv_date, "Acme", "Beta", f"Item {j}", 100])
    wb.save(str(path))
    return []


def _make_xlsx_hidden_sheet(path: Path, r) -> list[dict]:
    """One visible sheet + one hidden sheet."""
    import openpyxl
    wb = openpyxl.Workbook()
    ws_visible = wb.active
    ws_visible.title = "Visible"
    ws_visible.append(["Invoice Number", "Invoice Date", "Amount"])
    ws_visible.append(["INV-HS-001", "2026-10-01", 100])

    ws_hidden = wb.create_sheet("Hidden")
    ws_hidden.sheet_state = "hidden"
    ws_hidden.append(["Secret"])
    ws_hidden.append(["data"])

    wb.save(str(path))
    return []


def _make_xlsx_merged_header(path: Path, r) -> list[dict]:
    """Merged title cell above the real header row; sheet_load still finds the header."""
    import openpyxl
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Sheet1"
    # Row 1: merged title
    ws.merge_cells("A1:F1")
    ws["A1"] = "Company Invoice Export 2026"
    # Row 2: real header
    ws.append(["Invoice Number", "Invoice Date", "Supplier", "Customer", "Amount", "Currency"])
    # Row 3+: data
    for i in range(1, 5):
        ws.append([f"INV-MH-{i:03d}", f"2026-10-{i:02d}", "Acme", "Beta", 100 * i, "USD"])
    wb.save(str(path))
    return []


def _make_xlsx_banner_rows(path: Path, r) -> list[dict]:
    """Header on Excel row 3 (two banner rows before it)."""
    import openpyxl
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Sheet1"
    # Row 1: title banner
    ws.append(["Company Invoice Report"])
    # Row 2: date banner
    ws.append(["Generated: 2026-10-15"])
    # Row 3: real header (0-indexed = 2)
    ws.append(["Invoice Number", "Invoice Date", "Supplier", "Customer", "Amount"])
    # Row 4+: data
    for i in range(1, 5):
        ws.append([f"INV-BR-{i:03d}", f"2026-10-{i:02d}", "Acme", "Beta", 100 * i])
    wb.save(str(path))
    return []


def _make_xlsx_formula_nocache(path: Path, r) -> list[dict]:
    """Formula cells without cached values — triggers FORMULA_NO_CACHE."""
    import openpyxl
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Sheet1"
    ws.append(["Invoice Number", "Invoice Date", "Quantity", "Unit Price", "Amount"])
    for i in range(1, 4):
        ws.append([f"INV-FNC-{i:03d}", f"2026-10-{i:02d}", i, 50, f"=C{i+1}*D{i+1}"])
    wb.save(str(path))
    return []


def _make_xlsx_2sheets_tabular_plus_note(path: Path, r) -> list[dict]:
    """Primary tabular sheet + secondary note/text sheet."""
    import openpyxl
    wb = openpyxl.Workbook()
    ws_data = wb.active
    ws_data.title = "Invoices"
    ws_data.append(["Invoice Number", "Invoice Date", "Supplier", "Amount"])
    for i in range(1, 6):
        ws_data.append([f"INV-TN-{i:03d}", f"2026-10-{i:02d}", "Acme", 100 * i])

    ws_note = wb.create_sheet("Notes")
    ws_note["A1"] = "Processing Notes"
    ws_note["A2"] = "Please review all invoices carefully."
    ws_note["A3"] = "Contact finance@example.com for queries."

    wb.save(str(path))
    return []


def _make_xlsx_excel_dates(path: Path, r) -> list[dict]:
    """Real Excel datetime objects in date column — sheet_load must strip time."""
    import openpyxl
    from datetime import datetime as dt
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Sheet1"
    ws.append(["Invoice Number", "Invoice Date", "Amount"])
    for i in range(1, 5):
        ws.append([f"INV-ED-{i:03d}", dt(2026, 10, i, 0, 0, 0), 100 * i])
    wb.save(str(path))
    return []


def _make_xlsx_tiny_4row(path: Path, r) -> list[dict]:
    """Only 4 data rows — still classifies as tabular."""
    import openpyxl
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Sheet1"
    ws.append(["Invoice Number", "Invoice Date", "Amount"])
    for i in range(1, 5):
        ws.append([f"INV-T4-{i:03d}", f"2026-10-{i:02d}", 100 * i])
    wb.save(str(path))
    return []


def _make_xlsx_leading_zeros(path: Path, r) -> list[dict]:
    """Invoice numbers with leading zeros — must be preserved as strings."""
    import openpyxl
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Sheet1"
    ws.append(["Invoice Number", "Invoice Date", "Supplier", "Amount"])
    for i in range(1, 4):
        ws.append([f"00{i:04d}", f"2026-10-{i:02d}", "Acme Corp", 100 * i])
    wb.save(str(path))
    return []


def _make_xlsx_totals_row(path: Path, r) -> list[dict]:
    """Data rows + trailing totals row without invoice number → UNGROUPED_ROWS."""
    import openpyxl
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Sheet1"
    ws.append(["Invoice Number", "Invoice Date", "Description", "Amount"])
    for j in range(1, 4):
        ws.append([f"INV-TR-001", "2026-10-01", f"Item {j}", 100 * j])
    # Totals row: no invoice number
    ws.append([None, None, "TOTAL", 600])
    wb.save(str(path))
    return []


def _make_xlsx_header_money_equal(path: Path, r) -> list[dict]:
    """Repeated total column equal across all lines of same invoice."""
    import openpyxl
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Sheet1"
    ws.append(["Invoice Number", "Invoice Date", "Description", "Amount", "Total"])
    for j in range(1, 4):
        ws.append(["INV-HME-001", "2026-10-01", f"Item {j}", 100, 300])
    wb.save(str(path))
    return []


def _make_xlsx_header_money_differ(path: Path, r) -> list[dict]:
    """Repeated total column differs across lines of same invoice → SHEET_GROUP_INCONSISTENT."""
    import openpyxl
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Sheet1"
    ws.append(["Invoice Number", "Invoice Date", "Description", "Amount", "Total"])
    for j in range(1, 4):
        ws.append(["INV-HMD-001", "2026-10-01", f"Item {j}", 100, 100 * j])  # 100, 200, 300
    wb.save(str(path))
    return []


def _make_xlsx_arith_error(path: Path, r) -> list[dict]:
    """Standard headers but wrong Total (off by 1) → ARITH_TOTAL → needs_review."""
    import openpyxl
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Sheet1"
    ws.append(["Invoice Number", "Invoice Date", "Supplier", "Customer", "Currency",
               "Description", "Quantity", "Unit Price", "Amount", "Subtotal", "Tax", "Total"])
    # 3 lines: amount = qty * unit_price
    for j in range(1, 4):
        amt = j * 100
        ws.append(["INV-AE-001", "2026-10-01", "Acme", "Beta", "USD",
                   f"Item {j}", j, 100, amt, 600, 60, 661])  # correct total=660, written=661
    wb.save(str(path))
    return [{"invoice_number": "INV-AE-001", "invoice_date": "2026-10-01",
             "supplier_name": "Acme", "customer_name": "Beta",
             "subtotal": "600.00", "tax": "60.00", "total": "660.00",
             "currency": "USD", "line_items": []}]


def _make_csv_headeronly(path: Path) -> None:
    """Header row only, no data rows."""
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["Invoice Number", "Invoice Date", "Supplier", "Amount"])


def _make_csv_cp1252_eu(path: Path, r) -> list[dict]:
    """CP1252, semicolon-delimited, EU-format amounts (1.234,56)."""
    rows = []
    gt_invoices = []
    for i in range(1, 4):
        inv_no = f"INV-EU-{i:03d}"
        inv_date = f"2026-10-{i:02d}"
        qty = i
        unit_price = 1234.56
        amt = qty * unit_price
        rows.append({
            "Invoice Number": inv_no,
            "Invoice Date": inv_date,
            "Supplier": "EU Supplier",
            "Customer": "EU Customer",
            "Currency": "EUR",
            "Quantity": str(qty),
            "Unit Price": _fmt_amount_eu(f"{unit_price:.2f}"),
            "Amount": _fmt_amount_eu(f"{amt:.2f}"),
        })
        gt_invoices.append({
            "invoice_number": inv_no, "invoice_date": inv_date,
            "supplier_name": "EU Supplier", "customer_name": "EU Customer",
            "subtotal": None, "tax": None, "total": None, "currency": "EUR", "line_items": [],
        })
    with open(path, "w", newline="", encoding="cp1252") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()),
                           delimiter=";", quoting=csv.QUOTE_MINIMAL)
        w.writeheader()
        w.writerows(rows)
    return gt_invoices


def _make_perf_5000x30(path: Path, r) -> None:
    """5000 rows × 30 columns for performance testing. Written to data/fixtures only."""
    import openpyxl
    wb = openpyxl.Workbook(write_only=True)
    ws = wb.create_sheet("Sheet1")
    # 10 standard invoice columns + 20 filler columns
    filler_cols = [f"Extra_{i:02d}" for i in range(1, 21)]
    headers = ["Invoice Number", "Invoice Date", "Supplier", "Customer", "Currency",
               "Description", "Quantity", "Unit Price", "Amount", "Total"] + filler_cols
    ws.append(headers)
    for i in range(1, 5001):
        row = [
            f"INV-PERF-{(i % 10) + 1:04d}",
            f"2026-{(i % 12) + 1:02d}-01",
            "Acme Corp",
            "Beta Ltd",
            "USD",
            f"Item {i}",
            i % 10 + 1,
            100,
            (i % 10 + 1) * 100,
            (i % 10 + 1) * 110,
        ] + [f"val_{j}" for j in range(20)]
        ws.append(row)
    wb.save(str(path))


# ------------------------------------------------------------------ main generator

# ------------------------------------------------------------------ M5 scan constants

_SCAN_INV_NUMBER = "SCAN-0001"
_SCAN_INV_DATE = "2026-11-01"
_SCAN_SUPPLIER = "ScanCo Ltd"
_SCAN_CUSTOMER = "TestClient Corp"
_SCAN_TOTAL = "99.00"
_SCAN_SUBTOTAL = "90.00"
_SCAN_TAX = "9.00"

# Ground-truth record shared by all readable scan fixtures
SCAN_GT_INVOICE = {
    "invoice_number": _SCAN_INV_NUMBER,
    "invoice_date": _SCAN_INV_DATE,
    "supplier_name": _SCAN_SUPPLIER,
    "customer_name": _SCAN_CUSTOMER,
    "subtotal": _SCAN_SUBTOTAL,
    "tax": _SCAN_TAX,
    "total": _SCAN_TOTAL,
    "currency": "USD",
    "line_items": [
        {"description": "Widget", "quantity": "10", "unit_price": "5.00", "amount": "50.00"},
        {"description": "Gadget", "quantity": "4", "unit_price": "10.00", "amount": "40.00"},
    ],
}


def _make_scan_base_pdf(path: Path) -> None:
    """Single-page reportlab invoice with known fields at readable 11-pt body text."""
    from reportlab.pdfgen import canvas as rl_canvas
    c = rl_canvas.Canvas(str(path), pagesize=(612, 792))
    c.setFont("Helvetica-Bold", 16)
    c.drawString(220, 740, "INVOICE")
    c.setFont("Helvetica", 11)
    lines = [
        f"Invoice Number: {_SCAN_INV_NUMBER}",
        f"Invoice Date: {_SCAN_INV_DATE}",
        f"Supplier: {_SCAN_SUPPLIER}",
        f"Customer: {_SCAN_CUSTOMER}",
        "Currency: USD",
        "",
        "Description          Qty    Unit Price    Amount",
        "-----------------------------------------------------",
        "Widget                10       5.00        50.00",
        "Gadget                 4      10.00        40.00",
        "",
        f"Subtotal:  {_SCAN_SUBTOTAL}",
        f"Tax (10%):  {_SCAN_TAX}",
        f"Total:     {_SCAN_TOTAL}",
    ]
    y = 700
    for line in lines:
        c.drawString(80, y, line)
        y -= 20
    c.save()


def _render_first_page_to_png(pdf_path: Path, out_path: Path, dpi: int = 250) -> None:
    """Render first page of a PDF to an RGB PNG at the given DPI."""
    doc = fitz.open(str(pdf_path))
    pix = doc[0].get_pixmap(matrix=fitz.Matrix(dpi / 72, dpi / 72))
    pix.save(str(out_path))
    doc.close()


def _make_scan_clean_png(path: Path) -> None:
    """Clean 250 DPI rasterized invoice page."""
    tmp_pdf = path.with_name(path.stem + "_base.pdf")
    try:
        _make_scan_base_pdf(tmp_pdf)
        _render_first_page_to_png(tmp_pdf, path, dpi=250)
    finally:
        if tmp_pdf.exists():
            tmp_pdf.unlink()


def _apply_degradation(src: Path, dst: Path, *,
                       blur_sigma: float = 0.0,
                       rotation_deg: float = 0.0,
                       noise_sigma: float = 0.0,
                       jpeg_quality: int | None = None) -> None:
    """Load src PNG, apply degradations, save to dst."""
    img = np.array(Image.open(str(src)).convert("RGB"))
    if rotation_deg:
        h, w = img.shape[:2]
        M = cv2.getRotationMatrix2D((w / 2, h / 2), -rotation_deg, 1.0)
        img = cv2.warpAffine(img, M, (w, h), borderValue=(255, 255, 255))
    if blur_sigma > 0.0:
        k = max(3, int(blur_sigma * 6) | 1)  # odd kernel
        img = cv2.GaussianBlur(img, (k, k), blur_sigma)
    if noise_sigma > 0.0:
        rng = np.random.RandomState(42)
        noise = rng.randint(0, int(noise_sigma), img.shape, dtype=np.uint8)
        img = cv2.add(img, noise)
    pil_img = Image.fromarray(img)
    if jpeg_quality is not None:
        buf = io.BytesIO()
        pil_img.save(buf, "JPEG", quality=jpeg_quality)
        buf.seek(0)
        pil_img = Image.open(buf).copy()
    pil_img.save(str(dst))


def _make_scan_mild_png(path: Path, clean_path: Path) -> None:
    """Mild: blur σ=0.5, rot 1.5°, light noise, JPEG q=72 artefacts baked in.
    Laplacian variance ~104 — well above BLUR_VAR_MIN so image_prep keeps it."""
    _apply_degradation(clean_path, path,
                       blur_sigma=0.5, rotation_deg=1.5, noise_sigma=10, jpeg_quality=72)


def _make_scan_moderate_png(path: Path, clean_path: Path) -> None:
    """Moderate: blur σ=0.5, rot 3.5°, noise σ=12, JPEG q=68.
    Raw Laplacian ~105; processed (post-deskew) ~76 — above BLUR_VAR_MIN so image_prep keeps it.
    More degraded than mild via larger rotation and slightly more noise/compression."""
    _apply_degradation(clean_path, path,
                       blur_sigma=0.5, rotation_deg=3.5, noise_sigma=12, jpeg_quality=68)


def _make_blurry_severe_png(path: Path, clean_path: Path) -> None:
    """Severe Gaussian blur σ=20 — unreadable; Laplacian variance << BLUR_VAR_MIN."""
    _apply_degradation(clean_path, path, blur_sigma=20.0)


def _make_rotated_5deg_png(path: Path, clean_path: Path) -> None:
    """5° clockwise rotation for deskew testing (image_prep T4)."""
    _apply_degradation(clean_path, path, rotation_deg=5.0)


def _make_rotated_90_png(path: Path, clean_path: Path) -> None:
    """90° rotation for OCR orientation-retry testing (ocr T3)."""
    img = np.array(Image.open(str(clean_path)))
    Image.fromarray(np.rot90(img)).save(str(path))


def _make_tiny_300px_png(path: Path, clean_path: Path) -> None:
    """Resize clean scan to 300×300 px — side_px < MIN_SIDE_PX."""
    Image.open(str(clean_path)).resize((300, 300), Image.LANCZOS).save(str(path))


def _make_exif_rotated_jpg_m5(path: Path, clean_path: Path) -> None:
    """Pixels stored rotated 90° CCW + EXIF Orientation=6.

    Orientation 6 tells PIL exif_transpose to apply ROTATE_270 (=90° CW) to
    recover the natural portrait orientation.  Stored as landscape on disk;
    image_prep's exif_transpose should produce a portrait image (h > w).
    """
    img = Image.open(str(clean_path)).convert("RGB")
    # Rotate pixels 90° CCW so the image is stored sideways (landscape)
    rotated = img.transpose(Image.Transpose.ROTATE_90)
    exif = Image.Exif()
    exif[0x0112] = 6  # Orientation: rotate 90° CW to correct (ROTATE_270 in PIL)
    rotated.save(str(path), "JPEG", quality=90, exif=exif.tobytes())


def _make_3frame_scan_tiff(path: Path, clean_path: Path) -> None:
    """3-frame TIFF; each frame is a copy of the clean scan page."""
    base = Image.open(str(clean_path)).convert("RGB")
    frames = [base.copy() for _ in range(3)]
    frames[0].save(str(path), format="TIFF", save_all=True, append_images=frames[1:])


def _make_blank_scan_png(path: Path) -> None:
    """White 800×1100 image — no text; for blank-page OCR test."""
    Image.new("RGB", (800, 1100), color=(255, 255, 255)).save(str(path))


# ------------------------------------------------------------------ multi-invoice PDFs

def _make_multi_invoice_page(
    doc,
    inv_number: str,
    inv_date: str,
    supplier: str,
    customer: str,
    currency: str,
    line_items: list[dict],
    tax_rate: int,
    page_num: int,
    total_pages: int,
    amount_fmt: str = "us",
) -> dict | None:
    """Add one page of a multi-page invoice to an open fitz.Document.

    Returns the ground-truth invoice dict on page 1 (when page_num==1), else None.
    """
    def fmt(v):
        s = f"{v:.2f}"
        if amount_fmt == "eu":
            return _fmt_amount_eu(s)
        elif amount_fmt == "plain":
            return _fmt_amount_plain(s)
        return _fmt_amount_us(s)

    subtotal = sum(it["quantity"] * it["unit_price"] for it in line_items)
    tax = subtotal * tax_rate / 100
    total = subtotal + tax

    page = doc.new_page(width=595, height=842)
    y = 50

    # Page header with "Page X of Y"
    page.insert_text(fitz.Point(50, y), f"Page {page_num} of {total_pages}", fontsize=9)
    y += 14

    if page_num == 1:
        page.insert_text(fitz.Point(50, y), "INVOICE", fontsize=16)
        y += 22
        page.insert_text(fitz.Point(50, y), f"Invoice No: {inv_number}", fontsize=11)
        y += 16
        page.insert_text(fitz.Point(50, y), f"Invoice Date: {inv_date}", fontsize=11)
        y += 16
        page.insert_text(fitz.Point(50, y), f"Supplier: {supplier}", fontsize=11)
        y += 16
        page.insert_text(fitz.Point(50, y), f"Customer: {customer}", fontsize=11)
        y += 16
        page.insert_text(fitz.Point(50, y), f"Currency: {currency}", fontsize=11)
        y += 20
    else:
        # Continuation page: re-print invoice number and page header only
        page.insert_text(fitz.Point(50, y), f"Invoice No: {inv_number} (continued)", fontsize=11)
        y += 20

    # Line items for this page (split evenly across pages)
    items_per_page = max(1, len(line_items) // total_pages)
    start = (page_num - 1) * items_per_page
    end = len(line_items) if page_num == total_pages else start + items_per_page
    page_items = line_items[start:end]

    for it in page_items:
        amt = it["quantity"] * it["unit_price"]
        page.insert_text(
            fitz.Point(50, y),
            f"  {it['description']}: {it['quantity']} x {fmt(it['unit_price'])} = {fmt(amt)}",
            fontsize=9,
        )
        y += 12

    # Totals only on last page
    if page_num == total_pages:
        y += 8
        page.insert_text(fitz.Point(50, y), f"Subtotal: {fmt(subtotal)}", fontsize=11)
        y += 14
        page.insert_text(fitz.Point(50, y), f"Tax ({tax_rate}%): {fmt(tax)}", fontsize=11)
        y += 14
        page.insert_text(fitz.Point(50, y), f"Total Due: {fmt(total)}", fontsize=11)

    if page_num == 1:
        def _dstr(v):
            return f"{v:.2f}"
        return {
            "invoice_number": inv_number,
            "invoice_date": inv_date,
            "supplier_name": supplier,
            "customer_name": customer,
            "subtotal": _dstr(subtotal),
            "tax": _dstr(tax),
            "total": _dstr(total),
            "currency": currency,
            "line_items": [
                {
                    "description": it["description"],
                    "quantity": str(it["quantity"]),
                    "unit_price": _dstr(it["unit_price"]),
                    "amount": _dstr(it["quantity"] * it["unit_price"]),
                }
                for it in line_items
            ],
            "printed": {
                "invoice_number": inv_number,
                "invoice_date": inv_date,
                "supplier_name": supplier,
                "customer_name": customer,
                "subtotal": fmt(subtotal),
                "tax": fmt(tax),
                "total": fmt(total),
                "currency": currency,
            },
        }
    return None


def _make_multi_invoice_pdf(
    path: Path,
    invoices: list[dict],
    rng,
    fake_gen,
) -> tuple[list[dict], list[dict]]:
    """Create a PDF with multiple invoices.

    invoices: list of dicts with keys: inv_number, inv_date, supplier, customer,
              currency, line_items, tax_rate, n_pages, amount_fmt.
    Returns: (gt_invoices list, page_range_records list).
    """
    doc = fitz.open()
    gt_invoices = []
    page_ranges = []
    global_page = 0

    for spec in invoices:
        n_pages = spec.get("n_pages", 1)
        line_items = spec["line_items"]
        start_page = global_page
        inv_gt = None
        for pg in range(1, n_pages + 1):
            result = _make_multi_invoice_page(
                doc,
                inv_number=spec["inv_number"],
                inv_date=spec["inv_date"],
                supplier=spec["supplier"],
                customer=spec["customer"],
                currency=spec["currency"],
                line_items=line_items,
                tax_rate=spec["tax_rate"],
                page_num=pg,
                total_pages=n_pages,
                amount_fmt=spec.get("amount_fmt", "us"),
            )
            if result is not None:
                inv_gt = result
            global_page += 1
        end_page = global_page - 1
        if inv_gt is not None:
            inv_gt = {**inv_gt, "pages": list(range(start_page, end_page + 1))}
            gt_invoices.append(inv_gt)
        page_ranges.append({"inv_number": spec["inv_number"], "pages": list(range(start_page, end_page + 1))})

    doc.save(str(path))
    doc.close()
    return gt_invoices, page_ranges


# ------------------------------------------------------------------ injected-error PDFs

def _make_injected_arith_pdf(
    path: Path,
    inv_number: str,
    inv_date: str,
    supplier: str,
    customer: str,
    line_items: list[dict],
    currency: str,
    tax_rate: int,
) -> dict:
    """PDF where the printed total is off by 1.00 (subtotal+tax+1.00 printed as total).

    The arithmetic error is in the printed text, not silently consistent.
    """
    subtotal = sum(it["quantity"] * it["unit_price"] for it in line_items)
    tax = subtotal * tax_rate / 100
    correct_total = subtotal + tax
    wrong_total = correct_total + 1.0  # deliberate off-by-one

    def _dstr(v):
        return f"{v:.2f}"

    doc = fitz.open()
    page = doc.new_page(width=595, height=842)
    y = 50
    page.insert_text(fitz.Point(50, y), "INVOICE", fontsize=16)
    y += 22
    page.insert_text(fitz.Point(50, y), f"Invoice No: {inv_number}", fontsize=11)
    y += 16
    page.insert_text(fitz.Point(50, y), f"Invoice Date: {inv_date}", fontsize=11)
    y += 16
    page.insert_text(fitz.Point(50, y), f"Supplier: {supplier}", fontsize=11)
    y += 16
    page.insert_text(fitz.Point(50, y), f"Customer: {customer}", fontsize=11)
    y += 16
    page.insert_text(fitz.Point(50, y), f"Currency: {currency}", fontsize=11)
    y += 20
    for it in line_items:
        amt = it["quantity"] * it["unit_price"]
        page.insert_text(fitz.Point(50, y),
                         f"  {it['description']}: {_dstr(amt)}", fontsize=9)
        y += 12
    y += 8
    page.insert_text(fitz.Point(50, y), f"Subtotal: {_fmt_amount_us(_dstr(subtotal))}", fontsize=11)
    y += 14
    page.insert_text(fitz.Point(50, y), f"Tax ({tax_rate}%): {_fmt_amount_us(_dstr(tax))}", fontsize=11)
    y += 14
    # Print WRONG total
    page.insert_text(fitz.Point(50, y), f"Total Due: {_fmt_amount_us(_dstr(wrong_total))}", fontsize=11)
    doc.save(str(path))
    doc.close()

    return {
        "invoice_number": inv_number,
        "invoice_date": inv_date,
        "supplier_name": supplier,
        "customer_name": customer,
        "subtotal": _dstr(subtotal),
        "tax": _dstr(tax),
        "total": _dstr(wrong_total),
        "currency": currency,
        "line_items": [
            {
                "description": it["description"],
                "quantity": str(it["quantity"]),
                "unit_price": _dstr(it["unit_price"]),
                "amount": _dstr(it["quantity"] * it["unit_price"]),
            }
            for it in line_items
        ],
        "printed": {
            "invoice_number": inv_number,
            "invoice_date": inv_date,
            "supplier_name": supplier,
            "customer_name": customer,
            "subtotal": _fmt_amount_us(_dstr(subtotal)),
            "tax": _fmt_amount_us(_dstr(tax)),
            "total": _fmt_amount_us(_dstr(wrong_total)),
            "currency": currency,
        },
    }


def _make_injected_dup_pdf(
    path: Path,
    inv_number: str,
    inv_date: str,
    supplier: str,
    customer: str,
    total: str,
    currency: str,
) -> dict:
    """PDF with same invoice_number, supplier_name, total as a reference invoice.

    The duplicate key in the validation layer is (invoice_number, supplier_name, total).
    """
    doc = fitz.open()
    page = doc.new_page(width=595, height=842)
    y = 50
    page.insert_text(fitz.Point(50, y), "INVOICE", fontsize=16)
    y += 22
    page.insert_text(fitz.Point(50, y), f"Invoice No: {inv_number}", fontsize=11)
    y += 16
    page.insert_text(fitz.Point(50, y), f"Invoice Date: {inv_date}", fontsize=11)
    y += 16
    page.insert_text(fitz.Point(50, y), f"Supplier: {supplier}", fontsize=11)
    y += 16
    page.insert_text(fitz.Point(50, y), f"Customer: {customer}", fontsize=11)
    y += 16
    page.insert_text(fitz.Point(50, y), f"Currency: {currency}", fontsize=11)
    y += 20
    page.insert_text(fitz.Point(50, y), f"Total Due: {_fmt_amount_us(total)}", fontsize=11)
    doc.save(str(path))
    doc.close()

    return {
        "invoice_number": inv_number,
        "invoice_date": inv_date,
        "supplier_name": supplier,
        "customer_name": "Duplicate Customer",
        "subtotal": None,
        "tax": None,
        "total": total,
        "currency": currency,
        "line_items": [],
        "printed": {
            "invoice_number": inv_number,
            "invoice_date": inv_date,
            "supplier_name": supplier,
            "customer_name": "Duplicate Customer",
            "total": _fmt_amount_us(total),
            "currency": currency,
        },
    }


def _make_injected_missing_invnum_pdf(
    path: Path,
    inv_date: str,
    supplier: str,
    customer: str,
    line_items: list[dict],
    currency: str,
    tax_rate: int,
) -> dict:
    """PDF where invoice_number is intentionally absent from the text."""
    subtotal = sum(it["quantity"] * it["unit_price"] for it in line_items)
    tax = subtotal * tax_rate / 100
    total = subtotal + tax

    def _dstr(v):
        return f"{v:.2f}"

    doc = fitz.open()
    page = doc.new_page(width=595, height=842)
    y = 50
    page.insert_text(fitz.Point(50, y), "INVOICE", fontsize=16)
    y += 22
    # Deliberately omit Invoice No line
    page.insert_text(fitz.Point(50, y), f"Invoice Date: {inv_date}", fontsize=11)
    y += 16
    page.insert_text(fitz.Point(50, y), f"Supplier: {supplier}", fontsize=11)
    y += 16
    page.insert_text(fitz.Point(50, y), f"Customer: {customer}", fontsize=11)
    y += 16
    page.insert_text(fitz.Point(50, y), f"Currency: {currency}", fontsize=11)
    y += 20
    for it in line_items:
        amt = it["quantity"] * it["unit_price"]
        page.insert_text(fitz.Point(50, y),
                         f"  {it['description']}: {_dstr(amt)}", fontsize=9)
        y += 12
    y += 8
    page.insert_text(fitz.Point(50, y), f"Subtotal: {_fmt_amount_us(_dstr(subtotal))}", fontsize=11)
    y += 14
    page.insert_text(fitz.Point(50, y), f"Tax ({tax_rate}%): {_fmt_amount_us(_dstr(tax))}", fontsize=11)
    y += 14
    page.insert_text(fitz.Point(50, y), f"Total Due: {_fmt_amount_us(_dstr(total))}", fontsize=11)
    doc.save(str(path))
    doc.close()

    return {
        "invoice_number": None,
        "invoice_date": inv_date,
        "supplier_name": supplier,
        "customer_name": customer,
        "subtotal": _dstr(subtotal),
        "tax": _dstr(tax),
        "total": _dstr(total),
        "currency": currency,
        "line_items": [
            {
                "description": it["description"],
                "quantity": str(it["quantity"]),
                "unit_price": _dstr(it["unit_price"]),
                "amount": _dstr(it["quantity"] * it["unit_price"]),
            }
            for it in line_items
        ],
        "printed": {
            "invoice_number": None,
            "invoice_date": inv_date,
            "supplier_name": supplier,
            "customer_name": customer,
            "subtotal": _fmt_amount_us(_dstr(subtotal)),
            "tax": _fmt_amount_us(_dstr(tax)),
            "total": _fmt_amount_us(_dstr(total)),
            "currency": currency,
        },
    }


def _make_scanned_pdf_from_image(path: Path, img_path: Path, n_copies: int = 1) -> None:
    """Wrap a PNG/JPG as a scanned PDF page (n_copies times)."""
    import io as _io
    img = Image.open(str(img_path)).convert("RGB")
    buf = _io.BytesIO()
    img.save(buf, "PNG")
    img_bytes = buf.getvalue()

    doc = fitz.open()
    for _ in range(n_copies):
        w, h = img.width, img.height
        page = doc.new_page(width=w, height=h)
        page.insert_image(fitz.Rect(0, 0, w, h), stream=img_bytes)
    doc.save(str(path))
    doc.close()


def generate_fixtures(out_dir: Path, seed: int = 42) -> list[dict]:
    """Generate all fixture files into out_dir. Returns list of ground-truth records."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    r = _rng(seed)
    fake = Faker()
    fake.seed_instance(seed)
    Faker.seed(seed)

    gt: list[dict] = []
    currencies = ["USD", "EUR", "GBP"]
    tax_rates = [0, 5, 10, 15, 20]
    amount_fmts = ["us", "eu", "plain"]

    # ---- digital PDF A x7 ----
    _a001_inv: dict = {}  # captured for dup_invnum fixtures
    for i in range(1, 8):
        fname = f"digital_A_{i:03d}.pdf"
        fpath = out_dir / fname
        items = _gen_items(r, r.randint(2, 5))
        inv = _make_pdf_template_A(
            fpath,
            inv_number=f"INV-A-{seed}-{i:04d}",
            inv_date=f"2026-10-{i:02d}",
            supplier=fake.company(),
            customer=fake.company(),
            line_items=items,
            currency=r.choice(currencies),
            tax_rate=r.choice(tax_rates),
            amount_fmt=amount_fmts[(i - 1) % 3],
        )
        if i == 1:
            _a001_inv = inv
        gt.append({"file": fname, "group": "digital_pdf", "expect_status": "validated_ok",
                   "injected": None, "invoices": [inv]})

    # ---- digital PDF B x7 ----
    for i in range(1, 8):
        fname = f"digital_B_{i:03d}.pdf"
        fpath = out_dir / fname
        items = _gen_items(r, r.randint(2, 5))
        inv = _make_pdf_template_B(
            fpath,
            inv_number=f"INV-B-{seed}-{i:04d}",
            inv_date=f"2026-10-{i+7:02d}",
            supplier=fake.company(),
            customer=fake.company(),
            line_items=items,
            currency=r.choice(currencies),
            tax_rate=r.choice(tax_rates),
            amount_fmt=amount_fmts[(i - 1) % 3],
        )
        gt.append({"file": fname, "group": "digital_pdf", "expect_status": "validated_ok",
                   "injected": None, "invoices": [inv]})

    # ---- digital PDF C x1 (multi-page) ----
    fname = "digital_C_001.pdf"
    items = _gen_items(r, 12)
    inv = _make_pdf_template_C(
        out_dir / fname,
        inv_number=f"INV-C-{seed}-0001",
        inv_date="2026-10-20",
        supplier=fake.company(),
        customer=fake.company(),
        line_items=items,
        currency="GBP",
        tax_rate=20,
    )
    gt.append({"file": fname, "group": "digital_pdf", "expect_status": "validated_ok",
               "injected": None, "invoices": [inv]})

    # ---- digital PDF A extra (to reach 20 total digital PDFs) ----
    for i in range(8, 11):  # A_008, A_009, A_010
        fname = f"digital_A_{i:03d}.pdf"
        fpath = out_dir / fname
        items = _gen_items(r, r.randint(2, 5))
        inv = _make_pdf_template_A(
            fpath,
            inv_number=f"INV-A-{seed}-{i:04d}",
            inv_date=f"2026-11-{i - 7:02d}",
            supplier=fake.company(),
            customer=fake.company(),
            line_items=items,
            currency=r.choice(currencies),
            tax_rate=r.choice(tax_rates),
            amount_fmt=amount_fmts[(i - 1) % 3],
        )
        gt.append({"file": fname, "group": "digital_pdf", "expect_status": "validated_ok",
                   "injected": None, "invoices": [inv]})

    for i in range(8, 10):  # B_008, B_009
        fname = f"digital_B_{i:03d}.pdf"
        fpath = out_dir / fname
        items = _gen_items(r, r.randint(2, 5))
        inv = _make_pdf_template_B(
            fpath,
            inv_number=f"INV-B-{seed}-{i:04d}",
            inv_date=f"2026-11-{i:02d}",
            supplier=fake.company(),
            customer=fake.company(),
            line_items=items,
            currency=r.choice(currencies),
            tax_rate=r.choice(tax_rates),
            amount_fmt=amount_fmts[(i - 1) % 3],
        )
        gt.append({"file": fname, "group": "digital_pdf", "expect_status": "validated_ok",
                   "injected": None, "invoices": [inv]})

    # ---- images ----
    _make_png(out_dir / "valid_001.png")
    gt.append({"file": "valid_001.png", "group": "image", "expect_status": "validated_ok",
               "injected": None, "invoices": []})

    _make_jpg(out_dir / "valid_001.jpg")
    gt.append({"file": "valid_001.jpg", "group": "image", "expect_status": "validated_ok",
               "injected": None, "invoices": []})

    _make_tiff_multiframe(out_dir / "multiframe_001.tiff", n_frames=3)
    gt.append({"file": "multiframe_001.tiff", "group": "image", "expect_status": "validated_ok",
               "injected": None, "invoices": []})

    # ---- XLSX tabular ----
    fname = "tabular_001.xlsx"
    items = _gen_items(r, 4)
    inv = _make_xlsx_tabular(
        out_dir / fname,
        items=items,
        inv_number=f"INV-XLS-{seed}-0001",
        inv_date="2026-10-15",
        supplier=fake.company(),
        customer=fake.company(),
        currency="USD",
        tax_rate=10,
    )
    gt.append({"file": fname, "group": "xlsx", "expect_status": "validated_ok",
               "injected": None, "invoices": [inv]})

    # ---- XLSX freeform ----
    fname = "freeform_001.xlsx"
    inv = _make_xlsx_freeform(
        out_dir / fname,
        inv_number=f"INV-FF-{seed}-0001",
        inv_date="2026-10-16",
        supplier=fake.company(),
        customer=fake.company(),
        currency="EUR",
        total=round(r.uniform(100, 5000), 2),
    )
    gt.append({"file": fname, "group": "xlsx", "expect_status": "validated_ok",
               "injected": None, "invoices": [inv]})

    # ---- XLS legacy ----
    fname = "legacy_001.xls"
    items = _gen_items(r, 3)
    inv = _make_xls_legacy(
        out_dir / fname,
        inv_number=f"INV-XL-{seed}-0001",
        inv_date="2026-10-17",
        supplier=fake.company(),
        customer=fake.company(),
        currency="GBP",
        items=items,
        tax_rate=5,
    )
    gt.append({"file": fname, "group": "xls", "expect_status": "validated_ok",
               "injected": None, "invoices": [inv]})

    # ---- CSV UTF-8 ----
    fname = "utf8_001.csv"
    items = _gen_items(r, 3)
    inv = _make_csv_utf8(
        out_dir / fname,
        inv_number=f"INV-CSV-{seed}-0001",
        inv_date="2026-10-18",
        supplier=fake.company(),
        customer="Acme, Inc.",  # comma in name — tests quoting
        currency="USD",
        items=items,
        tax_rate=0,
    )
    gt.append({"file": fname, "group": "csv", "expect_status": "validated_ok",
               "injected": None, "invoices": [inv]})

    # ---- CSV freeform — label-value pairs (2 cols → sheet_classify: freeform) ----
    # Fixed arithmetic subtotal+tax=total so stub invoice passes validate grounding.
    fname = "freeform_001.csv"
    freeform_inv_num = f"INV-CSV-{seed}-0002"
    freeform_supplier = fake.company()
    freeform_customer = fake.company()
    freeform_subtotal = "100.00"
    freeform_tax = "10.00"
    freeform_total = "110.00"
    with open(out_dir / fname, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["Label", "Value"])
        w.writerow(["Invoice Number", freeform_inv_num])
        w.writerow(["Date", "2026-10-19"])
        w.writerow(["Supplier", freeform_supplier])
        w.writerow(["Customer", freeform_customer])
        w.writerow(["Currency", "EUR"])
        w.writerow(["Subtotal", freeform_subtotal])
        w.writerow(["Tax", freeform_tax])
        w.writerow(["Total", freeform_total])
    inv_freeform_csv = {
        "invoice_number": freeform_inv_num,
        "invoice_date": "2026-10-19",
        "supplier_name": freeform_supplier,
        "customer_name": freeform_customer,
        "subtotal": freeform_subtotal,
        "tax": freeform_tax,
        "total": freeform_total,
        "currency": "EUR",
        "line_items": [],
        # Used by stub _make_invoice so grounding passes in scenario test
        "printed": {
            "invoice_number": freeform_inv_num,
            "invoice_date": "2026-10-19",
            "supplier_name": freeform_supplier,
            "customer_name": freeform_customer,
            "subtotal": freeform_subtotal,
            "tax": freeform_tax,
            "total": freeform_total,
            "currency": "EUR",
        },
    }
    gt.append({"file": fname, "group": "csv", "expect_status": "validated_ok",
               "injected": None, "invoices": [inv_freeform_csv]})

    # ---- CSV cp1252 ----
    fname = "cp1252_001.csv"
    items = _gen_items(r, 3)
    inv = _make_csv_cp1252(
        out_dir / fname,
        inv_number=f"INV-CSV-{seed}-0003",
        inv_date="2026-10-20",
        supplier=fake.company(),
        customer=fake.company(),
        currency="USD",
        items=items,
        tax_rate=15,
    )
    gt.append({"file": fname, "group": "csv", "expect_status": "validated_ok",
               "injected": None, "invoices": [inv]})

    # ---- M3 triage + text fixtures ----

    # scanned.pdf
    _make_scanned_pdf(out_dir / "scanned.pdf", n_pages=3)
    gt.append({"file": "scanned.pdf", "group": "triage", "eval": False,
               "expect_status": "needs_review", "injected": None, "invoices": []})

    # mixed.pdf — 5 digital pages + 1 scanned; use template A fields
    _mixed_items = _gen_items(r, r.randint(2, 4))
    _mixed_sub = sum(it["quantity"] * it["unit_price"] for it in _mixed_items)
    _mixed_tax_rate = 10
    _mixed_tax = _mixed_sub * _mixed_tax_rate / 100
    _mixed_total = _mixed_sub + _mixed_tax
    def _dstr(v): return f"{v:.2f}"
    _mixed_supplier = fake.company()
    _mixed_customer = fake.company()
    _mixed_inv = {
        "invoice_number": f"INV-MIX-{seed}-0001",
        "invoice_date": "2026-10-22",
        "supplier_name": _mixed_supplier,
        "customer_name": _mixed_customer,
        "subtotal": _dstr(_mixed_sub),
        "tax": _dstr(_mixed_tax),
        "total": _dstr(_mixed_total),
        "currency": "USD",
        "line_items": [
            {
                "description": it["description"],
                "quantity": str(it["quantity"]),
                "unit_price": _dstr(it["unit_price"]),
                "amount": _dstr(it["quantity"] * it["unit_price"]),
            }
            for it in _mixed_items
        ],
        "printed": {
            "invoice_number": f"INV-MIX-{seed}-0001",
            "invoice_date": "2026-10-22",
            "supplier_name": _mixed_supplier,
            "customer_name": _mixed_customer,
            "subtotal": _fmt_amount_us(_dstr(_mixed_sub)),
            "tax": _fmt_amount_us(_dstr(_mixed_tax)),
            "total": _fmt_amount_us(_dstr(_mixed_total)),
            "currency": "USD",
        },
    }
    _make_mixed_pdf(out_dir / "mixed.pdf", r, _mixed_inv)
    gt.append({"file": "mixed.pdf", "group": "triage", "eval": True,
               "expect_status": "validated_ok", "injected": None,
               "invoices": [_mixed_inv]})

    # garbled_cid.pdf
    _make_garbled_cid_pdf(out_dir / "garbled_cid.pdf")
    gt.append({"file": "garbled_cid.pdf", "group": "triage", "eval": False,
               "expect_status": "needs_review", "injected": None, "invoices": []})

    # garbled_pua.pdf (conditional: only if self-check passes)
    _pua_written = _make_garbled_pua_pdf(out_dir / "garbled_pua.pdf")
    if _pua_written:
        gt.append({"file": "garbled_pua.pdf", "group": "triage", "eval": False,
                   "expect_status": "needs_review", "injected": None, "invoices": []})

    # blank_page.pdf
    _make_blank_page_pdf(out_dir / "blank_page.pdf")
    gt.append({"file": "blank_page.pdf", "group": "triage", "eval": False,
               "expect_status": "needs_review", "injected": None, "invoices": []})

    # scan_with_pagenum.pdf (rule 3: img_cover > 0.85, chars in [50, 200))
    _make_scan_with_pagenum_pdf(out_dir / "scan_with_pagenum.pdf")
    gt.append({"file": "scan_with_pagenum.pdf", "group": "triage", "eval": False,
               "expect_status": "needs_review", "injected": None, "invoices": []})

    # scan_with_pagenum_short.pdf (rule 1: chars < 50)
    _make_scan_with_pagenum_short_pdf(out_dir / "scan_with_pagenum_short.pdf")
    gt.append({"file": "scan_with_pagenum_short.pdf", "group": "triage", "eval": False,
               "expect_status": "needs_review", "injected": None, "invoices": []})

    # borderless_table.pdf
    _bt_items = _gen_items(r, 15)
    _bt_inv = _make_borderless_table_pdf(out_dir / "borderless_table.pdf", r, _bt_items)
    gt.append({"file": "borderless_table.pdf", "group": "triage", "eval": True,
               "expect_status": "validated_ok", "injected": None,
               "invoices": [_bt_inv]})

    # two_column.pdf
    _tc_items = _gen_items(r, 3)
    _tc_sub = sum(it["quantity"] * it["unit_price"] for it in _tc_items)
    _tc_tax = _tc_sub * 0.15
    _tc_total = _tc_sub + _tc_tax
    _tc_supplier = fake.company()
    _tc_customer = fake.company()
    _tc_fields = {
        "invoice_number": f"INV-TC-{seed}-0001",
        "invoice_date": "2026-10-23",
        "supplier_name": _tc_supplier,
        "customer_name": _tc_customer,
        "subtotal": _dstr(_tc_sub),
        "tax": _dstr(_tc_tax),
        "total": _dstr(_tc_total),
        "currency": "GBP",
        "printed": {
            "invoice_number": f"INV-TC-{seed}-0001",
            "invoice_date": "2026-10-23",
            "supplier_name": _tc_supplier,
            "customer_name": _tc_customer,
            "subtotal": _fmt_amount_us(_dstr(_tc_sub)),
            "tax": _fmt_amount_us(_dstr(_tc_tax)),
            "total": _fmt_amount_us(_dstr(_tc_total)),
            "currency": "GBP",
        },
    }
    _tc_inv = _make_two_column_pdf(out_dir / "two_column.pdf", _tc_fields)
    gt.append({"file": "two_column.pdf", "group": "triage", "eval": True,
               "expect_status": "validated_ok", "injected": None,
               "invoices": [_tc_inv]})

    # ---- bad files ----
    _make_bad_truncated_pdf(out_dir / "bad_truncated.pdf")
    gt.append({"file": "bad_truncated.pdf", "group": "bad_files",
               "expect_status": "failed", "expect_error_code": "CORRUPT_FILE",
               "injected": None, "invoices": []})

    _make_bad_encrypted_pdf(out_dir / "bad_encrypted.pdf", tmp_path=out_dir)
    # clean up the temp source
    _tmp = out_dir / "_enc_src.pdf"
    if _tmp.exists():
        _tmp.unlink()
    gt.append({"file": "bad_encrypted.pdf", "group": "bad_files",
               "expect_status": "failed", "expect_error_code": "ENCRYPTED",
               "injected": None, "invoices": []})

    _make_bad_empty(out_dir / "bad_empty.bin")
    gt.append({"file": "bad_empty.bin", "group": "bad_files",
               "expect_status": "failed", "expect_error_code": "EMPTY_FILE",
               "injected": None, "invoices": []})

    _make_bad_docx(out_dir / "bad_docx.docx")
    gt.append({"file": "bad_docx.docx", "group": "bad_files",
               "expect_status": "failed", "expect_error_code": "UNSUPPORTED_TYPE",
               "injected": None, "invoices": []})

    _make_bad_corrupt_xlsx(out_dir / "bad_corrupt.xlsx")
    gt.append({"file": "bad_corrupt.xlsx", "group": "bad_files",
               "expect_status": "failed", "expect_error_code": "CORRUPT_FILE",
               "injected": None, "invoices": []})

    # ---- edge cases ----
    _make_edge_truncated_png(out_dir / "edge_truncated.png")
    gt.append({"file": "edge_truncated.png", "group": "edge",
               "expect_status": "validated_ok",
               "expect_warning_code": "TRUNCATED_IMAGE",
               "injected": None, "invoices": []})

    _make_edge_pdf_as_jpg(out_dir / "digital_A_001.pdf", out_dir / "edge_pdf_as_jpg.jpg")
    gt.append({"file": "edge_pdf_as_jpg.jpg", "group": "edge",
               "expect_status": "validated_ok",
               "expect_warning_code": "EXT_MISMATCH",
               "injected": None, "invoices": []})

    _make_edge_zipbomb_xlsx(out_dir / "edge_zipbomb.xlsx", unzipped_mb=5)
    gt.append({"file": "edge_zipbomb.xlsx", "group": "edge",
               "expect_status": "failed", "expect_error_code": "TOO_LARGE",
               "injected": None, "invoices": []})

    # ---- M4 sheet fixtures ----

    fname = "xlsx_simple_linegrain.xlsx"
    _inv_sl = _make_xlsx_simple_linegrain(out_dir / fname, r)
    gt.append({"file": fname, "group": "sheet_m4", "expect_status": "validated_ok",
               "injected": None, "invoices": _inv_sl})

    fname = "xlsx_invoice_grain.xlsx"
    _inv_ig = _make_xlsx_invoice_grain(out_dir / fname, r)
    gt.append({"file": fname, "group": "sheet_m4", "expect_status": "validated_ok",
               "injected": None, "invoices": _inv_ig})

    fname = "csv_invoice_grain.csv"
    _inv_cig = _make_csv_invoice_grain(out_dir / fname, r)
    gt.append({"file": fname, "group": "sheet_m4", "expect_status": "validated_ok",
               "injected": None, "invoices": _inv_cig})

    fname = "xlsx_odd_headers.xlsx"
    _inv_oh = _make_xlsx_odd_headers(out_dir / fname, r)
    gt.append({"file": fname, "group": "sheet_m4", "expect_status": "needs_review",
               "injected": None, "invoices": _inv_oh})

    fname = "xlsx_ambiguous_dates.xlsx"
    _make_xlsx_ambiguous_dates(out_dir / fname, r)
    gt.append({"file": fname, "group": "sheet_m4", "expect_status": "needs_review",
               "injected": None, "invoices": []})

    fname = "xlsx_multisheet_same_headers.xlsx"
    _make_xlsx_multisheet_same_headers(out_dir / fname, r)
    gt.append({"file": fname, "group": "sheet_m4", "expect_status": "needs_review",
               "injected": None, "invoices": []})

    fname = "xlsx_hidden_sheet.xlsx"
    _make_xlsx_hidden_sheet(out_dir / fname, r)
    gt.append({"file": fname, "group": "sheet_m4", "expect_status": "needs_review",
               "injected": None, "invoices": []})

    fname = "xlsx_merged_header.xlsx"
    _make_xlsx_merged_header(out_dir / fname, r)
    gt.append({"file": fname, "group": "sheet_m4", "expect_status": "needs_review",
               "injected": None, "invoices": []})

    fname = "xlsx_banner_rows.xlsx"
    _make_xlsx_banner_rows(out_dir / fname, r)
    gt.append({"file": fname, "group": "sheet_m4", "expect_status": "needs_review",
               "injected": None, "invoices": []})

    fname = "xlsx_formula_nocache.xlsx"
    _make_xlsx_formula_nocache(out_dir / fname, r)
    gt.append({"file": fname, "group": "sheet_m4", "expect_status": "needs_review",
               "injected": None, "invoices": []})

    fname = "xlsx_2sheets_tabular_plus_note.xlsx"
    _make_xlsx_2sheets_tabular_plus_note(out_dir / fname, r)
    gt.append({"file": fname, "group": "sheet_m4", "expect_status": "needs_review",
               "injected": None, "invoices": []})

    fname = "xlsx_excel_dates.xlsx"
    _make_xlsx_excel_dates(out_dir / fname, r)
    gt.append({"file": fname, "group": "sheet_m4", "expect_status": "needs_review",
               "injected": None, "invoices": []})

    fname = "xlsx_tiny_4row.xlsx"
    _make_xlsx_tiny_4row(out_dir / fname, r)
    gt.append({"file": fname, "group": "sheet_m4", "expect_status": "needs_review",
               "injected": None, "invoices": []})

    fname = "xlsx_leading_zeros.xlsx"
    _make_xlsx_leading_zeros(out_dir / fname, r)
    gt.append({"file": fname, "group": "sheet_m4", "expect_status": "needs_review",
               "injected": None, "invoices": []})

    fname = "xlsx_totals_row.xlsx"
    _make_xlsx_totals_row(out_dir / fname, r)
    gt.append({"file": fname, "group": "sheet_m4", "expect_status": "needs_review",
               "injected": None, "invoices": []})

    fname = "xlsx_header_money_equal.xlsx"
    _make_xlsx_header_money_equal(out_dir / fname, r)
    gt.append({"file": fname, "group": "sheet_m4", "expect_status": "needs_review",
               "injected": None, "invoices": []})

    fname = "xlsx_header_money_differ.xlsx"
    _make_xlsx_header_money_differ(out_dir / fname, r)
    gt.append({"file": fname, "group": "sheet_m4", "expect_status": "needs_review",
               "injected": None, "invoices": []})

    fname = "xlsx_arith_error.xlsx"
    _inv_ae = _make_xlsx_arith_error(out_dir / fname, r)
    gt.append({"file": fname, "group": "sheet_m4", "expect_status": "needs_review",
               "injected": None, "invoices": _inv_ae})

    fname = "csv_headeronly.csv"
    _make_csv_headeronly(out_dir / fname)
    gt.append({"file": fname, "group": "sheet_m4", "expect_status": "needs_review",
               "injected": None, "invoices": []})

    fname = "csv_cp1252_eu.csv"
    _make_csv_cp1252_eu(out_dir / fname, r)
    gt.append({"file": fname, "group": "sheet_m4", "expect_status": "needs_review",
               "injected": None, "invoices": []})

    # perf_5000x30.xlsx is generated only if not already present (slow to write)
    perf_path = out_dir / "perf_5000x30.xlsx"
    if not perf_path.exists():
        _make_perf_5000x30(perf_path, r)
    gt.append({"file": "perf_5000x30.xlsx", "group": "sheet_m4_perf",
               "expect_status": "needs_review", "injected": None, "invoices": []})

    # ---- M5 scan image fixtures ----

    _scan_clean_path = out_dir / "scan_clean.png"
    _make_scan_clean_png(_scan_clean_path)
    gt.append({"file": "scan_clean.png", "group": "image_m5", "expect_status": "validated_ok",
               "injected": None, "invoices": [SCAN_GT_INVOICE]})

    _scan_mild_path = out_dir / "scan_mild.png"
    _make_scan_mild_png(_scan_mild_path, _scan_clean_path)
    gt.append({"file": "scan_mild.png", "group": "image_m5", "expect_status": "validated_ok",
               "injected": None, "invoices": [SCAN_GT_INVOICE]})

    _scan_moderate_path = out_dir / "scan_moderate.png"
    _make_scan_moderate_png(_scan_moderate_path, _scan_clean_path)
    gt.append({"file": "scan_moderate.png", "group": "image_m5", "expect_status": "validated_ok",
               "injected": None, "invoices": [SCAN_GT_INVOICE]})

    _make_blurry_severe_png(out_dir / "blurry_severe.png", _scan_clean_path)
    gt.append({"file": "blurry_severe.png", "group": "image_m5", "expect_status": "needs_review",
               "injected": None, "invoices": []})

    _make_rotated_5deg_png(out_dir / "rotated_5deg.png", _scan_clean_path)
    gt.append({"file": "rotated_5deg.png", "group": "image_m5", "expect_status": "validated_ok",
               "injected": None, "invoices": [SCAN_GT_INVOICE]})

    _make_rotated_90_png(out_dir / "rotated_90.png", _scan_clean_path)
    gt.append({"file": "rotated_90.png", "group": "image_m5", "expect_status": "validated_ok",
               "injected": None, "invoices": [SCAN_GT_INVOICE]})

    _make_tiny_300px_png(out_dir / "tiny_300px.png", _scan_clean_path)
    gt.append({"file": "tiny_300px.png", "group": "image_m5", "expect_status": "needs_review",
               "injected": None, "invoices": []})

    _make_exif_rotated_jpg_m5(out_dir / "exif_rotated.jpg", _scan_clean_path)
    gt.append({"file": "exif_rotated.jpg", "group": "image_m5", "expect_status": "validated_ok",
               "injected": None, "invoices": [SCAN_GT_INVOICE]})

    _make_3frame_scan_tiff(out_dir / "scan_3frame.tiff", _scan_clean_path)
    gt.append({"file": "scan_3frame.tiff", "group": "image_m5", "expect_status": "validated_ok",
               "injected": None, "invoices": [SCAN_GT_INVOICE]})

    _make_blank_scan_png(out_dir / "blank.png")
    gt.append({"file": "blank.png", "group": "image_m5", "expect_status": "needs_review",
               "injected": None, "invoices": []})

    # ---- scanned PDFs (wrap M5 images as single-page PDFs) ----
    for _sf_name, _sf_img, _sf_group, _sf_status, _sf_invs in [
        ("scanned_clean.pdf",    _scan_clean_path,         "scanned_pdf", "validated_ok",  [SCAN_GT_INVOICE]),
        ("scanned_mild.pdf",     out_dir / "scan_mild.png",    "scanned_pdf", "validated_ok",  [SCAN_GT_INVOICE]),
        ("scanned_moderate.pdf", out_dir / "scan_moderate.png","scanned_pdf", "validated_ok",  [SCAN_GT_INVOICE]),
        ("scanned_severe.pdf",   out_dir / "blurry_severe.png","scanned_pdf", "needs_review",  []),
        ("scanned_rotated.pdf",  out_dir / "rotated_5deg.png", "scanned_pdf", "validated_ok",  [SCAN_GT_INVOICE]),
        ("scanned_3page.pdf",    _scan_clean_path,             "scanned_pdf", "validated_ok",  [SCAN_GT_INVOICE]),
    ]:
        n = 3 if _sf_name == "scanned_3page.pdf" else 1
        _make_scanned_pdf_from_image(out_dir / _sf_name, _sf_img, n_copies=n)
        gt.append({"file": _sf_name, "group": _sf_group,
                   "expect_status": _sf_status, "injected": None, "invoices": _sf_invs})

    # ---- multi-invoice PDFs ----

    # multi_inv_001: 3 invoices, 1 page each
    _m1_specs = []
    for _mi in range(1, 4):
        _m1_items = _gen_items(r, r.randint(2, 4))
        _m1_specs.append({
            "inv_number": f"INV-M1-{seed}-{_mi:04d}",
            "inv_date": f"2026-11-{_mi:02d}",
            "supplier": fake.company(),
            "customer": fake.company(),
            "currency": r.choice(currencies),
            "line_items": _m1_items,
            "tax_rate": r.choice(tax_rates),
            "n_pages": 1,
            "amount_fmt": "us",
        })
    _m1_gt, _m1_ranges = _make_multi_invoice_pdf(out_dir / "multi_inv_001.pdf", _m1_specs, r, fake)
    gt.append({"file": "multi_inv_001.pdf", "group": "multi_invoice",
               "expect_status": "validated_ok", "injected": None,
               "invoices": _m1_gt, "page_ranges": _m1_ranges})

    # multi_inv_002: mix of 1-page and 2-page invoices (inv1=1p, inv2=2p, inv3=1p)
    _m2_page_counts = [1, 2, 1]
    _m2_specs = []
    for _mi, _np in enumerate(_m2_page_counts, 1):
        _m2_items = _gen_items(r, r.randint(3, 6))  # more items to fill 2 pages
        _m2_specs.append({
            "inv_number": f"INV-M2-{seed}-{_mi:04d}",
            "inv_date": f"2026-11-{_mi + 3:02d}",
            "supplier": fake.company(),
            "customer": fake.company(),
            "currency": r.choice(currencies),
            "line_items": _m2_items,
            "tax_rate": r.choice(tax_rates),
            "n_pages": _np,
            "amount_fmt": "us",
        })
    _m2_gt, _m2_ranges = _make_multi_invoice_pdf(out_dir / "multi_inv_002.pdf", _m2_specs, r, fake)
    gt.append({"file": "multi_inv_002.pdf", "group": "multi_invoice",
               "expect_status": "validated_ok", "injected": None,
               "invoices": _m2_gt, "page_ranges": _m2_ranges})

    # multi_inv_003: mix of 1, 3, and 2-page invoices (inv1=1p, inv2=3p, inv3=2p, inv4=1p)
    _m3_page_counts = [1, 3, 2, 1]
    _m3_specs = []
    for _mi, _np in enumerate(_m3_page_counts, 1):
        _m3_items = _gen_items(r, r.randint(4, 8))
        _m3_specs.append({
            "inv_number": f"INV-M3-{seed}-{_mi:04d}",
            "inv_date": f"2026-11-{_mi + 7:02d}",
            "supplier": fake.company(),
            "customer": fake.company(),
            "currency": r.choice(currencies),
            "line_items": _m3_items,
            "tax_rate": r.choice(tax_rates),
            "n_pages": _np,
            "amount_fmt": "us",
        })
    _m3_gt, _m3_ranges = _make_multi_invoice_pdf(out_dir / "multi_inv_003.pdf", _m3_specs, r, fake)
    gt.append({"file": "multi_inv_003.pdf", "group": "multi_invoice",
               "expect_status": "validated_ok", "injected": None,
               "invoices": _m3_gt, "page_ranges": _m3_ranges})

    # ---- injected-error fixtures ----

    # wrong_total x3 (arithmetic error: total printed is subtotal+tax+1.00)
    for _wi in range(1, 4):
        _wt_items = _gen_items(r, r.randint(2, 4))
        _wt_inv = _make_injected_arith_pdf(
            out_dir / f"wrong_total_{_wi:03d}.pdf",
            inv_number=f"INV-WT-{seed}-{_wi:04d}",
            inv_date=f"2026-11-{_wi:02d}",
            supplier=fake.company(),
            customer=fake.company(),
            line_items=_wt_items,
            currency="USD",
            tax_rate=10,
        )
        gt.append({"file": f"wrong_total_{_wi:03d}.pdf", "group": "injected",
                   "expect_status": "needs_review",
                   "injected": {"code": "ARITH_TOTAL"},
                   "invoices": [_wt_inv]})

    # dup_invnum x2 — same invoice_number, supplier_name, and total as digital_A_001
    _a001_invnum = _a001_inv.get("invoice_number", f"INV-A-{seed}-0001")
    _a001_supplier = _a001_inv.get("supplier_name", "SupplierA")
    _a001_total = _a001_inv.get("total", "100.00")
    _a001_currency = _a001_inv.get("currency", "USD")
    for _di in range(1, 3):
        _dup_inv = _make_injected_dup_pdf(
            out_dir / f"dup_invnum_{_di:03d}.pdf",
            inv_number=_a001_invnum,
            inv_date=f"2026-11-{_di + 3:02d}",
            supplier=_a001_supplier,
            customer=fake.company(),
            total=_a001_total,
            currency=_a001_currency,
        )
        gt.append({"file": f"dup_invnum_{_di:03d}.pdf", "group": "injected",
                   "expect_status": "needs_review",
                   "injected": {"code": "DUPLICATE_INVOICE"},
                   "invoices": [_dup_inv]})

    # missing_invnum x2 (no invoice_number in the PDF text)
    for _mni in range(1, 3):
        _mn_items = _gen_items(r, r.randint(2, 3))
        _mn_inv = _make_injected_missing_invnum_pdf(
            out_dir / f"missing_invnum_{_mni:03d}.pdf",
            inv_date=f"2026-11-{_mni + 5:02d}",
            supplier=fake.company(),
            customer=fake.company(),
            line_items=_mn_items,
            currency="USD",
            tax_rate=10,
        )
        gt.append({"file": f"missing_invnum_{_mni:03d}.pdf", "group": "injected",
                   "expect_status": "needs_review",
                   "injected": {"code": "MISSING_REQUIRED"},
                   "invoices": [_mn_inv]})

    # ---- print count per group ----
    _group_counts: dict[str, int] = {}
    for _entry in gt:
        _g = _entry.get("group", "unknown")
        _group_counts[_g] = _group_counts.get(_g, 0) + 1
    print("Fixture counts per group:")
    for _g, _c in sorted(_group_counts.items()):
        print(f"  {_g:20s}: {_c}")
    print(f"  {'TOTAL':20s}: {len(gt)}")

    # ---- write ground truth ----
    gt_json = json.dumps(gt, indent=2)
    (out_dir / "ground_truth.json").write_text(gt_json, encoding="utf-8")

    return gt


def _write_eval_ground_truth(gt: list[dict], project_root: Path) -> None:
    eval_dir = project_root / "eval"
    eval_dir.mkdir(parents=True, exist_ok=True)
    (eval_dir / "ground_truth.json").write_text(json.dumps(gt, indent=2), encoding="utf-8")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Generate synthetic invoice fixtures")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--out", type=str, default="data/fixtures")
    args = parser.parse_args()

    out = Path(args.out)
    gt = generate_fixtures(out, seed=args.seed)
    print(f"Generated {len(gt)} fixture entries in {out}")

    # also write to eval/ground_truth.json relative to project root
    project_root = Path(__file__).parent.parent.parent  # backend/scripts/ -> project root
    _write_eval_ground_truth(gt, project_root)
    print(f"Ground truth written to {project_root / 'eval' / 'ground_truth.json'}")
