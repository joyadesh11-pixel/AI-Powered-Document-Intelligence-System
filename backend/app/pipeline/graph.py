"""Pipeline graph: build, process, stream.

Fix 6: dependency injection via build_graph(deps).
  - deps are closed over in router lambdas so they are NOT stored in State
    (State must stay JSON-serialisable).
  - process(file_path, *, deps, _sim) -> PipelineResult
  - stream_process(file_path, *, deps, _sim) -> Iterator[(node_name, state_update)]
  - _sim is a test-only kwarg (underscore prefix); removed with sim switches in M7.
  - _default_deps() lets dry-run tests work with no environment variables.
M5: image_prep is now a factory (make_image_prep); process() and stream_process() create
    a TemporaryDirectory and inject its path into deps["image_temp_dir"] so image_prep can
    write page PNGs into it.  The directory is cleaned up when the context manager exits.
"""
from __future__ import annotations

import tempfile
from typing import Any, Iterator

from langgraph.graph import END, START, StateGraph

from app.pipeline.config import Config, get_config
from app.pipeline.llm.client import LLMClient, LLMConfigError, RealLLMClient
from app.pipeline.state import Deps, State
from app.pipeline.routers import (
    route_after_intake,
    route_after_pdf_text,
    route_after_classify,
    route_after_transform,
    route_after_validate,
)
from app.pipeline.nodes.intake import make_intake
from app.pipeline.nodes.pdf_triage import pdf_triage
from app.pipeline.nodes.pdf_text import pdf_text
from app.pipeline.nodes.image_prep import make_image_prep
from app.pipeline.nodes.ocr import ocr
from app.pipeline.nodes.sheet_load import sheet_load
from app.pipeline.nodes.sheet_classify import sheet_classify
from app.pipeline.nodes.sheet_map import make_sheet_map
from app.pipeline.nodes.sheet_transform import sheet_transform
from app.pipeline.nodes.segment import segment
from app.pipeline.nodes.llm_extract import llm_extract
from app.pipeline.nodes.validate import make_validate
from app.pipeline.nodes.vision_extract import vision_extract
from app.pipeline.nodes.finalize import finalize
from app.pipeline.nodes.fail import fail
from app.schemas.models import PipelineResult


class NullLLM:
    """Default LLM when no deps are provided.
    Any real LLM call raises LLMConfigError so the document fails loudly.
    Tests that exercise LLM nodes must pass explicit deps with a FakeLLM."""
    def extract(self, schema, system, user, *, images=None, model):  # type: ignore[override]
        raise LLMConfigError(
            "No LLM configured. Pass deps=Deps(llm=FakeLLM([...])) in tests "
            "or set GOOGLE_API_KEY and use RealLLMClient in production."
        )


def _default_deps() -> Deps:
    return Deps(config=get_config(), llm=NullLLM(), seen_store=None, mapping_cache=None)


def build_graph(deps: Deps | None = None):
    """Compile the LangGraph pipeline. deps are closed over in routers."""
    cfg: Config = (deps or {}).get("config") or get_config()

    g = StateGraph(State)

    validate = make_validate(deps)
    intake = make_intake(deps)
    sheet_map = make_sheet_map(deps)
    image_prep = make_image_prep(deps)

    # register nodes
    for name, fn in [
        ("intake", intake), ("pdf_triage", pdf_triage), ("pdf_text", pdf_text),
        ("image_prep", image_prep), ("ocr", ocr),
        ("sheet_load", sheet_load), ("sheet_classify", sheet_classify),
        ("sheet_map", sheet_map), ("sheet_transform", sheet_transform),
        ("segment", segment), ("llm_extract", llm_extract),
        ("validate", validate), ("vision_extract", vision_extract),
        ("finalize", finalize), ("fail", fail),
    ]:
        g.add_node(name, fn)

    # edges
    g.add_edge(START, "intake")
    g.add_conditional_edges(
        "intake", route_after_intake,
        ["pdf_triage", "image_prep", "sheet_load", "fail"],
    )
    g.add_edge("pdf_triage", "pdf_text")
    g.add_conditional_edges("pdf_text", route_after_pdf_text, ["image_prep", "segment"])
    g.add_edge("image_prep", "ocr")
    g.add_edge("ocr", "segment")
    g.add_edge("sheet_load", "sheet_classify")
    g.add_conditional_edges("sheet_classify", route_after_classify, ["sheet_map", "segment"])
    g.add_edge("sheet_map", "sheet_transform")
    g.add_conditional_edges(
        "sheet_transform",
        lambda s: route_after_transform(s, cfg),
        ["validate", "sheet_map", "finalize"],
    )
    g.add_edge("segment", "llm_extract")
    g.add_edge("llm_extract", "validate")
    g.add_conditional_edges("validate", route_after_validate, ["vision_extract", "finalize"])
    g.add_edge("vision_extract", "validate")
    g.add_edge("finalize", END)
    g.add_edge("fail", END)

    return g.compile()


def _initial_state(file_path: str, _sim: dict | None) -> dict:
    return {"file_path": file_path, "sim": _sim or {}, "history": []}


def _build_result(final: State) -> PipelineResult:
    """Build a PipelineResult from the final graph state."""
    raw = final.get("result")
    if raw is None:
        # fallback for stubs that don't populate result
        raw = {
            "status": final.get("status", "failed"),
            "error_code": final.get("error_code"),
            "error": final.get("error"),
            "file_hash": final.get("file_hash", ""),
            "file_type": final.get("file_type", ""),
            "size_bytes": final.get("size_bytes", 0),
            "page_count": final.get("page_count", 0),
            "invoices": [],
            "warnings": [],
            "page_text": {},
            "page_source": {},
            "ocr_conf": {},
            "timings": {},
            "history": final.get("history", []),
            "llm_usage": {},
            "pipeline_version": "0.0.1-m0",
        }
    # Always use the fully-merged history from final state so nodes like fail
    # (which build result before the wrapper appends their own entry) are included.
    raw["history"] = final.get("history", [])
    return PipelineResult.model_validate(raw)


def process(
    file_path: str,
    *,
    deps: Deps | None = None,
    _sim: dict | None = None,   # test-only; removed in M7
) -> PipelineResult:
    """Run the pipeline synchronously and return a PipelineResult."""
    d = deps or _default_deps()
    with tempfile.TemporaryDirectory(prefix="dociq_imgp_") as tmp:
        d_with_tmp: Deps = {**d, "image_temp_dir": tmp}  # type: ignore[misc]
        graph = build_graph(d_with_tmp)
        final: State = graph.invoke(_initial_state(file_path, _sim))
        return _build_result(final)


def stream_process(
    file_path: str,
    *,
    deps: Deps | None = None,
    _sim: dict | None = None,
) -> Iterator[tuple[str, dict]]:
    """Stream (node_name, state_update) pairs as the graph runs."""
    d = deps or _default_deps()
    with tempfile.TemporaryDirectory(prefix="dociq_imgp_") as tmp:
        d_with_tmp: Deps = {**d, "image_temp_dir": tmp}  # type: ignore[misc]
        graph = build_graph(d_with_tmp)
        for update in graph.stream(_initial_state(file_path, _sim), stream_mode="updates"):
            for node_name, state_update in update.items():
                yield node_name, state_update
