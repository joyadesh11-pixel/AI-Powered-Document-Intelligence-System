"""Pure metric functions for the evaluation (M10, docs/05 metrics 1-12). No I/O.

Inputs are plain dicts:
  * ground-truth entries, as in eval/ground_truth.json;
  * per-file run records (see scripts/eval_runner.py): state, status, invoices (slim dicts), ...

Money and dates are compared as Decimal / date, never as float. Only records whose
state == "ran" enter any denominator.
"""
from __future__ import annotations

import math
import re
from collections import Counter, defaultdict
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any, Iterable

from rapidfuzz import fuzz

FIELDS = ("invoice_number", "invoice_date", "supplier_name", "customer_name",
          "subtotal", "tax", "total", "currency")
MONEY_FIELDS = frozenset({"subtotal", "tax", "total"})
DATE_FIELDS = frozenset({"invoice_date"})
OUTCOMES = ("correct", "correct_null", "omission", "wrong", "hallucination")
STATUSES = ("validated_ok", "needs_review", "failed")
LINE_ATTRS = ("quantity", "unit_price", "amount")

LINE_DESC_MIN = 80          # rapidfuzz ratio for a description match
LINE_DESC_FLOOR = 30        # second pass: equal amount, weak description
SUPPLIER_FALLBACK_MIN = 90  # supplier ratio that may pair invoices when totals differ
STATE_RAN = "ran"


# ------------------------------------------------------------ normalisation

def norm_str(v: Any) -> str | None:
    if v is None:
        return None
    s = " ".join(str(v).split()).casefold()
    return s or None


def _alnum(v: Any) -> str | None:
    if v is None:
        return None
    s = re.sub(r"[^0-9a-z]", "", str(v).casefold())
    return s or None


def to_decimal(v: Any) -> Decimal | None:
    if v is None:
        return None
    try:
        d = Decimal(str(v).strip())
    except InvalidOperation:
        return None
    return d if d.is_finite() else None


def amounts_equal(a: Any, b: Any) -> bool:
    da, db = to_decimal(a), to_decimal(b)
    return da is not None and db is not None and da == db


def to_date(v: Any) -> date | None:
    if v is None:
        return None
    if isinstance(v, date):
        return v
    try:
        return date.fromisoformat(str(v).strip()[:10])
    except ValueError:
        return None


def values_equal(field: str, gt: Any, pred: Any) -> bool:
    if field in MONEY_FIELDS:
        return amounts_equal(gt, pred)
    if field in DATE_FIELDS:
        dg, dp = to_date(gt), to_date(pred)
        return dg is not None and dg == dp
    return norm_str(gt) == norm_str(pred)


def _is_null(v: Any) -> bool:
    return v is None or (isinstance(v, str) and not v.strip())


def classify_field(field: str, gt: Any, pred: Any) -> str:
    gn, pn = _is_null(gt), _is_null(pred)
    if gn and pn:
        return "correct_null"
    if gn:
        return "hallucination"
    if pn:
        return "omission"
    return "correct" if values_equal(field, gt, pred) else "wrong"


def ratio(n: int, d: int) -> float | None:
    return None if d == 0 else n / d


def _f1(p: float | None, r: float | None) -> float | None:
    if p is None or r is None:
        return None
    return 0 if p + r == 0 else 2 * p * r / (p + r)


def percentile(values: Iterable[Any], p: int) -> Any:
    """Linear-interpolation percentile; None for an empty list."""
    v = sorted(values)
    if not v:
        return None
    k = (len(v) - 1) * p / 100
    lo, hi = math.floor(k), math.ceil(k)
    if lo == hi:
        return v[lo]
    return v[lo] + (v[hi] - v[lo]) * (k - lo)


def stats(values: list[Any]) -> dict:
    return {"n": len(values), "p50": percentile(values, 50), "p95": percentile(values, 95),
            "mean": (sum(values) / len(values)) if values else None}


# ------------------------------------------------------------------ matching

def _supplier_ratio(a: dict, b: dict) -> int:
    sa, sb = norm_str(a.get("supplier_name")), norm_str(b.get("supplier_name"))
    if not sa or not sb:
        return 0
    return int(fuzz.ratio(sa, sb))


def match_invoices(gt: list[dict], pred: list[dict]) -> tuple[list[tuple[int, int]], list[int], list[int]]:
    """(pairs (gi, pi), missed gt indexes, extra pred indexes).

    1) equal invoice_number (alphanumerics, casefold); 2) fallback best total+supplier match.
    """
    pairs: list[tuple[int, int]] = []
    free_g, free_p = set(range(len(gt))), set(range(len(pred)))
    for gi, g in enumerate(gt):
        gk = _alnum(g.get("invoice_number"))
        if gk is None:
            continue
        for pi in sorted(free_p):
            if _alnum(pred[pi].get("invoice_number")) == gk:
                pairs.append((gi, pi))
                free_g.discard(gi)
                free_p.discard(pi)
                break
    cands = []
    for gi in free_g:
        for pi in free_p:
            tot_eq = amounts_equal(gt[gi].get("total"), pred[pi].get("total"))
            sup = _supplier_ratio(gt[gi], pred[pi])
            if (tot_eq and sup >= 50) or sup >= SUPPLIER_FALLBACK_MIN:
                cands.append((int(tot_eq) + sup / 100, gi, pi))
    for _, gi, pi in sorted(cands, key=lambda c: (-c[0], c[1], c[2])):
        if gi in free_g and pi in free_p:
            pairs.append((gi, pi))
            free_g.discard(gi)
            free_p.discard(pi)
    return sorted(pairs), sorted(free_g), sorted(free_p)


# --------------------------------------------------------------------- lines

def _attr_equal(a: Any, b: Any) -> bool:
    if _is_null(a) and _is_null(b):
        return True
    return amounts_equal(a, b)


def _desc_sim(a: dict, b: dict) -> int:
    da, db = norm_str(a.get("description")), norm_str(b.get("description"))
    if not da or not db:
        return 0
    return int(fuzz.ratio(da, db))


def score_lines(gt: list[dict], pred: list[dict]) -> dict:
    free_g, free_p = set(range(len(gt))), set(range(len(pred)))
    pairs: list[tuple[int, int]] = []

    def assign(cands: list[tuple]) -> None:
        for _, gi, pi in sorted(cands):
            if gi in free_g and pi in free_p:
                pairs.append((gi, pi))
                free_g.discard(gi)
                free_p.discard(pi)

    first = []
    for gi in free_g:
        for pi in free_p:
            sim = _desc_sim(gt[gi], pred[pi])
            if sim >= LINE_DESC_MIN:
                first.append(((-int(amounts_equal(gt[gi].get("amount"), pred[pi].get("amount"))), -sim), gi, pi))
    assign(first)
    second = []
    for gi in free_g:
        for pi in free_p:
            if amounts_equal(gt[gi].get("amount"), pred[pi].get("amount")):
                sim = _desc_sim(gt[gi], pred[pi])
                if sim >= LINE_DESC_FLOOR:
                    second.append(((-sim,), gi, pi))
    assign(second)

    attr_correct = {a: 0 for a in LINE_ATTRS}
    all_ok = True
    for gi, pi in pairs:
        line_ok = True
        for a in LINE_ATTRS:
            if _attr_equal(gt[gi].get(a), pred[pi].get(a)):
                attr_correct[a] += 1
            else:
                line_ok = False
        all_ok = all_ok and line_ok
    tp, fp, fn = len(pairs), len(free_p), len(free_g)
    precision = ratio(tp, tp + fp)
    recall = ratio(tp, tp + fn)
    f1 = _f1(precision, recall)
    return {"tp": tp, "fp": fp, "fn": fn, "precision": precision, "recall": recall, "f1": f1,
            "n_matched": tp, "attr_correct": attr_correct, "all_attrs_correct": all_ok,
            "n_gt": len(gt), "n_pred": len(pred)}


# ------------------------------------------------------------ invoice scoring

def score_invoice(gt: dict, pred: dict) -> dict:
    fields = {f: classify_field(f, gt.get(f), pred.get(f)) for f in FIELDS}
    lines = score_lines(gt.get("line_items") or [], pred.get("line_items") or [])
    header_ok = all(o in ("correct", "correct_null") for o in fields.values())
    lines_ok = lines["fn"] == 0 and lines["fp"] == 0 and lines["all_attrs_correct"]
    bad = [(f, gt.get(f), pred.get(f)) for f, o in fields.items() if o not in ("correct", "correct_null")]
    return {"fields": fields, "lines": lines, "fully_correct": header_ok and lines_ok, "bad_fields": bad}


# ----------------------------------------------------------------- aggregates

def _codes(inv: dict) -> list[str]:
    return sorted({i["code"] for i in inv.get("issues") or []})


def _has_error(inv: dict) -> bool:
    return any(i.get("severity") == "error" for i in inv.get("issues") or [])


def _dominant_tier(invoices: list[dict]) -> str:
    tiers = [i.get("tier") for i in invoices if i.get("tier")]
    return Counter(tiers).most_common(1)[0][0] if tiers else "none"


def _line_summary(sc: dict) -> str:
    ln = sc["lines"]
    return f"lines tp={ln['tp']} fp={ln['fp']} fn={ln['fn']} attrs_ok={ln['all_attrs_correct']}"


def compute_metrics(records: dict[str, dict], gt_entries: list[dict]) -> dict:
    gt_by_file = {e["file"]: e for e in gt_entries}
    state_counts: Counter = Counter()
    coverage_rows = []
    for e in gt_entries:
        r = records.get(e["file"])
        st = r["state"] if r else "skipped_not_selected"
        state_counts[st] += 1
        coverage_rows.append({"file": e["file"], "group": e["group"], "state": st,
                              "reason": (r or {}).get("reason")})

    ran = [(gt_by_file[f], r) for f, r in records.items() if r["state"] == STATE_RAN and f in gt_by_file]
    scored = [(g, r) for g, r in ran if g.get("eval") is not False]

    field_counts = {f: Counter() for f in FIELDS}
    line_tot = Counter()
    attr_ok = Counter()
    attr_n = 0
    n_gt_inv = n_fully = 0
    sil_den = sil_count = 0
    sil_rows: list[dict] = []
    hall_rows: list[dict] = []
    rev_n = rev_nr = rev_ok = rev_unn = 0
    inj_n = inj_det = 0
    inj_by_code: dict[str, dict] = defaultdict(lambda: {"n": 0, "detected": 0})
    clean_n = clean_fp = 0
    seg_files = seg_exact = seg_gt = seg_ok = seg_pred = 0
    per_file: dict[str, dict] = {}

    for g, r in scored:
        gts, preds = g.get("invoices") or [], r.get("invoices") or []
        pairs, missed, extra = match_invoices(gts, preds)
        injected = g.get("injected")
        n_gt_inv += len(gts)
        fc_here = silent_here = 0

        for gi, pi in pairs:
            sc = score_invoice(gts[gi], preds[pi])
            p = preds[pi]
            for f, o in sc["fields"].items():
                field_counts[f][o] += 1
                if o == "hallucination":
                    hall_rows.append({"file": g["file"], "field": f, "expected": None,
                                      "got": p.get(f), "issue_codes": _codes(p)})
            ln = sc["lines"]
            line_tot.update({"tp": ln["tp"], "fp": ln["fp"], "fn": ln["fn"]})
            for a in LINE_ATTRS:
                attr_ok[a] += ln["attr_correct"][a]
            attr_n += ln["n_matched"]
            if sc["fully_correct"]:
                n_fully += 1
                fc_here += 1
            if p.get("status") == "validated_ok":
                sil_den += 1
                if not sc["fully_correct"]:
                    sil_count += 1
                    silent_here += 1
                    rows = [(f, e, got) for f, e, got in sc["bad_fields"]]
                    if not rows:
                        rows = [("line_items", f"{ln['n_gt']} lines", _line_summary(sc))]
                    for f, e, got in rows:
                        sil_rows.append({"file": g["file"], "invoice_number": p.get("invoice_number"),
                                         "kind": "wrong_value", "field": f, "expected": e, "got": got,
                                         "issue_codes": _codes(p)})
            rev_n += 1
            if p.get("status") == "needs_review":
                rev_nr += 1
                if (not sc["fully_correct"]) or injected:
                    rev_ok += 1
                else:
                    rev_unn += 1
            if not injected and sc["fully_correct"]:
                clean_n += 1
                if _has_error(p):
                    clean_fp += 1

        for gi in missed:
            for f in FIELDS:
                if not _is_null(gts[gi].get(f)):
                    field_counts[f]["omission"] += 1
            line_tot["fn"] += len(gts[gi].get("line_items") or [])

        for pi in extra:
            p = preds[pi]
            if gts:  # files with no GT invoices (e.g. the 5,000-row perf sheet) must not swamp the review rate
                rev_n += 1
                if p.get("status") == "needs_review":
                    rev_nr += 1
                    rev_ok += 1
            if g["expect_status"] == "validated_ok":
                if p.get("status") == "validated_ok":
                    sil_den += 1
                    sil_count += 1
                    silent_here += 1
                    sil_rows.append({"file": g["file"], "invoice_number": p.get("invoice_number"),
                                     "kind": "extra_invoice", "field": "(invoice)", "expected": None,
                                     "got": p.get("invoice_number"), "issue_codes": _codes(p)})

        if injected:
            code = injected["code"]
            codes_doc = set(r.get("warnings") or [])
            matched = dict(pairs)
            for gi in range(len(gts)):
                inj_n += 1
                inj_by_code[code]["n"] += 1
                if gi in matched and (code in _codes(preds[matched[gi]]) or code in codes_doc):
                    inj_det += 1
                    inj_by_code[code]["detected"] += 1

        if g.get("page_ranges"):
            gt_sets = [tuple(sorted(x["pages"])) for x in g["page_ranges"]]
            pr_sets = [tuple(sorted(i.get("source_pages") or [])) for i in preds]
            hit = sum((Counter(gt_sets) & Counter(pr_sets)).values())
            seg_files += 1
            seg_gt += len(gt_sets)
            seg_pred += len(pr_sets)
            seg_ok += hit
            seg_exact += int(sorted(gt_sets) == sorted(pr_sets))

        per_file[g["file"]] = {"n_gt_invoices": len(gts), "n_pred_invoices": len(preds),
                               "fully_correct_invoices": fc_here, "silent_errors": silent_here,
                               "tier": _dominant_tier(preds),
                               "issue_codes": sorted({c for i in preds for c in _codes(i)})}

    # ---- header table
    fields_out = {}
    micro = Counter()
    for f in FIELDS:
        c = field_counts[f]
        n = sum(c.values())
        fields_out[f] = {"n": n, **{o: c[o] for o in OUTCOMES},
                         "accuracy": ratio(c["correct"] + c["correct_null"], n),
                         **{f"{o}_rate": ratio(c[o], n) for o in OUTCOMES}}
        micro.update(c)
    mn = sum(micro.values())
    header = {"fields": fields_out,
              "micro": {"n": mn, **{o: micro[o] for o in OUTCOMES},
                        "accuracy": ratio(micro["correct"] + micro["correct_null"], mn),
                        **{f"{o}_rate": ratio(micro[o], mn) for o in OUTCOMES}}}

    tp, fp, fn = line_tot["tp"], line_tot["fp"], line_tot["fn"]
    prec, rec_ = ratio(tp, tp + fp), ratio(tp, tp + fn)
    lines = {"tp": tp, "fp": fp, "fn": fn, "precision": prec, "recall": rec_,
             "f1": _f1(prec, rec_),
             "n_matched": attr_n,
             "attr_accuracy": {a: ratio(attr_ok[a], attr_n) for a in LINE_ATTRS}}

    # ---- status confusion (all ran files with a status, incl. eval:false)
    matrix = {e: {a: 0 for a in STATUSES} for e in STATUSES}
    n_status = n_diag = 0
    for g, r in ran:
        if r.get("status") in STATUSES and g["expect_status"] in STATUSES:
            matrix[g["expect_status"]][r["status"]] += 1
            n_status += 1
            n_diag += int(g["expect_status"] == r["status"])

    # ---- success / failure / bad files
    proc = [(g, r) for g, r in ran if g["expect_status"] != "failed"]
    n_ok = sum(1 for g, r in proc if r.get("status") in ("validated_ok", "needs_review"))
    n_bad_status = sum(1 for g, r in proc if r.get("status") == "failed" or r.get("crash"))
    bad = [(g, r) for g, r in ran if g["expect_status"] == "failed"]
    handled = sum(1 for g, r in bad if r.get("status") == "failed" and not r.get("crash")
                  and r.get("error_code") == g.get("expect_error_code"))
    success = {"n_processable": len(proc), "success": n_ok, "failed": n_bad_status,
               "success_rate": ratio(n_ok, len(proc)), "failure_rate": ratio(n_bad_status, len(proc)),
               "bad_files_total": len(bad), "bad_files_handled": handled,
               "crashes": sum(1 for _, r in ran if r.get("crash"))}

    # ---- latency
    timed = [(g, r) for g, r in ran if r.get("seconds") is not None]
    by_type: dict[str, list] = defaultdict(list)
    by_tier: dict[str, list] = defaultdict(list)
    by_node_file: dict[str, list] = defaultdict(list)
    for g, r in timed:
        by_type[r.get("file_type") or "unknown"].append(r["seconds"])
        by_tier[_dominant_tier(r.get("invoices") or [])].append(r["seconds"])
        node_sum: dict[str, Any] = defaultdict(lambda: 0)
        for h in r.get("history") or []:
            node_sum[h["node"]] += h.get("seconds") or 0
        for n, s in node_sum.items():
            by_node_file[n].append(s)
    latency = {"overall": stats([r["seconds"] for _, r in timed]),
               "by_file_type": {k: stats(v) for k, v in sorted(by_type.items())},
               "by_tier": {k: stats(v) for k, v in sorted(by_tier.items())},
               "by_node": {k: {**stats(v), "total": sum(v)} for k, v in sorted(by_node_file.items())}}

    # ---- tier usage (all ran files)
    tier_c: Counter = Counter()
    for g, r in scored:
        if not g.get("invoices"):
            continue
        tier_c.update(i.get("tier") for i in r.get("invoices") or [] if i.get("tier"))
    n_t = sum(tier_c.values())
    calls = [r.get("llm_calls") or 0 for _, r in ran]
    tiers = {"n_invoices": n_t, "counts": dict(tier_c),
             "share": {t: ratio(c, n_t) for t, c in tier_c.items()},
             "vision_escalation_rate": ratio(tier_c["vision"], n_t),
             "files_with_vision_node": sum(
                 1 for _, r in ran if any(h["node"] == "vision_extract" for h in r.get("history") or [])),
             "llm_calls_per_doc": {**stats(calls), "total": sum(calls)},
             "total_input_tokens": sum(r.get("input_tokens") or 0 for _, r in ran),
             "total_output_tokens": sum(r.get("output_tokens") or 0 for _, r in ran)}

    # ---- sheet efficiency
    layouts = [r for _, r in ran if any(h["node"] == "sheet_map" for h in r.get("history") or [])]
    zero = sum(1 for r in layouts if (r.get("llm_calls_by_schema") or {}).get("SheetMapping", 0) == 0)

    return {
        "coverage": {"counts": dict(state_counts), "files": coverage_rows,
                     "n_ran": len(ran), "n_scored": len(scored)},
        "header": header,
        "lines": lines,
        "fully_correct": {"count": n_fully, "denominator": n_gt_inv, "rate": ratio(n_fully, n_gt_inv)},
        "silent": {"count": sil_count, "denominator": sil_den, "rate": ratio(sil_count, sil_den),
                   "failures": sil_rows},
        "review": {"n_invoices": rev_n, "needs_review": rev_nr, "rate": ratio(rev_nr, rev_n),
                   "correctly_flagged": rev_ok, "unnecessary": rev_unn},
        "validation": {"n_injected": inj_n, "detected": inj_det, "recall": ratio(inj_det, inj_n),
                       "by_code": dict(inj_by_code), "n_clean": clean_n, "false_positives": clean_fp,
                       "false_positive_rate": ratio(clean_fp, clean_n)},
        "status": {"labels": list(STATUSES), "matrix": matrix, "n": n_status,
                   "accuracy": ratio(n_diag, n_status)},
        "success": success,
        "latency": latency,
        "tiers": tiers,
        "sheet_efficiency": {"n_layouts": len(layouts), "zero_llm": zero, "rate": ratio(zero, len(layouts))},
        "segmentation": {"n_files": seg_files, "exact_files": seg_exact, "gt_boundaries": seg_gt,
                         "pred_boundaries": seg_pred, "correct_boundaries": seg_ok,
                         "recall": ratio(seg_ok, seg_gt), "precision": ratio(seg_ok, seg_pred)},
        "errors": {"silent": sil_rows, "hallucinations": hall_rows},
        "per_file": per_file,
    }
