# Document Intelligence: invoice extraction pipeline

Care Guide AI Engineer assignment. Ingest PDF/JPG/PNG/TIFF/XLSX/XLS/CSV, extract invoice
fields to JSON (null when unsure, never invented), validate with plain code, flag problems
for manual review, store everything, make it searchable. Deadline: **Nov 4, 2026, 11 PM**
(send before 6 PM).

## Stack
Python 3.11+, LangGraph, Pydantic v2, PyMuPDF (+pdfplumber fallback), RapidOCR (+Tesseract
fallback), pandas/openpyxl/xlrd, Gemini via LangChain (`with_structured_output`),
FastAPI, Celery+Redis, PostgreSQL+pgvector, Next.js (TypeScript). Windows dev machine.

## Commands (PowerShell, from `backend/`)
```
pip install -r requirements.txt
pytest -m "not live"                 # fast, offline, uses FakeLLM. Run before saying "done"
pytest -m live                       # real LLM, a handful of calls
python -m scripts.run_pipeline data/fixtures/*      # terminal proof-of-work table
python -m scripts.generate_fixtures --seed 42 --out data/fixtures
python -m scripts.evaluate --fixtures data/fixtures --out eval/reports/latest
docker compose up -d                 # postgres(pgvector) + redis only
uvicorn app.api.main:app --reload
celery -A app.workers.celery_app worker --pool=threads --concurrency=4 --loglevel=info
```

## Working rules (hard)
1. **One task = one node or module.** Read `docs/nodes/<node>.md` first. Do not edit other
   nodes. If the spec is wrong or ambiguous, stop and ask; do not improvise.
2. **Plan mode first.** Propose a plan, wait for approval, then implement.
3. **Tests first.** Write the tests listed in the node spec (red), then implement (green).
   Run `pytest -m "not live"` and show the output before declaring done.
4. **Never invent values.** Every extracted field is optional and defaults to `None`.
   Prompts must say "return null rather than guess". Missing stays null; validation flags it.
5. **Money is `Decimal`, never `float`.** Parse only via `pipeline/utils/money.py`.
   The LLM never does arithmetic.
6. **`pipeline/` is pure.** No imports of `db`, `api`, `services`, `workers`, `sqlalchemy`,
   `fastapi`, `celery`, `redis`. Enforced by `tests/unit/test_architecture.py`.
7. **Expected failures are issues, not exceptions.** Nodes append issues and keep going.
   Only unexpected bugs raise; the `@node` wrapper catches them.
8. **All LLM calls go through `pipeline/llm/client.py`.** Tests inject `FakeLLM`. No network
   in non-live tests.
9. **Prompts live in `pipeline/llm/prompts.py`** with a version constant. Change = bump version.
10. **Config via `pipeline/config.py`** (env-overridable). No magic numbers in nodes.
11. **No secrets in the repo.** `.env` is git-ignored; keep `.env.example` current.
12. **Commit after every green milestone** (`M3: pdf path`). At the end of each milestone,
    append the prompt you were given and a one-line outcome to `docs/prompts-log.md`.
13. New dependency = add to `requirements.txt` and one line to the decision log in
    `docs/01-architecture.md`.

## Docs index
- `docs/00-skeleton-review.md`  bugs/gaps in the starting `graph.py`. Fix in M0.
- `docs/01-architecture.md`     graph, data flow, layers, decisions
- `docs/02-schemas.md`          Pydantic models, statuses, issue codes, result shape
- `docs/03-config-llm.md`       config table, LLM client, prompts, retry policy
- `docs/nodes/*.md`             one spec per node (15)
- `docs/04-validation.md`       rule catalogue and normalisation rules
- `docs/05-testing-eval.md`     fixtures, ground truth, metrics, report
- `docs/06-api-db.md`           DB schema, endpoints, workers, search, security
- `docs/07-frontend.md`         screens and behaviour
- `docs/08-build-plan.md`       milestones with acceptance criteria and prompts
- `docs/09-ai-disclosure.md`    AI-assisted development disclosure + prompt log format
- `reference/graph_skeleton/`   starting skeleton (stubs + dry-run harness + diagram)

## Definition of done (any milestone)
Tests for the spec pass; `pytest -m "not live"` is green; architecture test is green;
no TODOs left in touched files; build-plan checkbox ticked; committed; prompts-log updated.
