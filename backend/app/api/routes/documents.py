"""/documents: upload, list, detail, pages, original file, reprocess."""
from __future__ import annotations

import logging
import re
import uuid
from typing import Annotated
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Query, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.api.deps import Dispatcher, get_dispatcher, get_session
from app.api.errors import ApiError
from app.api.schemas import (
    DocumentDetailOut, DocumentRowOut, ErrorBody, EventOut, InvoiceOut, IssueOut, Page,
    PageOut, UploadRow,
)
from app.pipeline.config import get_config
from app.services import queries, reprocess
from app.services.errors import IngestError, NotFound
from app.services.ingest import save_upload, upload_dir

log = logging.getLogger(__name__)
router = APIRouter(prefix="/documents", tags=["documents"])


def _enqueue(dispatcher: Dispatcher, document_id: uuid.UUID) -> None:
    """Hand the document to the worker. The row is already committed and stays
    `queued` if this fails; the periodic retry task picks it up later."""
    try:
        dispatcher.process(document_id)
    except Exception as exc:
        log.warning("Could not enqueue document %s: %s", document_id, type(exc).__name__)


@router.post("", status_code=202, response_model=list[UploadRow])
def upload(
    files: Annotated[list[UploadFile], File(description="1..MAX_FILES_PER_REQUEST files")],
    session: Session = Depends(get_session),
    dispatcher: Dispatcher = Depends(get_dispatcher),
) -> list[UploadRow]:
    limit = get_config().MAX_FILES_PER_REQUEST
    if len(files) > limit:
        raise ApiError(400, "TOO_MANY_FILES", f"At most {limit} files per request")

    rows: list[UploadRow] = []
    to_enqueue: list[uuid.UUID] = []
    for f in files:
        name = f.filename or "upload"
        try:
            result = save_upload(name, f.file, session)
        except IngestError as exc:
            rows.append(UploadRow(filename=name, error=ErrorBody(code=exc.code, message=exc.message)))
            continue
        doc = result.document
        rows.append(UploadRow(document_id=doc.id, filename=doc.filename, status=doc.status,
                              cached=result.cached))
        if not result.cached:
            to_enqueue.append(doc.id)

    session.commit()                       # a worker must never read uncommitted rows
    for document_id in to_enqueue:
        _enqueue(dispatcher, document_id)
    return rows


@router.get("", response_model=Page[DocumentRowOut])
def list_documents(
    status: str | None = None,
    q: str | None = Query(default=None, max_length=200),
    sort: str = "created_at:desc",
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    session: Session = Depends(get_session),
) -> Page[DocumentRowOut]:
    listing = queries.list_documents(session, status=status, q=q, sort=sort, limit=limit, offset=offset)
    items = [
        DocumentRowOut.model_validate(r.document).model_copy(update={
            "invoice_count": r.invoice_count,
            "issue_count": r.issue_count,
            "error_count": r.error_count,
        })
        for r in listing.items
    ]
    return Page(items=items, total=listing.total, limit=limit, offset=offset)


@router.get("/{document_id}", response_model=DocumentDetailOut)
def get_document(document_id: uuid.UUID, session: Session = Depends(get_session)) -> DocumentDetailOut:
    d = queries.get_document(session, document_id)
    base = DocumentRowOut.model_validate(d.document).model_dump(
        exclude={"invoice_count", "issue_count", "error_count"}
    )
    return DocumentDetailOut(
        **base,
        tier_summary=d.document.tier_summary,
        timings=d.document.timings,
        llm_usage=d.document.llm_usage,
        events=[EventOut.model_validate(e) for e in d.events],
        invoices=[InvoiceOut.model_validate(i) for i in d.invoices],
        issues=[IssueOut.model_validate(i) for i in d.document_issues],
    )


@router.get("/{document_id}/pages", response_model=list[PageOut])
def get_pages(document_id: uuid.UUID, session: Session = Depends(get_session)) -> list[PageOut]:
    return [PageOut.model_validate(p) for p in queries.get_pages(session, document_id)]


_MAGIC_TYPES = (
    (b"%PDF", "application/pdf"),
    (b"\x89PNG", "image/png"),
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"II*\x00", "image/tiff"),
    (b"MM\x00*", "image/tiff"),
    (b"PK\x03\x04", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
    (b"\xd0\xcf\x11\xe0", "application/vnd.ms-excel"),
)


def _media_type(path, filename: str) -> str:
    with open(path, "rb") as fh:
        head = fh.read(8)
    for magic, media in _MAGIC_TYPES:
        if head.startswith(magic):
            return media
    return "text/csv" if filename.lower().endswith(".csv") else "application/octet-stream"


def _content_disposition(filename: str) -> str:
    safe = re.sub(r"\.{2,}", ".", filename)
    ascii_name = re.sub(r"[^A-Za-z0-9._-]", "_", safe.encode("ascii", "replace").decode())
    return f"inline; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(safe, safe='')}"


@router.get("/{document_id}/file")
def get_file(document_id: uuid.UUID, session: Session = Depends(get_session)) -> FileResponse:
    d = queries.get_document(session, document_id)
    path = upload_dir() / d.document.file_hash          # content hash: user input never forms a path
    if not path.is_file():
        raise NotFound("Original file is no longer available")
    return FileResponse(
        path,
        media_type=_media_type(path, d.document.filename),
        headers={"Content-Disposition": _content_disposition(d.document.filename)},
    )


@router.post("/{document_id}/reprocess", status_code=202, response_model=UploadRow)
def reprocess_document(
    document_id: uuid.UUID,
    force: bool = False,
    session: Session = Depends(get_session),
    dispatcher: Dispatcher = Depends(get_dispatcher),
) -> UploadRow:
    doc = reprocess.run(document_id, session, force=force)
    row = UploadRow(document_id=doc.id, filename=doc.filename, status=doc.status)
    session.commit()
    _enqueue(dispatcher, document_id)
    return row
