"""Unit tests for scripts/eval_runner.py (M10): quota handling, selection, resume. No network."""
from __future__ import annotations

from pathlib import Path

import pytest

from app.pipeline.llm.client import LLMConfigError, LLMTransientError, Usage
from scripts import eval_runner as er


# ------------------------------------------------------------------ helpers

class Quota429(Exception):
    pass


def transient_429() -> LLMTransientError:
    try:
        try:
            raise Quota429("429 RESOURCE_EXHAUSTED: rate limit")
        except Quota429 as inner:
            raise LLMTransientError("Transient LLM error after 3 retries") from inner
    except LLMTransientError as e:
        return e


class FakeInner:
    """Real-client stand-in: returns usage, or raises the configured exception."""

    def __init__(self, exc: Exception | None = None):
        self.exc = exc
        self.calls = 0

    def extract(self, schema, system, user, *, images=None, model):
        self.calls += 1
        if self.exc:
            raise self.exc
        return object(), Usage(calls=1, input_tokens=7, output_tokens=3)


def result_dict(status="validated_ok", invoices=None, error_code=None):
    return {"status": status, "error_code": error_code, "file_type": "pdf", "page_count": 1,
            "invoices": invoices if invoices is not None else [], "warnings": [],
            "history": [{"node": "intake", "seconds": 0.01, "ok": True, "attempt": 0, "meta": {}}],
            "llm_usage": {}}


def entry(file, group="digital_pdf", status="validated_ok", **kw):
    e = {"file": file, "group": group, "expect_status": status, "injected": None, "invoices": []}
    e.update(kw)
    return e


def make_deps(llm, store):
    return {"llm": llm, "seen_store": store}


def run(entries, process_fn, **kw):
    params = dict(fixtures=Path("."), existing={}, groups=None, no_llm=False, max_llm_files=None,
                  sleep_between_llm=0, llm_inner=FakeInner(), process_fn=process_fn,
                  make_deps=make_deps, sleep_fn=lambda s: None, quota_cooldown_s=0,
                  files=None, max_llm_calls=None)
    params.update(kw)
    return er.run_eval(entries, **params)


def calling(n=1):
    """process_fn that makes n LLM calls through deps['llm'] and returns a clean result."""
    def fn(path, deps):
        for _ in range(n):
            deps["llm"].extract(dict, "s", "u", model="m")
        return result_dict()
    return fn


# ------------------------------------------------------------ quota detection

def test_quota_detection_transient_wrapping_429():
    assert er.is_quota_error(transient_429()) is True


def test_quota_detection_daily_config_error():
    exc = LLMConfigError("429 You exceeded your current quota, limit per day reached")
    assert er.is_quota_error(exc) is True
    assert er.is_daily_quota(exc) is True


def test_non_quota_errors_not_matched():
    assert er.is_quota_error(ValueError("bad json")) is False
    assert er.is_quota_error(LLMConfigError("API key not valid")) is False
    assert er.is_daily_quota(transient_429()) is False


# --------------------------------------------------------------- llm hints

def test_needs_llm_hint():
    assert er.needs_llm_hint(entry("a.pdf")) == "yes"
    assert er.needs_llm_hint(entry("a.PNG")) == "yes"
    assert er.needs_llm_hint(entry("a.xlsx")) == "maybe"
    assert er.needs_llm_hint(entry("a.csv")) == "maybe"
    assert er.needs_llm_hint(entry("bad.pdf", expect_error_code="CORRUPT_FILE")) == "no"


# ------------------------------------------------------------ record building

def test_ran_record_has_counts_and_slim_invoices():
    inv = {"invoice_number": "X", "status": "validated_ok", "tier": "parser", "source_pages": [0],
           "issues": [{"code": "UNCERTAIN_FIELD", "severity": "warning", "field": "total",
                       "message": "m", "expected": None, "got": None, "location": {}}],
           "line_items": [{"position": 1, "description": "W", "quantity": "1", "unit_price": "2", "amount": "2"}],
           "total": "2", "extra": {"junk": 1}}
    recs = run([entry("a.pdf")], lambda p, d: (d["llm"].extract(dict, "", "", model="m"),
                                               result_dict(invoices=[inv]))[1])
    r = recs["a.pdf"]
    assert r["state"] == "ran" and r["status"] == "validated_ok"
    assert r["llm_calls"] == 1 and r["input_tokens"] == 7 and r["output_tokens"] == 3
    assert r["invoices"][0]["issues"] == [{"code": "UNCERTAIN_FIELD", "severity": "warning", "field": "total"}]
    assert "extra" not in r["invoices"][0]
    assert r["seconds"] >= 0


def test_process_exception_is_recorded_as_crash_not_raised():
    def boom(path, deps):
        raise RuntimeError("kaput")
    r = run([entry("a.pdf")], boom)["a.pdf"]
    assert r["state"] == "ran" and r["status"] is None and "kaput" in r["crash"]


# -------------------------------------------------------------------- tripwire

def test_no_llm_marks_files_that_call_llm_as_skipped_llm_required():
    entries = [entry("needs.xlsx", group="sheet_m4"), entry("free.xlsx", group="sheet_m4")]

    def fn(path, deps):
        if "needs" in str(path):
            deps["llm"].extract(dict, "", "", model="m")   # tripwire raises LLMConfigError
        return result_dict()
    inner = FakeInner()
    recs = run(entries, fn, no_llm=True, llm_inner=inner)
    assert recs["needs.xlsx"]["state"] == "skipped_llm_required"
    assert recs["free.xlsx"]["state"] == "ran" and recs["free.xlsx"]["llm_calls"] == 0
    assert inner.calls == 0, "no real LLM call may be made with --no-llm"


def test_no_llm_does_not_even_run_pdfs_and_images():
    called = []
    recs = run([entry("a.pdf"), entry("b.png")], lambda p, d: called.append(p) or result_dict(), no_llm=True)
    assert called == []
    assert recs["a.pdf"]["state"] == recs["b.png"]["state"] == "skipped_llm_required"


def test_no_llm_still_runs_bad_files():
    bad = entry("bad.pdf", group="bad_files", status="failed", expect_error_code="CORRUPT_FILE")
    recs = run([bad], lambda p, d: result_dict("failed", error_code="CORRUPT_FILE"), no_llm=True)
    assert recs["bad.pdf"]["state"] == "ran" and recs["bad.pdf"]["error_code"] == "CORRUPT_FILE"


# ----------------------------------------------------------------------- quota

def test_quota_hit_marks_file_skipped_quota_even_if_pipeline_returned_a_result():
    # llm_extract swallows LLMTransientError; the doc still comes back "needs_review"
    def fn(path, deps):
        try:
            deps["llm"].extract(dict, "", "", model="m")
        except LLMTransientError:
            pass
        return result_dict("needs_review")
    recs = run([entry("a.pdf")], fn, llm_inner=FakeInner(transient_429()))
    r = recs["a.pdf"]
    assert r["state"] == "skipped_quota" and "429" in r["reason"] + str(r.get("quota_error", ""))


def test_daily_quota_stops_further_llm_files_but_not_free_ones():
    entries = [entry("a.pdf"), entry("b.pdf"), entry("free.xlsx", group="sheet_m4"),
               entry("bad.pdf", group="bad_files", status="failed", expect_error_code="CORRUPT_FILE")]
    exc = LLMConfigError("429 quota exceeded: limit per day")

    def fn(path, deps):
        if str(path).endswith(("a.pdf", "b.pdf")):
            deps["llm"].extract(dict, "", "", model="m")
        return result_dict()
    inner = FakeInner(exc)
    recs = run(entries, fn, llm_inner=inner)
    assert recs["a.pdf"]["state"] == "skipped_quota"
    assert recs["b.pdf"]["state"] == "skipped_quota" and "stopped after daily quota" in recs["b.pdf"]["reason"]
    assert inner.calls == 1
    assert recs["free.xlsx"]["state"] == "ran" and recs["bad.pdf"]["state"] == "ran"


def test_non_quota_config_error_is_skipped_llm_error_not_a_pipeline_failure():
    def fn(path, deps):
        deps["llm"].extract(dict, "", "", model="m")
        return result_dict("failed", error_code="LLM_CONFIG_ERROR")
    recs = run([entry("a.pdf")], fn, llm_inner=FakeInner(LLMConfigError("model not found")))
    assert recs["a.pdf"]["state"] == "skipped_llm_error"


# ------------------------------------------------------------------- selection

def test_groups_filter_leaves_others_not_selected():
    entries = [entry("a.pdf", group="digital_pdf"), entry("b.pdf", group="scanned_pdf")]
    recs = run(entries, calling(), groups=["scanned_pdf"])
    assert recs["a.pdf"]["state"] == "skipped_not_selected"
    assert recs["b.pdf"]["state"] == "ran"


def test_max_llm_files_caps_llm_files_only():
    entries = [entry(f"{i}.pdf") for i in range(4)] + [entry("free.xlsx", group="sheet_m4")]
    def fn(path, deps):
        if str(path).endswith(".pdf"):
            deps["llm"].extract(dict, "", "", model="m")
        return result_dict()
    recs = run(entries, fn, max_llm_files=2)
    states = [recs[f"{i}.pdf"]["state"] for i in range(4)]
    assert states == ["ran", "ran", "skipped_not_selected", "skipped_not_selected"]
    assert recs["free.xlsx"]["state"] == "ran"


def test_sleep_between_llm_only_after_files_that_called_the_llm():
    slept = []
    entries = [entry("a.pdf"), entry("free.xlsx", group="sheet_m4")]

    def fn(path, deps):
        if str(path).endswith("a.pdf"):
            deps["llm"].extract(dict, "", "", model="m")
        return result_dict()
    run(entries, fn, sleep_between_llm=5, sleep_fn=slept.append)
    assert slept == [5]


def test_group_order_runs_injected_before_digital_by_default():
    order = []
    entries = [entry("d.pdf", group="digital_pdf"), entry("i.pdf", group="injected")]
    run(entries, lambda p, d: order.append(Path(p).name) or result_dict())
    assert order == ["i.pdf", "d.pdf"]


# ---------------------------------------------------------------------- resume

def test_resume_keeps_ran_and_retries_skipped_quota():
    existing = {"a.pdf": {"file": "a.pdf", "state": "ran", "status": "validated_ok", "marker": "old"},
                "b.pdf": {"file": "b.pdf", "state": "skipped_quota", "reason": "429"}}
    called = []
    recs = run([entry("a.pdf"), entry("b.pdf")], lambda p, d: called.append(Path(p).name) or result_dict(),
               existing=existing)
    assert called == ["b.pdf"]
    assert recs["a.pdf"]["marker"] == "old"
    assert recs["b.pdf"]["state"] == "ran"


# ------------------------------------------------------------------ duplicates

def test_duplicate_entry_gets_preseeded_seen_store_and_clean_files_do_not():
    gt_inv = {"invoice_number": "INV-A-1", "supplier_name": "Acme", "total": "10.00", "line_items": []}
    entries = [entry("orig.pdf", invoices=[gt_inv]),
               entry("dup.pdf", group="injected", status="needs_review",
                     injected={"code": "DUPLICATE_INVOICE"}, invoices=[dict(gt_inv)]),
               entry("other.pdf", invoices=[dict(gt_inv)])]
    seen = {}

    def fn(path, deps):
        key = er.dup_key(gt_inv)
        seen[Path(path).name] = deps["seen_store"].contains(key)
        return result_dict()
    run(entries, fn)
    assert seen["dup.pdf"] is True
    assert seen["orig.pdf"] is False and seen["other.pdf"] is False


# ------------------------------------------------- --files, call budget, 404

def test_files_flag_runs_only_those_in_given_order():
    order = []
    entries = [entry("a.pdf"), entry("b.pdf"), entry("c.pdf")]
    recs = run(entries, lambda p, d: order.append(Path(p).name) or result_dict(), files=["c.pdf", "a.pdf"])
    assert order == ["c.pdf", "a.pdf"]
    assert recs["b.pdf"]["state"] == "skipped_not_selected"


def test_call_budget_is_a_hard_cap_and_discards_the_file_that_hits_it():
    entries = [entry("a.pdf"), entry("b.pdf"), entry("c.pdf")]
    inner = FakeInner()

    def fn(path, deps):
        deps["llm"].extract(dict, "", "", model="m")
        deps["llm"].extract(dict, "", "", model="m")
        return result_dict()
    recs = run(entries, fn, files=["a.pdf", "b.pdf", "c.pdf"], max_llm_calls=3, llm_inner=inner)
    assert inner.calls == 3
    assert recs["a.pdf"]["state"] == "ran"
    assert recs["b.pdf"]["state"] == "skipped_not_selected" and "max-llm-calls" in recs["b.pdf"]["reason"]
    assert recs["c.pdf"]["state"] == "skipped_not_selected"


def test_model_404_is_llm_error_not_a_pipeline_result():
    exc = RuntimeError("404 This model models/gemini-2.5-pro is no longer available to new users.")

    def fn(path, deps):
        try:
            deps["llm"].extract(dict, "", "", model="m")
        except RuntimeError:
            pass   # pipeline swallows it as VISION_FAILED
        return result_dict("needs_review")
    recs = run([entry("a.pdf")], fn, llm_inner=FakeInner(exc))
    assert recs["a.pdf"]["state"] == "skipped_llm_error"
