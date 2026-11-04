"""Evaluation CLI (M10): run the pipeline in-process over the ground-truth files and write the report.

    python -m scripts.evaluate --no-llm                       # free, no quota
    python -m scripts.evaluate --resume ../eval/reports/<ts> --groups injected,digital_pdf \\
        --max-llm-files 10 --sleep-between-llm 5              # add LLM files to the same report

See docs/05-testing-eval.md for the metric definitions. Nothing is typed by hand.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

from scripts import eval_runner as er
from scripts.eval_metrics import compute_metrics
from scripts.eval_report import write_outputs, pct

# rough LLM calls per file for --dry-run (1 text call per invoice unit; escalations/mapping not predictable)
_EST_NOTE = "estimate: 1 text call per GT invoice (>=1 per file); vision/mapping/retries add more"


def _parse(argv: list[str] | None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Evaluate the pipeline against eval/ground_truth.json")
    p.add_argument("--fixtures", default="data/fixtures")
    p.add_argument("--gt", default="../eval/ground_truth.json")
    p.add_argument("--out", default=None, help="report dir (default ../eval/reports/<timestamp>)")
    p.add_argument("--resume", default=None, help="existing report dir: keep 'ran' files, run the rest, rewrite report")
    p.add_argument("--groups", default=None, help="comma list of GT group names; default all")
    p.add_argument("--files", default=None, help="comma list of exact file names, run in this order")
    p.add_argument("--max-llm-calls", type=int, default=None, help="hard cap on LLM calls for this invocation")
    p.add_argument("--no-llm", action="store_true", help="only files that need zero LLM calls")
    p.add_argument("--max-llm-files", type=int, default=None)
    p.add_argument("--sleep-between-llm", type=float, default=0.0)
    p.add_argument("--dry-run", action="store_true", help="print the plan and estimated LLM calls; run nothing")
    p.add_argument("--throughput", action="store_true", help="no-LLM set at concurrency 1 vs 4")
    p.add_argument("--parser-bench", type=int, default=None, metavar="PAGES",
                   help="parser-stage-only timing on a generated PDF of PAGES pages")
    p.add_argument("--repeat", action="store_true", help="second pass over files that ran, report the difference")
    p.add_argument("--fixtures-seed", type=int, default=42)
    return p.parse_args(argv)


def _plan(entries: list[dict], args, existing: dict) -> None:
    groups = args.groups.split(",") if args.groups else None
    print(f"{'file':<40s} {'group':<14s} {'llm?':<6s} {'est calls':>9s}  note")
    total = 0
    for e in er._order(entries, groups):
        hint = er.needs_llm_hint(e)
        done = existing.get(e["file"], {}).get("state") == er.STATE_RAN
        est = 0 if hint == "no" or (hint == "maybe") else max(1, len(e.get("invoices") or []))
        if args.no_llm and hint == "yes":
            note = "skipped (--no-llm)"
            est = 0
        elif done:
            note, est = "already ran (resume)", 0
        else:
            note = "may need 1 mapping call" if hint == "maybe" else ""
        total += est
        print(f"{e['file']:<40s} {e['group']:<14s} {hint:<6s} {est:>9d}  {note}")
    print(f"\nestimated LLM calls (floor): {total}  [{_EST_NOTE}]")


def main(argv: list[str] | None = None) -> int:
    args = _parse(argv)
    fixtures, gt_path = Path(args.fixtures), Path(args.gt)
    entries = er.load_gt(gt_path)
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    out = Path(args.resume or args.out or f"../eval/reports/{ts}")

    existing_raw = {}
    commands: list[str] = []
    if args.resume and (out / "results.json").exists():
        existing_raw = json.loads((out / "results.json").read_text(encoding="utf-8"))
    existing = existing_raw.get("files", {})
    commands = list(existing_raw.get("commands", []))

    if args.dry_run:
        _plan(entries, args, existing)
        return 0

    from dotenv import load_dotenv
    load_dotenv()
    from app.pipeline.config import get_config
    cfg = get_config()

    llm_inner = None
    if not args.no_llm:
        try:
            llm_inner = er.build_real_llm(cfg)
        except Exception as exc:  # noqa: BLE001
            print(f"WARNING: no real LLM client ({exc}); LLM files will be skipped as llm_error", file=sys.stderr)

    groups = args.groups.split(",") if args.groups else None
    flags = " ".join(a for a in (argv if argv is not None else sys.argv[1:]))
    commands.append(f"python -m scripts.evaluate {flags}".strip())

    def log(m: str) -> None:
        print(m, flush=True)

    records = er.run_eval(entries, fixtures=fixtures, existing=existing, groups=groups, no_llm=args.no_llm,
                          max_llm_files=args.max_llm_files, sleep_between_llm=args.sleep_between_llm,
                          llm_inner=llm_inner, make_deps=er.default_make_deps(cfg), log=log,
                          files=args.files.split(",") if args.files else None,
                          max_llm_calls=args.max_llm_calls)

    extras = dict(existing_raw.get("extras", {}))
    if args.throughput:
        extras["throughput"] = er.throughput_bench(records, fixtures, cfg)
    if args.parser_bench:
        extras["parser_bench"] = er.parser_bench(args.parser_bench, cfg)
    if args.repeat:
        ran_now = {f for f, r in records.items() if r["state"] == er.STATE_RAN and r["llm_calls"] > 0}
        second = er.run_eval(entries, fixtures=fixtures, existing={}, groups=groups, no_llm=args.no_llm,
                             max_llm_files=None, sleep_between_llm=args.sleep_between_llm, llm_inner=llm_inner,
                             make_deps=er.default_make_deps(cfg), only_files=ran_now or None, log=log)
        m1, m2 = compute_metrics(records, entries), compute_metrics(second, entries)
        rows = [[name, pct(a), pct(b)] for name, a, b in [
            ("silent error rate", m1["silent"]["rate"], m2["silent"]["rate"]),
            ("header micro accuracy", m1["header"]["micro"]["accuracy"], m2["header"]["micro"]["accuracy"]),
            ("fully correct rate", m1["fully_correct"]["rate"], m2["fully_correct"]["rate"]),
            ("review rate", m1["review"]["rate"], m2["review"]["rate"])]]
        extras["repeat"] = {"diff": er.diff_runs(records, second), "metric_rows": rows}

    metrics = compute_metrics(records, entries)
    meta = er.collect_meta(cfg, args.fixtures_seed)
    write_outputs(out, meta, records, entries, metrics, extras, commands)

    c = metrics["coverage"]["counts"]
    print(f"\nreport: {out / 'report.md'}")
    print("states: " + ", ".join(f"{k}={v}" for k, v in sorted(c.items())))
    print(f"silent errors: {metrics['silent']['count']}/{metrics['silent']['denominator']}  "
          f"micro accuracy: {pct(metrics['header']['micro']['accuracy'])}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
