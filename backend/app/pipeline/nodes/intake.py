"""intake node — classify file type, validate constraints.

Fix 7: sets both error_code and error on failure.
Stub: real logic (magic bytes, sha256, size check, encrypted) implemented in M2.
"""
from __future__ import annotations

from pathlib import Path

from app.pipeline.state import SHEET_EXT, IMAGE_EXT, State, node
from app.schemas.models import ErrorCode


@node("intake")
def intake(s: State) -> dict:
    sim = s.get("sim", {})
    ext = Path(s["file_path"]).suffix.lower()

    if sim.get("corrupt"):
        return {
            "error_code": ErrorCode.CORRUPT_FILE.value,
            "error": "intake: corrupt or unreadable file",
            "status": "failed",
        }
    if ext == ".pdf":
        return {"file_type": "pdf", "page_count": sim.get("pages", 3),
                "size_bytes": sim.get("size_bytes", 1024), "file_hash": "stub_hash"}
    if ext in IMAGE_EXT:
        return {"file_type": "image", "page_count": 1,
                "size_bytes": sim.get("size_bytes", 1024), "file_hash": "stub_hash"}
    if ext in SHEET_EXT:
        return {"file_type": "sheet", "page_count": 0,
                "size_bytes": sim.get("size_bytes", 1024), "file_hash": "stub_hash"}
    return {
        "error_code": ErrorCode.UNSUPPORTED_TYPE.value,
        "error": f"intake: unsupported file type {ext!r}",
        "status": "failed",
    }
