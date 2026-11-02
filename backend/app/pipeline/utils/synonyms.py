"""Column name synonyms for deterministic sheet_map resolution.

Keys are pre-normalised at import time: lower-cased, non-alphanumeric → space, trimmed.
Odd-header columns ("Doc Ref", "Supplier Co", "Line Amt", "Issued On", etc.)
are intentionally absent so they trigger an LLM call.
"""
from __future__ import annotations

import re


def _normalise(s: str) -> str:
    return re.sub(r" +", " ", re.sub(r"[^a-z0-9 ]+", " ", s.casefold())).strip()


_RAW: dict[str, str] = {
    # invoice_number — unambiguous only; "doc ref", "ref no", "reference" excluded
    "inv no":           "invoice_number",
    "inv num":          "invoice_number",
    "invoice number":   "invoice_number",
    "invoice num":      "invoice_number",
    "bill no":          "invoice_number",
    "bill number":      "invoice_number",
    # invoice_date
    "invoice date":     "invoice_date",
    "inv date":         "invoice_date",
    "bill date":        "invoice_date",
    "date issued":      "invoice_date",
    # due_date
    "due date":         "due_date",
    "payment due":      "due_date",
    "payment due date": "due_date",
    "due by":           "due_date",
    # supplier_name — "supplier co" excluded
    "supplier":         "supplier_name",
    "supplier name":    "supplier_name",
    "vendor":           "supplier_name",
    "seller":           "supplier_name",
    # customer_name
    "customer":         "customer_name",
    "customer name":    "customer_name",
    "bill to":          "customer_name",
    "buyer":            "customer_name",
    # subtotal
    "subtotal":         "subtotal",
    "sub total":        "subtotal",
    # tax
    "tax":              "tax",
    "vat":              "tax",
    "gst":              "tax",
    "tax amount":       "tax",
    # total
    "total":            "total",
    "total amount":     "total",
    "grand total":      "total",
    "invoice total":    "total",
    # currency
    "currency":         "currency",
    "ccy":              "currency",
    # line fields — "line amt" excluded; "rate"/"net" excluded (too generic)
    "description":      "description",
    "quantity":         "quantity",
    "qty":              "quantity",
    "unit price":       "unit_price",
    "unit cost":        "unit_price",
    "amount":           "amount",
    "line amount":      "amount",
    "line total":       "amount",
    "extended price":   "amount",
}

SYNONYMS: dict[str, str] = {_normalise(k): v for k, v in _RAW.items()}
