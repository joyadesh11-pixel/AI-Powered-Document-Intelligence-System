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
import struct
import zipfile
from pathlib import Path
from typing import Any

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


# ------------------------------------------------------------------ main generator

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

    # ---- CSV freeform (same format, different name used by scenarios) ----
    fname = "freeform_001.csv"
    items = _gen_items(r, 2)
    inv = _make_csv_utf8(
        out_dir / fname,
        inv_number=f"INV-CSV-{seed}-0002",
        inv_date="2026-10-19",
        supplier=fake.company(),
        customer=fake.company(),
        currency="EUR",
        items=items,
        tax_rate=0,
    )
    gt.append({"file": fname, "group": "csv", "expect_status": "validated_ok",
               "injected": None, "invoices": [inv]})

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
