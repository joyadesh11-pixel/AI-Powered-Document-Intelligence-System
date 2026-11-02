# 09. AI-assisted development disclosure (the assignment requires this; do not hide it)

## Template for the final disclosure (`docs/ai-disclosure.md` in the repo; fill in honestly)
**Tools used.** Claude (chat) for requirements analysis, architecture and planning, and for generating the planning docs and graph skeleton. Claude Code for implementation of nodes, tests and boilerplate. (Add any other tool: Copilot, Gemini, etc.)
**What AI produced vs what I did.**
| Area | AI contribution | My contribution |
|---|---|---|
| Requirements analysis | summarised and structured the assignment | chose scope cuts, decided priorities |
| Architecture | proposed tiered pipeline, graph, layering | reviewed each node, changed <list what you changed>, final decisions |
| Code | generated first drafts of <modules> | reviewed, ran, debugged, rewrote <modules> |
| Tests | drafted test cases from specs | verified assertions, added failure cases <list> |
| Evaluation | generated fixture/eval scripts | ran them, analysed errors, wrote the analysis |
| Documentation | drafted docs | edited for accuracy |
**Verification.** Every node has tests from its spec; metrics in the evaluation report are produced by `scripts/evaluate.py` from real runs, not written by hand; the dataset is synthetic and said so.
**LLM use inside the product.** Gemini models for extraction/vision/sheet-column mapping/embeddings; temperature 0; null-over-guess prompts; validation and grounding as independent checks. Prompts are in `pipeline/llm/prompts.py`, versions logged below.
**Known limits of AI-generated code here.** List real bugs AI introduced that you caught (be specific; this builds credibility).

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
