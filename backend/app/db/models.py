"""SQLAlchemy 2.x ORM models.

Money columns: Numeric(18, 4) — never Float.
UUIDs: postgres native uuid type.
Timestamps: recommend timezone-aware UTC from callers.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    BigInteger, Boolean, Column, Computed, Float, ForeignKey, Index, Integer,
    Numeric, String, Text, text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from pgvector.sqlalchemy import Vector

from app.db.base import Base
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, TSVECTOR, UUID

def _now() -> datetime:
    return datetime.now(timezone.utc)


def _uuid() -> uuid.UUID:
    return uuid.uuid4()


# ---------------------------------------------------------------------------
# documents
# ---------------------------------------------------------------------------

class Document(Base):
    __tablename__ = "documents"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    filename: Mapped[str] = mapped_column(Text, nullable=False)
    file_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    file_type: Mapped[str | None] = mapped_column(Text)
    size_bytes: Mapped[int | None] = mapped_column(BigInteger)
    page_count: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="queued")
    error_code: Mapped[str | None] = mapped_column(Text)
    error: Mapped[str | None] = mapped_column(Text)
    current_node: Mapped[str | None] = mapped_column(Text)
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    tier_summary: Mapped[dict | None] = mapped_column(JSONB)
    timings: Mapped[dict | None] = mapped_column(JSONB)
    llm_usage: Mapped[dict | None] = mapped_column(JSONB)
    pipeline_version: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(nullable=False, default=_now)
    updated_at: Mapped[datetime] = mapped_column(nullable=False, default=_now, onupdate=_now)
    finished_at: Mapped[datetime | None] = mapped_column(nullable=True)

    invoices: Mapped[list[Invoice]] = relationship(
        "Invoice", back_populates="document", cascade="all, delete-orphan",
        foreign_keys="Invoice.document_id",
    )
    pages: Mapped[list[DocumentPage]] = relationship(
        "DocumentPage", back_populates="document", cascade="all, delete-orphan"
    )
    chunks: Mapped[list[Chunk]] = relationship(
        "Chunk", back_populates="document", cascade="all, delete-orphan"
    )
    events: Mapped[list[ProcessingEvent]] = relationship("ProcessingEvent", back_populates="document")


# ---------------------------------------------------------------------------
# invoices
# ---------------------------------------------------------------------------

class Invoice(Base):
    __tablename__ = "invoices"
    __table_args__ = (
        Index("ix_invoices_supplier_name", "supplier_name"),
        Index("ix_invoices_invoice_date", "invoice_date"),
        Index("ix_invoices_status", "status"),
        Index("ix_invoices_document_id", "document_id"),
        # Partial unique: duplicates stored with dup_key=NULL and dup_of set
        Index(
            "uq_invoice_dupkey",
            "dup_key",
            unique=True,
            postgresql_where=text("dup_key IS NOT NULL AND status <> 'failed'"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    document_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("documents.id", ondelete="CASCADE"), nullable=False
    )
    unit_index: Mapped[int | None] = mapped_column(Integer)
    invoice_number: Mapped[str | None] = mapped_column(Text)
    invoice_date: Mapped[str | None] = mapped_column(Text)   # ISO date string
    due_date: Mapped[str | None] = mapped_column(Text)
    supplier_name: Mapped[str | None] = mapped_column(Text)
    customer_name: Mapped[str | None] = mapped_column(Text)
    subtotal: Mapped[float | None] = mapped_column(Numeric(18, 4))
    tax: Mapped[float | None] = mapped_column(Numeric(18, 4))
    total: Mapped[float | None] = mapped_column(Numeric(18, 4))
    currency: Mapped[str | None] = mapped_column(String(3))
    status: Mapped[str] = mapped_column(Text, nullable=False, default="needs_review")
    corrected: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    tier: Mapped[str | None] = mapped_column(Text)
    uncertain_fields: Mapped[list | None] = mapped_column(JSONB)
    raw: Mapped[dict | None] = mapped_column(JSONB)
    extra: Mapped[dict | None] = mapped_column(JSONB)
    derived: Mapped[dict | None] = mapped_column(JSONB)
    source_pages: Mapped[list | None] = mapped_column(ARRAY(Integer))
    source_rows: Mapped[list | None] = mapped_column(ARRAY(Integer))
    alternatives: Mapped[list | None] = mapped_column(JSONB)
    dup_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    dup_of: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("invoices.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(nullable=False, default=_now)
    updated_at: Mapped[datetime] = mapped_column(nullable=False, default=_now, onupdate=_now)

    document: Mapped[Document] = relationship(
        "Document", back_populates="invoices", foreign_keys=[document_id]
    )
    line_items: Mapped[list[LineItem]] = relationship(
        "LineItem", back_populates="invoice", cascade="all, delete-orphan"
    )
    issues: Mapped[list[ValidationIssue]] = relationship(
        "ValidationIssue",
        back_populates="invoice",
        cascade="all, delete-orphan",
        foreign_keys="ValidationIssue.invoice_id",
        primaryjoin="Invoice.id == ValidationIssue.invoice_id",
    )
    summary_chunks: Mapped[list[Chunk]] = relationship(
        "Chunk", back_populates="invoice", cascade="all, delete-orphan"
    )
    corrections: Mapped[list[Correction]] = relationship("Correction", back_populates="invoice")


# ---------------------------------------------------------------------------
# line_items
# ---------------------------------------------------------------------------

class LineItem(Base):
    __tablename__ = "line_items"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    invoice_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("invoices.id", ondelete="CASCADE"), nullable=False
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    quantity: Mapped[float | None] = mapped_column(Numeric(18, 4))
    unit_price: Mapped[float | None] = mapped_column(Numeric(18, 4))
    amount: Mapped[float | None] = mapped_column(Numeric(18, 4))

    invoice: Mapped[Invoice] = relationship("Invoice", back_populates="line_items")


# ---------------------------------------------------------------------------
# validation_issues
# ---------------------------------------------------------------------------

class ValidationIssue(Base):
    __tablename__ = "validation_issues"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    document_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("documents.id", ondelete="CASCADE"), nullable=False
    )
    invoice_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("invoices.id", ondelete="CASCADE"), nullable=True
    )
    code: Mapped[str] = mapped_column(Text, nullable=False)
    severity: Mapped[str] = mapped_column(Text, nullable=False)
    field: Mapped[str | None] = mapped_column(Text)
    expected: Mapped[str | None] = mapped_column(Text)
    got: Mapped[str | None] = mapped_column(Text)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    location: Mapped[dict | None] = mapped_column(JSONB)
    resolved: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    invoice: Mapped[Invoice | None] = relationship(
        "Invoice",
        back_populates="issues",
        foreign_keys=[invoice_id],
        primaryjoin="ValidationIssue.invoice_id == Invoice.id",
    )


# ---------------------------------------------------------------------------
# document_pages
# ---------------------------------------------------------------------------

class DocumentPage(Base):
    __tablename__ = "document_pages"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    document_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("documents.id", ondelete="CASCADE"), nullable=False
    )
    page_no: Mapped[int] = mapped_column(Integer, nullable=False)
    text: Mapped[str | None] = mapped_column(Text)
    source: Mapped[str | None] = mapped_column(Text)
    ocr_conf: Mapped[float | None] = mapped_column(Float)

    document: Mapped[Document] = relationship("Document", back_populates="pages")


# ---------------------------------------------------------------------------
# chunks
# The tsv column is a generated column (server-side, STORED).
# The HNSW index on embedding requires pgvector 0.5+ (pg16 image has it).
# ---------------------------------------------------------------------------

class Chunk(Base):
    __tablename__ = "chunks"
    __table_args__ = (
        Index("ix_chunks_tsv_gin", "tsv", postgresql_using="gin"),
        Index(
            "ix_chunks_embedding_hnsw",
            "embedding",
            postgresql_using="hnsw",
            postgresql_with={"m": 16, "ef_construction": 64},
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    document_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("documents.id", ondelete="CASCADE"), nullable=False
    )
    invoice_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("invoices.id", ondelete="CASCADE"), nullable=True
    )
    kind: Mapped[str] = mapped_column(Text, nullable=False)  # 'page' | 'invoice_summary'
    page_no: Mapped[int | None] = mapped_column(Integer)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    # Server-generated tsvector; SQLAlchemy reflects it but never writes it
    tsv = Column(TSVECTOR, Computed("to_tsvector('simple', coalesce(text, ''))", persisted=True))
    embedding: Mapped[list | None] = mapped_column(Vector(768))
    indexed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    document: Mapped[Document] = relationship("Document", back_populates="chunks")
    invoice: Mapped[Invoice | None] = relationship("Invoice", back_populates="summary_chunks")


# ---------------------------------------------------------------------------
# processing_events
# ---------------------------------------------------------------------------

class ProcessingEvent(Base):
    __tablename__ = "processing_events"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    document_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("documents.id", ondelete="CASCADE"), nullable=False
    )
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    node: Mapped[str] = mapped_column(Text, nullable=False)
    seconds: Mapped[float | None] = mapped_column(Float)
    ok: Mapped[bool | None] = mapped_column(Boolean)
    meta: Mapped[dict | None] = mapped_column(JSONB)
    at: Mapped[datetime] = mapped_column(nullable=False, default=_now)

    document: Mapped[Document] = relationship("Document", back_populates="events")


# ---------------------------------------------------------------------------
# corrections
# ---------------------------------------------------------------------------

class Correction(Base):
    __tablename__ = "corrections"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    invoice_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("invoices.id", ondelete="CASCADE"), nullable=False
    )
    field: Mapped[str] = mapped_column(Text, nullable=False)
    old_value: Mapped[str | None] = mapped_column(Text)
    new_value: Mapped[str | None] = mapped_column(Text)
    at: Mapped[datetime] = mapped_column(nullable=False, default=_now)

    invoice: Mapped[Invoice] = relationship("Invoice", back_populates="corrections")


# ---------------------------------------------------------------------------
# sheet_mapping_cache (persistent column-layout cache; keyed by header_hash)
# ---------------------------------------------------------------------------

class SheetMappingCache(Base):
    __tablename__ = "sheet_mapping_cache"

    header_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    mapping_json: Mapped[dict] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(nullable=False, default=_now)
    last_used_at: Mapped[datetime] = mapped_column(nullable=False, default=_now)
