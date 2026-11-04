"""/invoices: read one invoice, apply a human correction."""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import get_session
from app.api.schemas import InvoiceOut, PatchInvoice
from app.services import corrections, queries

router = APIRouter(prefix="/invoices", tags=["invoices"])


@router.get("/{invoice_id}", response_model=InvoiceOut)
def get_invoice(invoice_id: uuid.UUID, session: Session = Depends(get_session)) -> InvoiceOut:
    return InvoiceOut.model_validate(queries.get_invoice(session, invoice_id))


@router.patch("/{invoice_id}", response_model=InvoiceOut)
def patch_invoice(
    invoice_id: uuid.UUID,
    body: PatchInvoice,
    session: Session = Depends(get_session),
) -> InvoiceOut:
    # CorrectionParseError / Conflict / NotFound propagate to the error handlers;
    # the session is closed (rolled back) on the way out, so nothing half-applied is saved
    corrections.apply(invoice_id, body.to_patch(), session)
    session.commit()
    session.expire_all()    # re-read from the DB so numbers carry the stored scale, like GET
    return InvoiceOut.model_validate(queries.get_invoice(session, invoice_id))
