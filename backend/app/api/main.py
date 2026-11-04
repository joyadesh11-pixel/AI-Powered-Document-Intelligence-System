"""FastAPI application factory.  Run: uvicorn app.api.main:app --reload"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import APIRouter, Depends, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from app.api.deps import require_api_key
from app.api.errors import install_error_handlers
from app.api.routes import documents, health, invoices, search
from app.db.base import create_all
from app.pipeline.config import get_config
from app.services.session import get_session_factory

log = logging.getLogger(__name__)

# db/models.py hard-codes Vector(768); fail loudly if config disagrees
_MODEL_EMBED_DIM = 768


@asynccontextmanager
async def _lifespan(_: FastAPI):
    cfg = get_config()
    if cfg.EMBED_DIM != _MODEL_EMBED_DIM:
        raise RuntimeError(
            f"EMBED_DIM={cfg.EMBED_DIM} but the chunks.embedding column is vector({_MODEL_EMBED_DIM})"
        )
    try:
        create_all(get_session_factory().kw["bind"])
    except Exception as exc:   # the API still starts so /health can report the outage
        log.error("Could not create tables at startup: %s", type(exc).__name__)
    yield


def create_app() -> FastAPI:
    cfg = get_config()
    app = FastAPI(title="Document Intelligence API", version="0.1.0", lifespan=_lifespan)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=[o.strip() for o in cfg.CORS_ORIGINS.split(",") if o.strip()],
        allow_methods=["GET", "POST", "PATCH"],
        allow_headers=["X-API-Key", "Content-Type"],
    )

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        return response

    api = APIRouter(prefix="/api", dependencies=[Depends(require_api_key)])
    for module in (documents, invoices, search, health):
        api.include_router(module.router)
    app.include_router(api)

    install_error_handlers(app)
    return app


app = create_app()
