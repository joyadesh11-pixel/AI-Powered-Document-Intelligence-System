"""Render the compiled pipeline graph to docs/assets/.

Writes graph.mermaid always.
Writes graph.png via Graphviz `dot` if available; prints a message if not.

Run from backend/:
    python -m scripts.draw_graph
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

# Output directory is docs/assets/ relative to the repo root (one level up from backend/).
BACKEND = Path(__file__).parent.parent
ASSETS = BACKEND.parent / "docs" / "assets"
ASSETS.mkdir(parents=True, exist_ok=True)

MERMAID_PATH = ASSETS / "graph.mermaid"
DOT_PATH = ASSETS / "graph.dot"
PNG_PATH = ASSETS / "graph.png"

STAGE = {
    "intake": "#E6F1FB", "pdf_triage": "#E6F1FB", "pdf_text": "#E6F1FB", "image_prep": "#E6F1FB",
    "ocr": "#E6F1FB", "sheet_load": "#E6F1FB", "sheet_classify": "#E6F1FB",
    "sheet_map": "#EEEDFE", "sheet_transform": "#EEEDFE", "segment": "#EEEDFE",
    "llm_extract": "#EEEDFE", "vision_extract": "#EEEDFE",
    "validate": "#E1F5EE", "finalize": "#E1F5EE", "fail": "#FCEBEB",
}
LABEL = {
    ("validate", "vision_extract"): "checks failed\n(once only)",
    ("sheet_transform", "sheet_map"): "parse < 95%\n(retry once)",
    ("sheet_transform", "finalize"): "retry used up",
    ("intake", "fail"): "corrupt /\nunsupported",
    ("pdf_text", "image_prep"): "scanned\npages",
    ("sheet_classify", "segment"): "free-form",
    ("sheet_classify", "sheet_map"): "tabular",
}


def main() -> None:
    from app.pipeline.graph import build_graph

    app = build_graph()
    g = app.get_graph()

    # Always write .mermaid
    MERMAID_PATH.write_text(g.draw_mermaid(), encoding="utf-8")
    print(f"wrote {MERMAID_PATH}")

    # Build .dot
    L = [
        "digraph G {",
        'rankdir=TB; nodesep=0.45; ranksep=0.55; dpi=150; bgcolor="white";',
        'node [shape=box, style="rounded,filled", fontname="Helvetica", fontsize=11, '
        'color="#555555", margin="0.18,0.08"];',
        'edge [fontname="Helvetica", fontsize=9, color="#666666", arrowsize=0.7];',
    ]
    for n in g.nodes:
        if n == "__start__":
            L.append('"__start__" [label="START", shape=circle, fillcolor="#444444", '
                     'fontcolor="white", width=0.55];')
        elif n == "__end__":
            L.append('"__end__" [label="END", shape=doublecircle, fillcolor="#444444", '
                     'fontcolor="white", width=0.55];')
        else:
            color = STAGE.get(n, "#FFFFFF")
            L.append(f'"{n}" [fillcolor="{color}"];')
    for e in g.edges:
        attrs = []
        if e.conditional:
            attrs.append("style=dashed")
        lab = LABEL.get((e.source, e.target))
        if lab:
            attrs.append(f'label="{lab}"')
        attr_str = ", ".join(attrs)
        L.append(f'"{e.source}" -> "{e.target}" [{attr_str}];')
    L.append("}")
    DOT_PATH.write_text("\n".join(L), encoding="utf-8")
    print(f"wrote {DOT_PATH}")

    # Attempt PNG via dot (optional — install Graphviz to enable)
    try:
        result = subprocess.run(
            ["dot", "-Tpng", str(DOT_PATH), "-o", str(PNG_PATH)],
            capture_output=True,
        )
        if result.returncode == 0:
            print(f"wrote {PNG_PATH}")
        else:
            print(f"dot exited {result.returncode}: {result.stderr.decode()}")
    except FileNotFoundError:
        print(
            f"Graphviz `dot` not found — install Graphviz to generate {PNG_PATH}\n"
            f"  .mermaid at {MERMAID_PATH} can be rendered at https://mermaid.live"
        )

    print(
        f"nodes: {len(g.nodes)}  "
        f"edges: {len(g.edges)}  "
        f"conditional: {sum(e.conditional for e in g.edges)}"
    )


if __name__ == "__main__":
    main()
