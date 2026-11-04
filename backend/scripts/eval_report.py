"""Write results.json, per_file.csv, confusion.csv and report.md from computed metrics (M10)."""
from __future__ import annotations

import csv
import json
from pathlib import Path

from scripts.eval_metrics import FIELDS, LINE_ATTRS, OUTCOMES, STATUSES

DOC05_GROUP = {
    "digital_pdf": "Digital PDF", "scanned_pdf": "Scanned PDF", "image_m5": "Images", "image": "Images",
    "xlsx": "XLSX", "sheet_m4": "XLSX/CSV (sheet edge cases)", "xls": "XLS", "csv": "CSV",
    "multi_invoice": "Multi-invoice PDF", "injected": "Injected errors", "bad_files": "Bad files",
    "sheet_m4_perf": "Large sheet", "triage": "PDF triage edge cases", "edge": "Intake edge cases",
}


def pct(x, nd: int = 1) -> str:
    return "n/a" if x is None else f"{x * 100:.{nd}f}%"


def sec(x) -> str:
    return "n/a" if x is None else f"{x:.2f}"


def _table(headers: list[str], rows: list[list]) -> str:
    out = ["| " + " | ".join(headers) + " |", "|" + "|".join("---" for _ in headers) + "|"]
    out += ["| " + " | ".join(str(c) for c in r) + " |" for r in rows]
    return "\n".join(out)


def _cell(v) -> str:
    return "null" if v is None else str(v).replace("|", "\\|").replace("\n", " ")


def write_csvs(out: Path, records: dict, gt_entries: list[dict], metrics: dict) -> None:
    cols = ["file", "group", "state", "reason", "expect_status", "status", "error_code", "file_type",
            "seconds", "n_gt_invoices", "n_pred_invoices", "fully_correct_invoices", "silent_errors",
            "tier", "llm_calls", "input_tokens", "output_tokens", "issue_codes"]
    with open(out / "per_file.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=cols)
        w.writeheader()
        for g in gt_entries:
            r = records.get(g["file"], {})
            pf = metrics["per_file"].get(g["file"], {})
            w.writerow({
                "file": g["file"], "group": g["group"], "state": r.get("state"), "reason": r.get("reason"),
                "expect_status": g["expect_status"], "status": r.get("status"),
                "error_code": r.get("error_code"), "file_type": r.get("file_type"),
                "seconds": r.get("seconds"), "n_gt_invoices": len(g.get("invoices") or []),
                "n_pred_invoices": len(r.get("invoices") or []),
                "fully_correct_invoices": pf.get("fully_correct_invoices"),
                "silent_errors": pf.get("silent_errors"), "tier": pf.get("tier"),
                "llm_calls": r.get("llm_calls"), "input_tokens": r.get("input_tokens"),
                "output_tokens": r.get("output_tokens"), "issue_codes": ";".join(pf.get("issue_codes") or []),
            })
    with open(out / "confusion.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["expected \\ actual", *STATUSES])
        for e in STATUSES:
            w.writerow([e, *[metrics["status"]["matrix"][e][a] for a in STATUSES]])


def render_report(meta: dict, metrics: dict, records: dict, gt_entries: list[dict], extras: dict,
                  commands: list[str]) -> str:
    M = metrics
    cov = M["coverage"]
    n_files = len(gt_entries)
    n_gt_inv = sum(len(g.get("invoices") or []) for g in gt_entries)
    L: list[str] = []
    A = L.append

    A("# Evaluation report\n")
    A("## 1. Setup\n")
    A(f"- **Dataset: synthetic.** {n_files} generated fixtures with {n_gt_inv} ground-truth invoices "
      f"(`scripts/generate_fixtures.py --seed {meta['fixtures_seed']}`). Ground truth is known by construction. "
      "Scans are produced by rasterising generated PDFs and degrading them (blur, rotation, noise, JPEG artefacts).")
    A(f"- **Execution mode:** {meta['mode']}. Upload limits, auth, DB persistence and worker concurrency "
      "are therefore *not* part of these numbers.")
    A(f"- **Models:** text `{meta['models']['text']}`, vision `{meta['models']['vision']}` "
      f"(LLM concurrency per document: {meta['llm_concurrency']}).")
    A(f"- **Machine:** {meta['machine']}; Python {meta['python']}.")
    A("- **Packages:** " + ", ".join(f"{k} {v}" for k, v in meta["packages"].items()) + ".")
    A(f"- **Date:** {meta['date']}; **commit:** `{meta['commit']}`.")
    counts = cov["counts"]
    A(f"- **Subset that ran:** {cov['n_ran']} of {n_files} files ran "
      f"({cov['n_scored']} are scored for accuracy). "
      + ", ".join(f"{k}: {v}" for k, v in sorted(counts.items())) + ".")
    A("- **Every rate below is computed only over files that ran** (state `ran`). Skipped files are listed in section 2.\n")

    A("## 2. Coverage: which files ran, which were skipped and why\n")
    by_group: dict[str, dict] = {}
    for row in cov["files"]:
        d = by_group.setdefault(row["group"], {})
        d[row["state"]] = d.get(row["state"], 0) + 1
    states = sorted({s for d in by_group.values() for s in d})
    A(_table(["group", "docs/05 group", *states],
             [[g, DOC05_GROUP.get(g, "-"), *[by_group[g].get(s, 0) for s in states]] for g in sorted(by_group)]))
    A("\n<details><summary>Per-file state</summary>\n")
    A(_table(["file", "group", "state", "reason"],
             [[r["file"], r["group"], r["state"], _cell(r["reason"])] for r in cov["files"]]))
    A("\n</details>\n")

    A("## 3. Results\n")
    A("### 1. Header field accuracy (matched invoices; scored files only)\n")
    rows = []
    for f in FIELDS:
        c = M["header"]["fields"][f]
        rows.append([f, c["n"], pct(c["accuracy"]), *[f"{c[o]} ({pct(c[o + '_rate'])})" for o in OUTCOMES]])
    mic = M["header"]["micro"]
    rows.append(["**micro average**", mic["n"], pct(mic["accuracy"]),
                 *[f"{mic[o]} ({pct(mic[o + '_rate'])})" for o in OUTCOMES]])
    A(_table(["field", "n", "accuracy", *OUTCOMES], rows))
    A("\nAccuracy = (correct + correct-null) / n. A missed invoice counts as an omission for each non-null GT field. "
      "Null-GT fields are rare in this dataset (see section 5), so correct-null and hallucination rates rest on a small n.\n")

    ln = M["lines"]
    A("### 2. Line items\n")
    A(_table(["precision", "recall", "F1", "matched lines", *[f"{a} acc." for a in LINE_ATTRS]],
             [[pct(ln["precision"]), pct(ln["recall"]), pct(ln["f1"]), ln["n_matched"],
               *[pct(ln["attr_accuracy"][a]) for a in LINE_ATTRS]]]))
    A(f"\nTP {ln['tp']}, FP {ln['fp']}, FN {ln['fn']}.\n")

    fc = M["fully_correct"]
    A("### 3. Invoice fully correct\n")
    A(f"**{fc['count']} / {fc['denominator']} = {pct(fc['rate'])}** of ground-truth invoices "
      "(all 8 header fields and all lines; a missed invoice counts as not correct).\n")

    s = M["silent"]
    A("### 4. Silent error rate (safety metric)\n")
    A(f"**{s['count']} / {s['denominator']} = {pct(s['rate'])}** of `validated_ok` invoices are not fully correct. "
      f"{len(s['failures'])} failing field row(s); the full list is in section 4 below.\n")

    r = M["review"]
    A("### 5. Review rate\n")
    A(_table(["invoices", "needs_review", "review rate", "correctly flagged", "unnecessary review"],
             [[r["n_invoices"], r["needs_review"], pct(r["rate"]), r["correctly_flagged"], r["unnecessary"]]]))
    A("\n*Correctly flagged* = output not fully correct, or the file carries an injected error. "
      "*Unnecessary* = output fully correct and no injected error.\n")

    v = M["validation"]
    A("### 6. Validation detection\n")
    A(_table(["injected invoices", "detected", "recall", "clean invoices", "false positives", "FP rate"],
             [[v["n_injected"], v["detected"], pct(v["recall"]), v["n_clean"], v["false_positives"],
               pct(v["false_positive_rate"])]]))
    if v["by_code"]:
        A("\n" + _table(["injected code", "n", "detected"],
                        [[c, d["n"], d["detected"]] for c, d in sorted(v["by_code"].items())]))
    A("\nFalse positive = a fully correct invoice in a non-injected file that received an error-severity issue.\n")

    st = M["status"]
    A("### 7. Status accuracy (file level)\n")
    A(f"Accuracy {pct(st['accuracy'])} over {st['n']} files. Rows = expected, columns = actual (also in `confusion.csv`).\n")
    A(_table(["expected \\ actual", *STATUSES], [[e, *[st["matrix"][e][a] for a in STATUSES]] for e in STATUSES]))
    A("")

    sc = M["success"]
    A("### 8. Success / failure\n")
    A(_table(["processable files", "success", "failed", "success rate", "failure rate",
              "bad files handled correctly", "crashes"],
             [[sc["n_processable"], sc["success"], sc["failed"], pct(sc["success_rate"]), pct(sc["failure_rate"]),
               f"{sc['bad_files_handled']} / {sc['bad_files_total']}", sc["crashes"]]]))
    A("\nSuccess = `validated_ok` or `needs_review` among files expected to be processable. "
      "Handled correctly = expected failure with the right `error_code` and no crash.\n")

    lat = M["latency"]
    A("### 9. Latency (seconds per file, wall clock)\n")
    A(_table(["slice", "n", "p50", "p95", "mean"],
             [["overall", lat["overall"]["n"], sec(lat["overall"]["p50"]), sec(lat["overall"]["p95"]), sec(lat["overall"]["mean"])]]
             + [[f"file_type={k}", d["n"], sec(d["p50"]), sec(d["p95"]), sec(d["mean"])] for k, d in lat["by_file_type"].items()]
             + [[f"tier={k}", d["n"], sec(d["p50"]), sec(d["p95"]), sec(d["mean"])] for k, d in lat["by_tier"].items()]))
    A("\nPer node (summed per file, from `history`):\n")
    A(_table(["node", "files", "mean s", "p50", "p95", "total s"],
             [[k, d["n"], sec(d["mean"]), sec(d["p50"]), sec(d["p95"]), sec(d["total"])] for k, d in lat["by_node"].items()]))
    perf = records.get("perf_5000x30.xlsx")
    if perf and perf["state"] == "ran":
        A(f"\n5,000-row x 30-column sheet (`perf_5000x30.xlsx`): **{sec(perf['seconds'])} s** end to end "
          f"(status `{perf['status']}`, {perf['llm_calls']} LLM calls).")
    A("\nLatency of LLM files includes retry back-off and `--sleep-between-llm` is *not* included. "
      "The first scanned file also pays the one-off OCR model load.\n")
    tp = extras.get("throughput")
    if tp:
        A("**Throughput** (no-LLM set: spreadsheets and bad files; re-run, wall clock; this is files/min, **not** pages/min):\n")
        A(_table(["concurrency", "files", "seconds", "files/min"],
                 [[w, tp["n_files"], d["seconds"], d["files_per_min"]] for w, d in tp["runs"].items()]) + "\n")
    pb = extras.get("parser_bench")
    if pb:
        A("**Parser-stage-only timing** (generated large digital PDF; not end-to-end; no OCR, no LLM):\n")
        if "error" in pb:
            A(f"Bench did not run: {pb['error']} ({pb['error_code']}).\n")
        else:
            A(_table(["pages", "size MB", *pb["seconds_by_stage"].keys(), "total s", "pages/min"],
                     [[pb["pages"], pb["size_mb"], *pb["seconds_by_stage"].values(), pb["total_seconds"],
                       pb["pages_per_min"]]]) + "\n")

    t = M["tiers"]
    A("### 10. Tier usage and LLM cost\n")
    A(_table(["tier", "invoices", "share"], [[k, c, pct(t["share"][k])] for k, c in sorted(t["counts"].items())]))
    cp = t["llm_calls_per_doc"]
    A(f"\nVision escalation rate: {pct(t['vision_escalation_rate'])} of invoices; files that ran `vision_extract`: "
      f"{t['files_with_vision_node']}. LLM calls per document (attempted, incl. failed): mean {sec(cp['mean'])}, "
      f"p50 {sec(cp['p50'])}, p95 {sec(cp['p95'])}, total {cp['total']}. "
      f"Tokens: {t['total_input_tokens']} in / {t['total_output_tokens']} out (successful calls only).\n")

    se = M["sheet_efficiency"]
    A("### 11. Sheet efficiency\n")
    A(f"{se['zero_llm']} / {se['n_layouts']} sheet layouts that reached `sheet_map` needed **0** LLM calls "
      f"({pct(se['rate'])}). The mapping cache is disabled in this run, so this is the synonym pass alone.\n")

    sg = M["segmentation"]
    A("### 12. Segmentation (multi-invoice PDFs)\n")
    A(_table(["files", "exact-match files", "GT invoices", "predicted invoices", "correct boundaries",
              "recall", "precision"],
             [[sg["n_files"], sg["exact_files"], sg["gt_boundaries"], sg["pred_boundaries"],
               sg["correct_boundaries"], pct(sg["recall"]), pct(sg["precision"])]]))
    A("\nA boundary is correct when a predicted invoice covers exactly the page set of a ground-truth invoice.\n")

    rr = extras.get("repeat")
    if rr:
        A("### Run-to-run difference\n")
        A(f"Second pass over {rr['diff']['n_compared']} files that ran in both: "
          f"{rr['diff']['n_changed']} changed output or status.\n")
        A(_table(["metric", "run 1", "run 2"], rr["metric_rows"]))
        for c in rr["diff"]["changed"]:
            A(f"- `{c['file']}`: " + "; ".join(c["changes"][:6]))
        A("")

    E = M["errors"]
    A("## 4. Error analysis (fill in the Cause column)\n")
    A(f"### Silent errors ({s['count']} invoices, {len(E['silent'])} rows)\n")
    A(_table(["file", "invoice", "kind", "field", "expected", "got", "issue codes", "Cause"],
             [[x["file"], _cell(x["invoice_number"]), x["kind"], x["field"], _cell(x["expected"]), _cell(x["got"]),
               ", ".join(x["issue_codes"]) or "-", "Cause: ___"] for x in E["silent"]]) if E["silent"]
      else "None in the files that ran.")
    A(f"\n### Hallucinations ({len(E['hallucinations'])}; GT null, prediction non-null)\n")
    A(_table(["file", "field", "expected", "got", "issue codes", "Cause"],
             [[x["file"], x["field"], "null", _cell(x["got"]), ", ".join(x["issue_codes"]) or "-", "Cause: ___"]
              for x in E["hallucinations"]]) if E["hallucinations"] else "None in the files that ran.")
    A("")

    A("## 5. What the numbers do not show\n")
    A(f"- **Synthetic data.** Layouts come from three generated templates plus degraded scans; real suppliers' layouts, stamps, "
      "handwriting and multilingual text are not covered. No handwriting at all; few currencies and date formats.")
    A(f"- **Small sample.** {n_gt_inv} ground-truth invoices in total, fewer in the subset that ran. Null-valued GT fields exist only for "
      "`subtotal` and `tax` (5 each) and `invoice_number` (2), so correct-null/hallucination rates rest on about a dozen cases.")
    A(f"- **Subset bias.** Only {cov['n_ran']} of {n_files} files ran; {counts.get('skipped_quota', 0)} were skipped by LLM quota, "
      f"{counts.get('skipped_llm_required', 0)} because `--no-llm` was set. Skipped files may be easier or harder than those that ran.")
    A("- **Ground-truth gaps.** Files whose GT lists no invoices (several images, sheet edge cases, triage files) are scored at file level only; "
      "any invoice extracted from them is an unscored *extra*, except that a `validated_ok` extra in a file expecting `validated_ok` counts as a silent error. "
      "`eval:false` triage files are excluded from field metrics but kept in status and latency tables.")
    A("- **Injected errors.** `wrong_total_*` ground truth stores the *printed* (wrong) total, so \"fully correct\" means \"extracted what is printed\"; "
      "detection is measured separately in section 3.6.")
    A("- **Duplicate check.** Each file runs with a fresh in-memory store, pre-seeded only for the injected duplicates, so cross-file duplicates among "
      "fixtures that share an invoice number are not flagged here as they would be in production. The sheet-mapping cache is off.")
    A("- **Nondeterminism.** The LLM is called at temperature 0 but is not deterministic; one run is one sample (see the run-to-run section if present).")
    A("- **In-process run.** API validation, queueing, DB constraints and real concurrency are outside these numbers.\n")

    A("## 6. Reproduce\n")
    A("```powershell")
    A(f"cd backend")
    A(f"python -m scripts.generate_fixtures --seed {meta['fixtures_seed']} --out data/fixtures")
    for c in commands:
        A(c)
    A("```\n")
    return "\n".join(L)


def write_outputs(out: Path, meta: dict, records: dict, gt_entries: list[dict], metrics: dict, extras: dict,
                  commands: list[str]) -> None:
    out.mkdir(parents=True, exist_ok=True)
    raw = {"meta": meta, "commands": commands, "files": records, "metrics": metrics, "extras": extras}
    (out / "results.json").write_text(json.dumps(raw, indent=2, default=str), encoding="utf-8")
    write_csvs(out, records, gt_entries, metrics)
    (out / "report.md").write_text(render_report(meta, metrics, records, gt_entries, extras, commands),
                                   encoding="utf-8")
