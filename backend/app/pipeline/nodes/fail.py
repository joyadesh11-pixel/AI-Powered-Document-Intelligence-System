"""fail node — document-level failure, builds minimal PipelineResult.

Fix 13: sets error_code and builds a valid PipelineResult dict.
"""
from __future__ import annotations

from app.pipeline.state import State, node
from app.schemas.models import DocStatus, ErrorCode


@node("fail", skip_on_error=False)
def fail(s: State) -> dict:
    error_code = s.get("error_code") or ErrorCode.INTERNAL_ERROR.value
    result = {
        "status": DocStatus.failed.value,
        "error_code": error_code,
        "error": s.get("error"),
        "file_hash": s.get("file_hash", ""),
        "file_type": s.get("file_type", ""),
        "size_bytes": s.get("size_bytes", 0),
        "page_count": s.get("page_count", 0),
        "invoices": [],
        "warnings": [],
        "page_text": {},
        "page_source": {},
        "ocr_conf": {},
        "timings": {},
        "history": s.get("history", []),
        "llm_usage": {},
        "pipeline_version": "0.0.1-m0",
    }
    return {"status": DocStatus.failed.value, "result": result}
