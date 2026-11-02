# Prompts log

Format: `| milestone | prompt (summary) | outcome |`

| Milestone | Prompt | Outcome |
|---|---|---|
| M0 | Read CLAUDE.md, docs/00-skeleton-review.md, docs/01, docs/02, docs/03, docs/08 (M0 only), reference/graph_skeleton/*. Scaffold backend/ per docs/01, split skeleton into state.py/routers.py/nodes//graph.py, apply 13 fixes. Create schemas, config.py, utils/ stubs, tests/fakes.py, test_architecture.py, requirements.txt. 12 dry-run scenarios + xlsx-no-vision case must pass as pytest. Plan only first, then implement. | 44/44 pytest green (13 scenarios + 31 architecture checks). All 13 skeleton fixes applied. |
