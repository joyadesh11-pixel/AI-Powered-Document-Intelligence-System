# 08. Build plan (tick the box when done, then commit)

Today is **Nov 2**. Hard deadline **Nov 4, 11 PM**; plan to send by **6 PM** so you have a buffer.
Targets: **Nov 2** = M0-M7 (pipeline works in the terminal). **Nov 3** = M8-M9 (API, worker, frontend) and a first M10 run. **Nov 4** = M10-M12 (real eval, docs, video, send).
If behind: cut in this order: frontend polish -> search filters -> API rate limit -> `CONTEXT_TRUNCATED` chunking -> Tesseract fallback. **Never cut:** evaluation numbers, tests for each component, AI disclosure, the video.

## Per-milestone routine (Claude Code)
`/clear` -> "Read CLAUDE.md and <the docs listed>. Plan mode: propose a plan for <milestone>." -> approve -> implement tests first -> run `pytest -m "not live"` -> tick box -> commit -> append prompt + outcome to `docs/prompts-log.md`.

## Milestones
- [x] **M0 Scaffold (30-45 min).** Repo layout from docs/01; copy `reference/graph_skeleton/` into `backend/app/pipeline/` and split into `state.py / routers.py / nodes/ / graph.py`; apply `docs/00-skeleton-review.md` items 1-13; `schemas/`, `config.py`, `utils/` stubs, `tests/fakes.py` (`FakeLLM`), `test_architecture.py`; the 12 dry-run scenarios still pass (+ the new sheet-no-vision case).
  *Accept:* `pytest` green, graph image regenerates. Docs: 00, 01, 02, 03.
- [x] **M1 Pure logic (60-75 min).** `utils/money.py, dates.py, text_norm.py, hashing.py`; `validation/*`; `validate` node. *Accept:* every test row in docs/04 passes; no `float`. Docs: 04, nodes/validate.
- [ ] **M2 Fixtures v0 + intake/fail (45 min).** `generate_fixtures.py` producing the small set (digital A/B, multi-page C, xlsx/csv, bad files) with ground truth; `intake`, `fail`. *Accept:* intake T1-T10. Docs: 05, nodes/intake, nodes/fail.
- [ ] **M3 PDF path (45 min).** `pdf_triage`, `pdf_text`. *Accept:* tests green; line items of clean fixtures reconstructable from tables. Docs: nodes/pdf_triage, pdf_text.
- [ ] **M4 Sheet path (75 min).** `sheet_load, sheet_classify, sheet_map, sheet_transform` with `FakeLLM` first, synonyms table. *Accept:* sheet tests + perf test numbers noted. Docs: nodes/sheet_*.
- [ ] **M5 Image path (60 min, hard cap 45 on OCR tuning).** `image_prep`, `ocr`. *Accept:* tests green; mean conf and sec/page noted. Docs: nodes/image_prep, ocr.
- [ ] **M6 LLM nodes (90 min).** `llm/client.py` (Gemini), `prompts.py`, `segment`, `llm_extract`, `vision_extract`, `finalize`. *Accept:* unit tests with `FakeLLM`; then `pytest -m live` 4-5 real calls pass. Docs: 03, nodes/segment, llm_extract, vision_extract, finalize.
- [ ] **M7 End to end in the terminal (60 min).** Remove `sim`; `scripts/run_pipeline.py` prints table (file, type, status, #invoices, issues, seconds, tier); integration tests with real fixtures + `FakeLLM`; full `generate_fixtures.py` (~60 files). *Accept:* **the terminal proof you wanted today**: `python -m scripts.run_pipeline data/fixtures/*` runs, statuses match ground truth for the clean set, no crash on bad files. Tag `v0.1-pipeline`.
- [ ] **M8 Persistence, API, workers (Nov 3 morning, 3 h).** `db/`, `services/`, `api/`, `workers/`, `docker-compose.yml` (postgres+pgvector, redis), `.env.example`. *Accept:* API tests green; `curl` upload -> poll -> result works with a real worker. Docs: 06.
- [ ] **M9 Frontend (Nov 3 afternoon, 3 h).** Docs: 07. *Accept:* the manual flow at the end of docs/07 works.
- [ ] **M10 Evaluation (Nov 3 evening first run, Nov 4 final).** `scripts/evaluate.py` + report. *Accept:* `eval/reports/<ts>/report.md` with all metrics from docs/05, error analysis written by you, honest limitations. Docs: 05.
- [ ] **M11 Documentation (Nov 4 midday, 2 h).** README (below), architecture diagram (regenerate `graph.png` + one system diagram), DB schema doc/diagram, test results (`pytest` output saved to `docs/test-results.txt`), `docs/evaluation-report.md`, `docs/09-ai-disclosure.md` finalised, `docs/prompts-log.md` complete.
- [ ] **M12 Video + submission (Nov 4 afternoon).** Record walkthrough; Drive link (anyone with link can view, test in incognito); submission email checklist at the bottom.

## README outline (what reviewers look for)
1. What it does + 30-second demo GIF/link. 2. Architecture diagram + data flow. 3. Setup (copy `.env.example`, `docker compose up -d`, `pip install`, run API/worker/frontend, run CLI). 4. API summary. 5. **Design decisions** (D1-D12 from docs/01, with reasons and alternatives). 6. **Reliability, scalability, security, performance** (concrete: async workers, caching, retries, limits, validation layers). 7. Testing (how to run, results). 8. Evaluation summary + link (dataset is synthetic; say it). 9. Known limitations + future work (docs/00, docs/01). 10. AI-assisted development disclosure (link). 11. Prior experience: one sentence linking your earlier LangGraph invoice pipeline.

## Claude Code prompts (copy per milestone; fill in names)
**Generic:** "Read CLAUDE.md, docs/01-architecture.md, docs/02-schemas.md and docs/nodes/<node>.md. Enter plan mode. Propose a short plan for <node> only: files to create/change, test list from the spec, risks. Do not modify other nodes. Wait for my approval."
After approval: "Implement. Write the tests from the spec first and show them failing, then implement until `pytest -m 'not live'` is green. Show the test output. Then update the checkbox in docs/08-build-plan.md and stop."
**M0:** "Read CLAUDE.md, docs/00-skeleton-review.md, docs/01, docs/02, docs/03 and reference/graph_skeleton/. Plan the scaffold and the 13 skeleton fixes. Do not implement real node logic."
**M6 live check:** "Run `pytest -m live`. Report each call's model, latency and tokens. Do not change prompts without telling me."
**M7:** "Remove the `sim` mechanism. Convert the 12 dry-run scenarios into integration tests using real fixtures and FakeLLM. Build run_pipeline.py. Show the final table."

## Submission email checklist (Reply All to the original thread)
- [ ] Reply All; `apply@careguidebd.com` still in recipients; subject unchanged ("Re: Interview Task Assignment").
- [ ] Portfolio (link), updated CV (education: institution, subject, year, result; work: company, position, responsibilities, duration), two professional references with official institutional/organisational emails (department head/supervisor + former HR contact).
- [ ] Assessment: repo link (public or access granted), README, architecture diagram, DB schema, evaluation report, video link, AI prompts disclosure.
- [ ] All Drive links open in incognito. Sent before 6 PM Nov 4 (deadline 11 PM; the email itself did not state a time zone).
- [ ] One sentence on prior LangGraph invoice-verification project, with link.
