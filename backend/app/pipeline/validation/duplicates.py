"""Duplicate invoice detection."""
from __future__ import annotations

import re
from decimal import Decimal
from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class SeenStore(Protocol):
    def contains(self, key: str, *, exclude_hash: str | None = None) -> bool: ...


class InMemorySeenStore:
    """Pure in-memory SeenStore for testing and default pipeline use."""

    def __init__(self, keys: Any = ()) -> None:
        self._keys: set[str] = set(keys)

    def contains(self, key: str, *, exclude_hash: str | None = None) -> bool:
        return key in self._keys

    def add(self, key: str) -> None:
        self._keys.add(key)


def compute_dup_key(invoice: Any) -> str | None:
    """Return the duplicate-detection key, or None if any part is null.

    key = casefold(strip(supplier_name)) | normalised(invoice_number) | quantised(total)
    """
    supplier = _field(invoice, "supplier_name")
    inv_num = _field(invoice, "invoice_number")
    total = _field(invoice, "total")

    if supplier is None or inv_num is None or total is None:
        return None

    sup_part = str(supplier).strip().casefold()
    num_part = re.sub(r"[^A-Z0-9]", "", str(inv_num).upper())
    try:
        tot_part = str(Decimal(str(total)).quantize(Decimal("0.01")))
    except Exception:
        return None

    return f"{sup_part}|{num_part}|{tot_part}"


def check_duplicate(
    invoice: Any,
    seen_in_file: dict,
    seen_store: SeenStore | None,
    file_hash: str,
) -> Any | None:
    """Return a DUPLICATE_INVOICE Issue if this invoice is a duplicate, else None."""
    from app.schemas.models import Issue, IssueCode, Severity

    key = compute_dup_key(invoice)
    if key is None:
        return None

    unit_id = _field(invoice, "unit_id")

    # Check within-file
    if key in seen_in_file:
        return Issue(
            code=IssueCode.DUPLICATE_INVOICE,
            severity=Severity.error,
            field="invoice_number",
            message=f"Duplicate invoice key in this file (first seen at unit {seen_in_file[key]})",
        )
    seen_in_file[key] = unit_id

    # Check store
    if seen_store and seen_store.contains(key, exclude_hash=file_hash):
        return Issue(
            code=IssueCode.DUPLICATE_INVOICE,
            severity=Severity.error,
            field="invoice_number",
            message="Duplicate invoice found in document store",
        )

    return None


def _field(obj: Any, name: str) -> Any:
    if isinstance(obj, dict):
        return obj.get(name)
    return getattr(obj, name, None)
