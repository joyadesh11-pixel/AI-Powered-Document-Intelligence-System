# Node specs

One file per node, 1:1 with `backend/app/pipeline/nodes/<node>.py` and
`backend/tests/unit/test_<node>.py`. Same template everywhere:
Purpose · Reads/Writes · Algorithm · Fallbacks · Failure behaviour · Edge cases · Tests · Done when.
Test IDs are `T<n>`; fixture names refer to files made by `scripts/generate_fixtures.py`
(or hand-made in `tests/fixtures_small/` for unit tests). Config names are in `docs/03-config-llm.md`.
Every node is wrapped by `@node(name)` (timing, exception capture, skip-after-error).
