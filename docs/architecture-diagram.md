# Architecture diagrams

Sources: [`assets/system.mermaid`](assets/system.mermaid) and [`assets/graph.mermaid`](assets/graph.mermaid).
Dependencies point one way: `frontend -> api -> services -> pipeline / db`. `pipeline/` imports nothing
from db, api, services or workers; `backend/tests/unit/test_architecture.py` enforces that.

## System

```mermaid
flowchart LR
  U[Browser<br/>Next.js frontend] -->|HTTP /api| API[FastAPI<br/>routes + validation]
  API --> SVC[services<br/>ingest, processing, corrections,<br/>indexing, search]
  API -->|enqueue| R[(Redis)]
  R --> W[Celery worker<br/>threads pool]
  W --> SVC
  SVC --> P[pipeline<br/>pure LangGraph:<br/>file in, result out]
  SVC --> DB[(PostgreSQL 16<br/>+ pgvector)]
  P -->|text / vision / mapping| G[Gemini API]
  SVC -->|embeddings| G
  SVC --> FS[(data/uploads<br/>stored by SHA-256)]
```

## Pipeline graph

Regenerate with `python -m scripts.draw_graph` from `backend/` (writes `assets/graph.mermaid`; the PNG
needs Graphviz `dot`). The `reference/graph_skeleton/graph.png` file is the original skeleton, not the final graph.

```mermaid
---
config:
  flowchart:
    curve: linear
---
graph TD;
	__start__([<p>__start__</p>]):::first
	intake(intake)
	pdf_triage(pdf_triage)
	pdf_text(pdf_text)
	image_prep(image_prep)
	ocr(ocr)
	sheet_load(sheet_load)
	sheet_classify(sheet_classify)
	sheet_map(sheet_map)
	sheet_transform(sheet_transform)
	segment(segment)
	llm_extract(llm_extract)
	validate(validate)
	vision_extract(vision_extract)
	finalize(finalize)
	fail(fail)
	__end__([<p>__end__</p>]):::last
	__start__ --> intake;
	image_prep --> ocr;
	intake -.-> fail;
	intake -.-> image_prep;
	intake -.-> pdf_triage;
	intake -.-> sheet_load;
	llm_extract --> validate;
	ocr --> segment;
	pdf_text -.-> image_prep;
	pdf_text -.-> segment;
	pdf_triage --> pdf_text;
	segment --> llm_extract;
	sheet_classify -.-> segment;
	sheet_classify -.-> sheet_map;
	sheet_load --> sheet_classify;
	sheet_map --> sheet_transform;
	sheet_transform -.-> finalize;
	sheet_transform -.-> sheet_map;
	sheet_transform -.-> validate;
	validate -.-> finalize;
	validate -.-> vision_extract;
	vision_extract --> validate;
	fail --> __end__;
	finalize --> __end__;
	classDef default fill:#f2f0ff,line-height:1.2
	classDef first fill-opacity:0
	classDef last fill:#bfb6fc
```
