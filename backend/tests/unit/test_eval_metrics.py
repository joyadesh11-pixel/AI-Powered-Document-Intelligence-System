"""Unit tests for scripts/eval_metrics.py (M10). Tiny hand-made predictions and ground truth."""
from __future__ import annotations

import ast
import inspect
from decimal import Decimal

import pytest

from scripts import eval_metrics as m


# ------------------------------------------------------------------ builders

def line(desc="Widget", qty="10", price="9.00", amt="90.00"):
    return {"description": desc, "quantity": qty, "unit_price": price, "amount": amt}


def gt_inv(**kw):
    base = {
        "invoice_number": "INV-1", "invoice_date": "2026-11-03", "supplier_name": "Acme Ltd",
        "customer_name": "Bolt Co", "subtotal": "90.00", "tax": "9.00", "total": "99.00",
        "currency": "USD", "line_items": [line()],
    }
    base.update(kw)
    return base


def pred_inv(status="validated_ok", issues=None, tier="parser", pages=(0,), **kw):
    base = gt_inv()
    base.update({"status": status, "tier": tier, "source_pages": list(pages),
                 "issues": issues or []})
    base.update(kw)
    return base


def gt_entry(file="a.pdf", group="digital_pdf", status="validated_ok", invoices=None, **kw):
    e = {"file": file, "group": group, "expect_status": status, "injected": None,
         "invoices": [gt_inv()] if invoices is None else invoices}
    e.update(kw)
    return e


def rec(file="a.pdf", state="ran", status="validated_ok", invoices=None, seconds=1.0,
        file_type="pdf", error_code=None, history=None, llm_calls=1, by_schema=None,
        crash=None, reason=None, **kw):
    r = {"file": file, "state": state, "status": status, "error_code": error_code,
         "file_type": file_type, "seconds": seconds, "crash": crash, "reason": reason,
         "invoices": [pred_inv()] if invoices is None else invoices,
         "warnings": [], "history": history or [], "llm_calls": llm_calls,
         "llm_calls_by_schema": by_schema or {}, "input_tokens": 10, "output_tokens": 5}
    r.update(kw)
    return r


def issue(code, severity="error", field=None):
    return {"code": code, "severity": severity, "field": field}


def run(records, gts):
    return m.compute_metrics({r["file"]: r for r in records}, gts)


# ------------------------------------------------------------ field outcomes

@pytest.mark.parametrize("gt,pred,expected", [
    ("INV-1", "inv-1", "correct"),
    (None, None, "correct_null"),
    ("INV-1", None, "omission"),
    ("INV-1", "INV-2", "wrong"),
    (None, "INV-9", "hallucination"),
])
def test_five_outcome_classes(gt, pred, expected):
    assert m.classify_field("invoice_number", gt, pred) == expected


def test_string_normalisation_casefold_and_whitespace():
    assert m.classify_field("supplier_name", "Acme  Ltd", " acme ltd\n") == "correct"
    assert m.classify_field("supplier_name", "Acme Ltd", "Acme Limited") == "wrong"


def test_amounts_compare_as_decimal():
    assert m.classify_field("total", "99.00", "99") == "correct"
    assert m.classify_field("total", "99.0", "99.00") == "correct"
    assert m.classify_field("total", "99.00", "99.01") == "wrong"
    assert m.classify_field("total", "0.30", "0.3") == "correct"


def test_unparseable_amount_is_wrong_not_crash():
    assert m.classify_field("total", "99.00", "abc") == "wrong"


def test_dates_compare_as_date():
    assert m.classify_field("invoice_date", "2026-11-03", "2026-11-03") == "correct"
    assert m.classify_field("invoice_date", "2026-11-03", "2026-03-11") == "wrong"


def test_eval_metrics_never_uses_float():
    src = inspect.getsource(m)
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            assert node.func.id != "float", "float() call in eval_metrics"
        if isinstance(node, ast.Constant) and isinstance(node.value, float):
            continue  # float literals for report ratios are allowed; money never passes through them
    # money comparison helper returns Decimal-based equality
    assert m.amounts_equal("1.10", "1.1") is True
    assert isinstance(m.to_decimal("1.10"), Decimal)


# ------------------------------------------------------------------ matching

def test_match_by_invoice_number():
    g = [gt_inv(invoice_number="A"), gt_inv(invoice_number="B", total="50.00")]
    p = [pred_inv(invoice_number="b", total="50.00"), pred_inv(invoice_number="A")]
    pairs, missed, extra = m.match_invoices(g, p)
    assert sorted(pairs) == [(0, 1), (1, 0)]
    assert missed == [] and extra == []


def test_match_fallback_total_and_supplier():
    g = [gt_inv(invoice_number=None, supplier_name="Edwards, Baker and Anderson", total="1308.69")]
    p = [pred_inv(invoice_number="X-77", supplier_name="Edwards Baker and Anderson", total="1308.69")]
    pairs, missed, extra = m.match_invoices(g, p)
    assert pairs == [(0, 0)] and not missed and not extra


def test_unmatched_are_missed_and_extra():
    g = [gt_inv(invoice_number="A", total="10.00", supplier_name="Zeta")]
    p = [pred_inv(invoice_number="Z", total="77.00", supplier_name="Totally Other")]
    pairs, missed, extra = m.match_invoices(g, p)
    assert pairs == [] and missed == [0] and extra == [0]


# --------------------------------------------------------------------- lines

def test_line_precision_recall_f1():
    g = [line("Widget", amt="90.00"), line("Gadget", qty="1", price="5.00", amt="5.00")]
    p = [line("widget", amt="90.00"), line("Invented", qty="1", price="1.00", amt="1.00")]
    r = m.score_lines(g, p)
    assert (r["tp"], r["fp"], r["fn"]) == (1, 1, 1)
    assert r["precision"] == r["recall"] == r["f1"] == 0.5


def test_line_attribute_accuracy_on_matched_lines():
    g = [line("Widget", qty="10", price="9.00", amt="90.00")]
    p = [line("Widget", qty="9", price="9.00", amt="90.00")]
    r = m.score_lines(g, p)
    assert r["attr_correct"] == {"quantity": 0, "unit_price": 1, "amount": 1}
    assert r["n_matched"] == 1 and r["all_attrs_correct"] is False


def test_line_match_by_amount_when_description_garbled():
    r = m.score_lines([line("Consulting Services")], [line("C0nsulting Serv", amt="90.00")])
    assert r["tp"] == 1


# ---------------------------------------------------------- invoice scoring

def test_fully_correct_requires_lines():
    ok = m.score_invoice(gt_inv(), pred_inv())
    assert ok["fully_correct"] is True
    bad_lines = m.score_invoice(gt_inv(), pred_inv(line_items=[]))
    assert bad_lines["fully_correct"] is False
    extra_line = m.score_invoice(gt_inv(), pred_inv(line_items=[line(), line("Extra", amt="1.00")]))
    assert extra_line["fully_correct"] is False


# ------------------------------------------------------------ aggregate: header

def test_header_rates_and_micro_average():
    gts = [gt_entry(invoices=[gt_inv(subtotal=None)])]
    recs = [rec(invoices=[pred_inv(subtotal="5.00", total="98.00", tax=None)])]
    out = run(recs, gts)["header"]
    f = out["fields"]
    assert f["subtotal"]["hallucination"] == 1 and f["subtotal"]["n"] == 1
    assert f["total"]["wrong"] == 1
    assert f["tax"]["omission"] == 1
    assert f["invoice_number"]["correct"] == 1
    assert out["micro"]["n"] == 8
    assert out["micro"]["accuracy"] == pytest.approx(5 / 8)


def test_missed_invoice_counts_as_omission_for_non_null_fields():
    gts = [gt_entry(invoices=[gt_inv(subtotal=None)])]
    out = run([rec(invoices=[])], gts)["header"]["fields"]
    assert out["total"]["omission"] == 1
    assert out["subtotal"]["n"] == 0   # null GT field of a missed invoice is not scored


# ----------------------------------------------------- aggregate: silent errors

def test_silent_error_counts_only_validated_ok_and_wrong():
    gts = [gt_entry("a.pdf"), gt_entry("b.pdf"), gt_entry("c.pdf")]
    recs = [
        rec("a.pdf", invoices=[pred_inv(total="1.00")]),                      # validated_ok + wrong -> silent
        rec("b.pdf", status="needs_review", invoices=[pred_inv("needs_review", total="1.00",
                                                              issues=[issue("ARITH_TOTAL")])]),  # flagged
        rec("c.pdf"),                                                          # validated_ok + right
    ]
    s = run(recs, gts)["silent"]
    assert s["count"] == 1 and s["denominator"] == 2
    assert s["rate"] == pytest.approx(0.5)
    f = s["failures"][0]
    assert (f["file"], f["field"], f["expected"], f["got"]) == ("a.pdf", "total", "99.00", "1.00")


def test_silent_error_failure_rows_carry_issue_codes():
    gts = [gt_entry()]
    recs = [rec(invoices=[pred_inv(total="1.00", issues=[issue("UNCERTAIN_FIELD", "warning")])])]
    f = run(recs, gts)["silent"]["failures"][0]
    assert f["issue_codes"] == ["UNCERTAIN_FIELD"]


def test_extra_invoice_validated_ok_is_silent_error_only_when_file_expects_validated_ok():
    gts = [gt_entry("x.png", invoices=[], status="validated_ok"),
           gt_entry("y.xlsx", invoices=[], status="needs_review", group="sheet_m4")]
    recs = [rec("x.png", file_type="image"), rec("y.xlsx", file_type="sheet")]
    s = run(recs, gts)["silent"]
    assert s["count"] == 1
    assert s["failures"][0]["file"] == "x.png" and s["failures"][0]["kind"] == "extra_invoice"


def test_hallucination_list():
    gts = [gt_entry(invoices=[gt_inv(subtotal=None)])]
    recs = [rec(invoices=[pred_inv(subtotal="90.00", issues=[issue("NOT_GROUNDED", "warning")])])]
    h = run(recs, gts)["errors"]["hallucinations"]
    assert h == [{"file": "a.pdf", "field": "subtotal", "expected": None, "got": "90.00",
                  "issue_codes": ["NOT_GROUNDED"]}]


# ------------------------------------------------------------ skipped handling

def test_skipped_files_excluded_from_every_denominator():
    gts = [gt_entry("a.pdf"), gt_entry("b.pdf"), gt_entry("c.pdf"), gt_entry("d.pdf")]
    recs = [
        rec("a.pdf"),
        rec("b.pdf", state="skipped_quota", status=None, invoices=[], seconds=0.0),
        rec("c.pdf", state="skipped_llm_required", status=None, invoices=[], seconds=0.0),
        rec("d.pdf", state="skipped_not_selected", status=None, invoices=[], seconds=0.0),
    ]
    out = run(recs, gts)
    assert out["coverage"]["counts"] == {"ran": 1, "skipped_quota": 1,
                                         "skipped_llm_required": 1, "skipped_not_selected": 1}
    assert out["header"]["micro"]["n"] == 8
    assert out["silent"]["denominator"] == 1
    assert out["status"]["n"] == 1
    assert out["success"]["n_processable"] == 1
    assert out["latency"]["overall"]["n"] == 1
    assert out["fully_correct"]["denominator"] == 1


def test_eval_false_files_skip_field_metrics_but_stay_in_status_and_latency():
    gts = [gt_entry("t.pdf", group="triage", status="needs_review", invoices=[], eval=False)]
    recs = [rec("t.pdf", status="needs_review", invoices=[pred_inv("needs_review")])]
    out = run(recs, gts)
    assert out["header"]["micro"]["n"] == 0
    assert out["silent"]["denominator"] == 0
    assert out["status"]["matrix"]["needs_review"]["needs_review"] == 1
    assert out["latency"]["overall"]["n"] == 1


# ------------------------------------------------------------ review rate

def test_review_split_correctly_flagged_vs_unnecessary():
    gts = [gt_entry("a.pdf"), gt_entry("b.pdf")]
    recs = [
        rec("a.pdf", status="needs_review", invoices=[pred_inv("needs_review", total="1.00")]),
        rec("b.pdf", status="needs_review", invoices=[pred_inv("needs_review", issues=[issue("MISSING_REQUIRED")])]),
    ]
    r = run(recs, gts)["review"]
    assert r["needs_review"] == 2 and r["n_invoices"] == 2
    assert r["correctly_flagged"] == 1 and r["unnecessary"] == 1
    assert r["rate"] == pytest.approx(1.0)


def test_injected_error_review_counts_as_correctly_flagged():
    gts = [gt_entry("w.pdf", group="injected", status="needs_review", injected={"code": "ARITH_TOTAL"})]
    recs = [rec("w.pdf", status="needs_review",
                invoices=[pred_inv("needs_review", issues=[issue("ARITH_TOTAL")])])]
    r = run(recs, gts)["review"]
    assert r["correctly_flagged"] == 1 and r["unnecessary"] == 0


# ------------------------------------------------------- validation detection

def test_validation_recall_and_false_positive_rate():
    gts = [
        gt_entry("w1.pdf", group="injected", status="needs_review", injected={"code": "ARITH_TOTAL"}),
        gt_entry("w2.pdf", group="injected", status="needs_review", injected={"code": "ARITH_TOTAL"}),
        gt_entry("c1.pdf"), gt_entry("c2.pdf"),
    ]
    recs = [
        rec("w1.pdf", status="needs_review", invoices=[pred_inv("needs_review", issues=[issue("ARITH_TOTAL")])]),
        rec("w2.pdf", invoices=[pred_inv()]),  # injected error missed
        rec("c1.pdf"),
        rec("c2.pdf", status="needs_review",
            invoices=[pred_inv("needs_review", issues=[issue("NOT_GROUNDED")])]),  # false alarm on a correct invoice
    ]
    v = run(recs, gts)["validation"]
    assert v["n_injected"] == 2 and v["detected"] == 1 and v["recall"] == pytest.approx(0.5)
    assert v["by_code"]["ARITH_TOTAL"] == {"n": 2, "detected": 1}
    assert v["n_clean"] == 2 and v["false_positives"] == 1
    assert v["false_positive_rate"] == pytest.approx(0.5)


# ------------------------------------------------------------ status confusion

def test_status_confusion_matrix():
    gts = [gt_entry("a.pdf"), gt_entry("b.pdf"), gt_entry("c.pdf", status="failed", invoices=[],
                                                          expect_error_code="CORRUPT_FILE")]
    recs = [rec("a.pdf"), rec("b.pdf", status="needs_review"),
            rec("c.pdf", status="failed", invoices=[], error_code="CORRUPT_FILE")]
    s = run(recs, gts)["status"]
    assert s["matrix"]["validated_ok"]["validated_ok"] == 1
    assert s["matrix"]["validated_ok"]["needs_review"] == 1
    assert s["matrix"]["failed"]["failed"] == 1
    assert s["accuracy"] == pytest.approx(2 / 3)


# ------------------------------------------------------------ success / failure

def test_success_failure_and_bad_files_handled():
    gts = [
        gt_entry("ok.pdf"), gt_entry("fail.pdf"),
        gt_entry("bad1.pdf", status="failed", invoices=[], expect_error_code="CORRUPT_FILE"),
        gt_entry("bad2.pdf", status="failed", invoices=[], expect_error_code="ENCRYPTED"),
        gt_entry("bad3.pdf", status="failed", invoices=[], expect_error_code="EMPTY_FILE"),
    ]
    recs = [
        rec("ok.pdf"), rec("fail.pdf", status="failed", invoices=[], error_code="INTERNAL_ERROR"),
        rec("bad1.pdf", status="failed", invoices=[], error_code="CORRUPT_FILE"),
        rec("bad2.pdf", status="failed", invoices=[], error_code="CORRUPT_FILE"),   # wrong code
        rec("bad3.pdf", status=None, invoices=[], crash="boom"),                     # crashed
    ]
    s = run(recs, gts)["success"]
    assert s["n_processable"] == 2 and s["success_rate"] == pytest.approx(0.5)
    assert s["failure_rate"] == pytest.approx(0.5)
    assert s["bad_files_total"] == 3 and s["bad_files_handled"] == 1
    assert s["crashes"] == 1


# --------------------------------------------------------------------- latency

def test_percentile_maths():
    assert m.percentile([1, 2, 3, 4, 5], 50) == 3
    assert m.percentile([10], 95) == 10
    assert m.percentile([], 50) is None
    assert m.percentile([1, 2, 3, 4], 50) == pytest.approx(2.5)
    assert m.percentile(list(range(1, 101)), 95) == pytest.approx(95.05)


def test_latency_by_file_type_tier_and_node():
    gts = [gt_entry("a.pdf"), gt_entry("b.xlsx", group="xlsx")]
    recs = [
        rec("a.pdf", seconds=2.0, file_type="pdf",
            history=[{"node": "llm_extract", "seconds": 1.5}, {"node": "validate", "seconds": 0.1}]),
        rec("b.xlsx", seconds=4.0, file_type="sheet",
            invoices=[pred_inv(tier="sheet")], history=[{"node": "sheet_map", "seconds": 0.2}]),
    ]
    lat = run(recs, gts)["latency"]
    assert lat["overall"]["mean"] == pytest.approx(3.0) and lat["overall"]["n"] == 2
    assert lat["by_file_type"]["pdf"]["p50"] == 2.0 and lat["by_file_type"]["sheet"]["p50"] == 4.0
    assert lat["by_tier"]["parser"]["n"] == 1 and lat["by_tier"]["sheet"]["n"] == 1
    assert lat["by_node"]["llm_extract"]["mean"] == pytest.approx(1.5)


# -------------------------------------------------------------- tier + sheets

def test_tier_usage_and_llm_calls_per_doc():
    gts = [gt_entry("a.pdf"), gt_entry("b.pdf")]
    recs = [rec("a.pdf", llm_calls=1, invoices=[pred_inv(tier="parser")]),
            rec("b.pdf", llm_calls=3, invoices=[pred_inv(tier="vision")])]
    t = run(recs, gts)["tiers"]
    assert t["share"]["parser"] == pytest.approx(0.5) and t["share"]["vision"] == pytest.approx(0.5)
    assert t["vision_escalation_rate"] == pytest.approx(0.5)
    assert t["llm_calls_per_doc"]["mean"] == pytest.approx(2.0)
    assert t["total_input_tokens"] == 20


def test_sheet_efficiency_zero_llm_layouts():
    gts = [gt_entry("s1.xlsx", group="sheet_m4"), gt_entry("s2.xlsx", group="sheet_m4"),
           gt_entry("p.pdf")]
    sm = [{"node": "sheet_map", "seconds": 0.1}]
    recs = [
        rec("s1.xlsx", file_type="sheet", history=sm, llm_calls=0, by_schema={}),
        rec("s2.xlsx", file_type="sheet", history=sm, llm_calls=1, by_schema={"SheetMapping": 1}),
        rec("p.pdf"),
    ]
    e = run(recs, gts)["sheet_efficiency"]
    assert e["n_layouts"] == 2 and e["zero_llm"] == 1 and e["rate"] == pytest.approx(0.5)


# ---------------------------------------------------------------- segmentation

def test_segmentation_boundary_accuracy():
    gts = [gt_entry("multi.pdf", group="multi_invoice",
                    invoices=[gt_inv(invoice_number="A"), gt_inv(invoice_number="B"), gt_inv(invoice_number="C")],
                    page_ranges=[{"inv_number": "A", "pages": [0]}, {"inv_number": "B", "pages": [1, 2]},
                                 {"inv_number": "C", "pages": [3]}])]
    recs = [rec("multi.pdf", invoices=[pred_inv(invoice_number="A", pages=(0,)),
                                        pred_inv(invoice_number="B", pages=(1,)),
                                        pred_inv(invoice_number="C", pages=(2, 3))])]
    s = run(recs, gts)["segmentation"]
    assert s["n_files"] == 1 and s["exact_files"] == 0
    assert s["gt_boundaries"] == 3 and s["correct_boundaries"] == 1
    assert s["recall"] == pytest.approx(1 / 3) and s["precision"] == pytest.approx(1 / 3)


def test_segmentation_exact_match():
    gts = [gt_entry("multi.pdf", group="multi_invoice",
                    invoices=[gt_inv(invoice_number="A"), gt_inv(invoice_number="B")],
                    page_ranges=[{"inv_number": "A", "pages": [0]}, {"inv_number": "B", "pages": [1, 2]}])]
    recs = [rec("multi.pdf", invoices=[pred_inv(invoice_number="A", pages=(0,)),
                                        pred_inv(invoice_number="B", pages=(1, 2))])]
    s = run(recs, gts)["segmentation"]
    assert s["exact_files"] == 1 and s["recall"] == 1.0


# --------------------------------------------------------------- fully correct

def test_fully_correct_rate_counts_missed_invoices_in_denominator():
    gts = [gt_entry("a.pdf", invoices=[gt_inv(invoice_number="A"), gt_inv(invoice_number="B", total="5.00")])]
    recs = [rec("a.pdf", invoices=[pred_inv(invoice_number="A")])]
    fc = run(recs, gts)["fully_correct"]
    assert fc["denominator"] == 2 and fc["count"] == 1 and fc["rate"] == pytest.approx(0.5)
