# 09. AI-assisted development disclosure

This project was built with AI assistance throughout. This page says what the AI did, what I did, and where it went wrong.

## Tools used
- **Claude (chat):** assignment analysis, architecture, planning docs (`docs/00`-`docs/08`) and the graph skeleton (`reference/graph_skeleton/`). Planning chat: Nov 1-2, 2026 (long; export not included in the repo).
- **Claude Code:** implementation of every milestone M0-M11 (nodes, services, API, workers, frontend, evaluation tooling, these docs), working plan-first and test-first as set out in `CLAUDE.md`.
- **Gemini (Google):** the models inside the product (extraction, vision, sheet-column mapping, embeddings). Not a development tool.

## What AI produced vs what I did
| Area | AI contribution | My contribution |
|---|---|---|
| Requirements and scope | summarised and structured the assignment | set scope and priorities, approved or amended every plan |
| Architecture | proposed the tiered pipeline, graph, layering, decision log | reviewed the design; chose which trade-offs to accept |
| Code | wrote the first drafts of all modules from the node specs | approved each plan before implementation; ran the app and live checks with my own API key; decided what to cut |
| Tests | wrote tests from the specs before the code (red then green) | ran the suite; asked for end-to-end and live verification |
| Evaluation | wrote the fixture generator, ground truth and `scripts/evaluate.py` | decided the sample/quota strategy; the root-cause analysis of the errors in the report is mine to write |
| Documentation | drafted docs and this README from real files and run output | reviewed for accuracy |

<!-- AUTHOR: replace the right-hand column with specifics (what you changed or rewrote by hand). Only you know this. -->

## Verification
- Every node has tests derived from its spec. `pytest -m "not live"` is offline (a `FakeLLM` is injected; no network). Output saved in `docs/test-results.txt`.
- `test_architecture` enforces that `pipeline/` imports no db, api, services, workers, sqlalchemy, fastapi, celery or redis.
- Evaluation metrics come from `scripts/evaluate.py` running the real pipeline, not from hand-written numbers. The dataset is synthetic and the run is partial; see `docs/evaluation-report.md`.

## LLM use inside the product
Gemini models for extraction, vision, sheet-column mapping and embeddings; temperature 0; prompts say "return null rather than guess"; validation and grounding are plain code, independent of the LLM. The LLM never does arithmetic. Prompts are in `backend/app/pipeline/llm/prompts.py` with a version constant.

## Real problems AI-written code had (found by tests, live runs or evaluation)
- **Gemini rejected the sheet-mapping schema** (HTTP 400, empty-string enum value in `SheetMapping.thousands_separator`). Offline tests with a fake LLM could not catch it; the first evaluation of a live odd-header sheet did. Fixed in `f19f136` with a regression test.
- **DB service bugs found only against a real Postgres** (M8a-fix): a vector cast in search, string vs date types, and a missing savepoint around the invoice insert.
- **Wrong embedding dimension from the Gemini API** (M8b-D), found in the first live run; indexing was hardened against wrong-size vectors.
- **Evaluation tooling bugs caught while building it (M10):** a 5,000-row performance sheet swamped the review rate and tier shares until files without ground truth were excluded; the line-matching tie-break used a wrong sort key; 404 "model not available" errors were first counted as pipeline results instead of being classified as an LLM/config problem.
- **Model names:** the planned `gemini-2.5-*` models return 404 for newly created API keys, so the working config uses `gemini-3.5-flash`.
- **Findings the evaluation surfaced, not yet fixed** (listed in the README under known limitations).

## Prompt log (`docs/prompts-log.md`)
Append after every milestone:
```
## M3: PDF path   (2026-11-02 15:10)
Tool: Claude Code   Model: <as shown in the UI>
Prompt: <exact prompt text>
Outcome: <one line: what was produced, what you changed afterwards>
```
Also include: the planning chat (export or link; mention it is long), the product prompts (`extract-v1`, `vision-v1`, `sheet-map-v1`) and any revisions with the reason.
Start the file now with this planning session: "Planning with Claude chat: assignment analysis, architecture, docs; Nov 1-2".
