"""Renders the COMPILED graph (not a hand-drawn copy) to .mermaid and .png."""
import subprocess
from graph import build_graph

app = build_graph()
g = app.get_graph()
open("graph.mermaid", "w").write(g.draw_mermaid())

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
L = ["digraph G {", 'rankdir=TB; nodesep=0.45; ranksep=0.55; dpi=150; bgcolor="white";',
     'node [shape=box, style="rounded,filled", fontname="Helvetica", fontsize=11, color="#555555", margin="0.18,0.08"];',
     'edge [fontname="Helvetica", fontsize=9, color="#666666", arrowsize=0.7];']
for n in g.nodes:
    if n == "__start__":
        L.append('"__start__" [label="START", shape=circle, fillcolor="#444444", fontcolor="white", width=0.55];')
    elif n == "__end__":
        L.append('"__end__" [label="END", shape=doublecircle, fillcolor="#444444", fontcolor="white", width=0.55];')
    else:
        L.append(f'"{n}" [fillcolor="{STAGE[n]}"];')
for e in g.edges:
    attrs = []
    if e.conditional:
        attrs.append('style=dashed')
    lab = LABEL.get((e.source, e.target))
    if lab:
        attrs.append(f'label="{lab}"')
    L.append(f'"{e.source}" -> "{e.target}" [{", ".join(attrs)}];')
L.append("}")
open("graph.dot", "w").write("\n".join(L))
subprocess.run(["dot", "-Tpng", "graph.dot", "-o", "graph.png"], check=True)
print("nodes:", len(g.nodes), "edges:", len(g.edges), "conditional:", sum(e.conditional for e in g.edges))
