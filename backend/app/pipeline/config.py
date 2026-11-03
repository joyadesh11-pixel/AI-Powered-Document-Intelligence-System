"""Pipeline configuration.

All values have defaults from docs/03. Override via environment variables.
The dataclass is frozen so nodes treat it as read-only.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from decimal import Decimal


def _env(key: str, default: str) -> str:
    return os.environ.get(key, default)


def _env_int(key: str, default: int) -> int:
    return int(os.environ.get(key, default))


def _env_float(key: str, default: float) -> float:
    return float(os.environ.get(key, default))


@dataclass(frozen=True)
class Config:
    # --- intake / limits
    MAX_FILE_MB: int = 25
    MAX_PAGES: int = 1000
    MAX_UNZIPPED_MB: int = 200

    # --- pdf_triage
    PAGE_MIN_CHARS: int = 50
    GARBLED_RATIO_MAX: float = 0.20
    IMG_COVER_MAX: float = 0.85
    SCAN_TEXT_MAX_CHARS: int = 200
    TABULAR_MIN_LINES: int = 6
    TABULAR_MIN_NUMERIC_TOKENS: int = 2

    # --- image_prep / ocr
    OCR_DPI: int = 250
    OCR_MAX_SIDE_PX: int = 3500
    BLUR_VAR_MIN: float = 60.0
    MIN_SIDE_PX: int = 600
    OCR_CONF_MIN: float = 0.80
    OCR_WORKERS: int = 2

    # --- vision_extract
    VISION_MAX_SIDE_PX: int = 2000
    VISION_RENDER_DPI: int = 150
    VISION_MAX_PAGES: int = 6

    # --- sheet_load
    HEADER_SCAN_ROWS: int = 30

    # --- sheet_transform
    PARSE_RATE_MIN: float = 0.95

    # --- routers  (Fix 11: explicit constant used in route_after_transform)
    MAPPING_MAX_RETRIES: int = 1

    # --- llm_extract
    MAX_CONTEXT_CHARS: int = 60_000
    LLM_CONCURRENCY: int = 4

    # --- llm client retry
    LLM_MAX_SCHEMA_RETRIES: int = 2
    LLM_MAX_TRANSIENT_RETRIES: int = 3

    # --- validate
    TOL_ABS: Decimal = field(default_factory=lambda: Decimal("0.01"))
    DATE_MIN: str = "2000-01-01"
    DATE_MAX_DAYS_AHEAD: int = 365
    GROUND_FUZZY_MIN: int = 90

    # --- models (names are env-overridable; assumed correct on build day)
    LLM_MODEL_TEXT: str = "gemini-2.5-flash"
    LLM_MODEL_VISION: str = "gemini-2.5-pro"
    EMBED_MODEL: str = "gemini-embedding-001"
    EMBED_DIM: int = 768

    # --- persistence (services layer only; pipeline is pure)
    DATABASE_URL: str = "postgresql+psycopg2://dociq:dociq@localhost:5432/dociq"
    REDIS_URL: str = "redis://localhost:6379/0"


def get_config() -> Config:
    """Return a Config instance populated from environment variables."""
    return Config(
        MAX_FILE_MB=_env_int("MAX_FILE_MB", 25),
        MAX_PAGES=_env_int("MAX_PAGES", 1000),
        MAX_UNZIPPED_MB=_env_int("MAX_UNZIPPED_MB", 200),
        PAGE_MIN_CHARS=_env_int("PAGE_MIN_CHARS", 50),
        GARBLED_RATIO_MAX=_env_float("GARBLED_RATIO_MAX", 0.20),
        IMG_COVER_MAX=_env_float("IMG_COVER_MAX", 0.85),
        SCAN_TEXT_MAX_CHARS=_env_int("SCAN_TEXT_MAX_CHARS", 200),
        TABULAR_MIN_LINES=_env_int("TABULAR_MIN_LINES", 6),
        TABULAR_MIN_NUMERIC_TOKENS=_env_int("TABULAR_MIN_NUMERIC_TOKENS", 2),
        OCR_DPI=_env_int("OCR_DPI", 250),
        OCR_MAX_SIDE_PX=_env_int("OCR_MAX_SIDE_PX", 3500),
        BLUR_VAR_MIN=_env_float("BLUR_VAR_MIN", 60.0),
        MIN_SIDE_PX=_env_int("MIN_SIDE_PX", 600),
        OCR_CONF_MIN=_env_float("OCR_CONF_MIN", 0.80),
        OCR_WORKERS=_env_int("OCR_WORKERS", 2),
        VISION_MAX_SIDE_PX=_env_int("VISION_MAX_SIDE_PX", 2000),
        VISION_RENDER_DPI=_env_int("VISION_RENDER_DPI", 150),
        VISION_MAX_PAGES=_env_int("VISION_MAX_PAGES", 6),
        HEADER_SCAN_ROWS=_env_int("HEADER_SCAN_ROWS", 30),
        PARSE_RATE_MIN=_env_float("PARSE_RATE_MIN", 0.95),
        MAPPING_MAX_RETRIES=_env_int("MAPPING_MAX_RETRIES", 1),
        MAX_CONTEXT_CHARS=_env_int("MAX_CONTEXT_CHARS", 60_000),
        LLM_CONCURRENCY=_env_int("LLM_CONCURRENCY", 4),
        LLM_MAX_SCHEMA_RETRIES=_env_int("LLM_MAX_SCHEMA_RETRIES", 2),
        LLM_MAX_TRANSIENT_RETRIES=_env_int("LLM_MAX_TRANSIENT_RETRIES", 3),
        TOL_ABS=Decimal(os.environ.get("TOL_ABS", "0.01")),
        DATE_MIN=_env("DATE_MIN", "2000-01-01"),
        DATE_MAX_DAYS_AHEAD=_env_int("DATE_MAX_DAYS_AHEAD", 365),
        GROUND_FUZZY_MIN=_env_int("GROUND_FUZZY_MIN", 90),
        LLM_MODEL_TEXT=_env("LLM_MODEL_TEXT", "gemini-2.5-flash"),
        LLM_MODEL_VISION=_env("LLM_MODEL_VISION", "gemini-2.5-pro"),
        EMBED_MODEL=_env("EMBED_MODEL", "gemini-embedding-001"),
        EMBED_DIM=_env_int("EMBED_DIM", 768),
        DATABASE_URL=_env("DATABASE_URL", "postgresql+psycopg2://dociq:dociq@localhost:5432/dociq"),
        REDIS_URL=_env("REDIS_URL", "redis://localhost:6379/0"),
    )
