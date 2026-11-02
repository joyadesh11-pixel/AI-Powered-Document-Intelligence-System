# Prompts log

Format: `| milestone | prompt (summary) | outcome |`

| Milestone | Prompt | Outcome |
|---|---|---|
| M0 | Read CLAUDE.md, docs/00-skeleton-review.md, docs/01, docs/02, docs/03, docs/08 (M0 only), reference/graph_skeleton/*. Scaffold backend/ per docs/01, split skeleton into state.py/routers.py/nodes//graph.py, apply 13 fixes. Create schemas, config.py, utils/ stubs, tests/fakes.py, test_architecture.py, requirements.txt. 12 dry-run scenarios + xlsx-no-vision case must pass as pytest. Plan only first, then implement. | 44/44 pytest green (13 scenarios + 31 architecture checks). All 13 skeleton fixes applied. |
| M1 | Read CLAUDE.md, docs/04-validation.md, docs/nodes/validate.md, docs/02-schemas.md, docs/03-config-llm.md. Edit docs (seen_store exclude_hash, escalation counts errors only, corrected-fields note). Implement utils/money.py, dates.py, text_norm.py, hashing.py; pipeline/validation/* (completeness, formats, arithmetic, grounding, duplicates, __init__ with run_all+ValidationCtx+ESCALATION_CODES+InMemorySeenStore); real validate node (make_validate factory); update stub nodes to emit complete Invoice dicts; update graph.py and finalize.py. Tests first (red), then green. No LLM, no DB. 13 scenarios must stay green. | 138/138 pytest green (13 scenarios + 37 arch + 88 new validate+utility tests). All doc edits applied. |
