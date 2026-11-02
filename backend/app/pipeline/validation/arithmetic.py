"""Arithmetic validation: line totals, subtotal, overall total."""
from __future__ import annotations

from decimal import Decimal
from typing import Any

from app.schemas.models import Issue, IssueCode, Severity


def check_arithmetic(invoice: Any) -> list[Issue]:
    issues: list[Issue] = []
    line_items = _get(invoice, "line_items") or []

    # ARITH_LINE: quantity * unit_price == amount per line
    for i, li in enumerate(line_items):
        qty = _get(li, "quantity")
        unit_price = _get(li, "unit_price")
        amount = _get(li, "amount")
        if qty is None or unit_price is None or amount is None:
            continue
        expected = qty * unit_price
        tol = max(Decimal("0.01"), Decimal("0.005") * abs(qty))
        if abs(expected - amount) > tol:
            issues.append(Issue(
                code=IssueCode.ARITH_LINE,
                severity=Severity.error,
                field=f"line_items[{i}].amount",
                expected=str(expected),
                got=str(amount),
                message=f"Line {i}: {qty}×{unit_price}={expected} but amount={amount} (tol={tol})",
            ))

    # ARITH_SUBTOTAL: sum of line amounts == subtotal
    subtotal = _get(invoice, "subtotal")
    line_amounts = [_get(li, "amount") for li in line_items if _get(li, "amount") is not None]
    if subtotal is not None and line_amounts:
        n_lines = Decimal(str(len(line_amounts)))
        tol = max(Decimal("0.01"), Decimal("0.005") * n_lines)
        lines_sum = sum(line_amounts, Decimal("0"))
        if abs(lines_sum - subtotal) > tol:
            issues.append(Issue(
                code=IssueCode.ARITH_SUBTOTAL,
                severity=Severity.error,
                field="subtotal",
                expected=str(lines_sum),
                got=str(subtotal),
                message=f"Sum of line amounts {lines_sum} != subtotal {subtotal} (tol={tol})",
            ))

    # ARITH_TOTAL: subtotal + tax == total
    total = _get(invoice, "total")
    tax = _get(invoice, "tax")
    if total is None:
        return issues

    tol_total = Decimal("0.01")

    if subtotal is not None and tax is not None:
        expected_total = subtotal + tax
        if abs(expected_total - total) > tol_total:
            issues.append(Issue(
                code=IssueCode.ARITH_TOTAL,
                severity=Severity.error,
                field="total",
                expected=str(expected_total),
                got=str(total),
                message=f"subtotal+tax={expected_total} != total={total} (tol={tol_total})",
            ))
    elif subtotal is None and line_amounts and tax is not None:
        # Fall back to sum(line_amounts) + tax == total
        lines_sum = sum(line_amounts, Decimal("0"))
        expected_total = lines_sum + tax
        if abs(expected_total - total) > tol_total:
            issues.append(Issue(
                code=IssueCode.ARITH_TOTAL,
                severity=Severity.error,
                field="total",
                expected=str(expected_total),
                got=str(total),
                message=f"lines_sum+tax={expected_total} != total={total} (tol={tol_total})",
            ))
    elif subtotal is not None and tax is None:
        # tax null: info-level only (not error)
        pass

    return issues


def _get(obj: Any, name: str) -> Any:
    if isinstance(obj, dict):
        return obj.get(name)
    return getattr(obj, name, None)
