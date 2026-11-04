"""Run the pipeline in-process over the ground-truth files and produce per-file run records (M10).

No API, DB or queue is involved: `app.pipeline.graph.process` is called directly.
Quota handling lives here, not in the pipeline: every LLM call goes through `EvalLLM`, which counts
attempts (including failed ones) and records rate-limit / quota errors, so a file whose LLM calls were
throttled is recorded as `skipped_quota` instead of as a pipeline result.

File states: ran | skipped_quota | skipped_llm_error | skipped_llm_required | skipped_not_selected.
"""
from __future__ import annotations

import json
import platform
import subprocess
import tempfile
import threading
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from app.pipeline.llm.client import LLMConfigError, LLMTransientError, Usage
from app.pipeline.validation.duplicates import InMemorySeenStore, compute_dup_key

from scripts.eval_metrics import STATE_RAN, values_equal, FIELDS

STATE_QUOTA = "skipped_quota"
STATE_LLM_ERROR = "skipped_llm_error"
STATE_LLM_REQUIRED = "skipped_llm_required"
STATE_NOT_SELECTED = "skipped_not_selected"

# no-LLM groups first, then the most informative LLM groups (injected, digital) so a quota-limited run covers them first
GROUP_ORDER = ["bad_files", "edge", "xlsx", "xls", "csv", "sheet_m4", "sheet_m4_perf",
               "injected", "digital_pdf", "multi_invoice", "scanned_pdf", "image_m5", "image", "triage"]

SHEET_EXT = {".xlsx", ".xls", ".csv"}
QUOTA_COOLDOWN_S = 60
MAX_CONSECUTIVE_QUOTA = 3

_QUOTA_MARKERS = ("429", "resource_exhausted", "quota", "rate limit", "rate_limit", "ratelimit",
                  "too many requests")
_DAILY_MARKERS = ("per day", "perday", "daily", "per-day")


# ------------------------------------------------------------------ GT + hints

def load_gt(path: Path) -> list[dict]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def needs_llm_hint(entry: dict) -> str:
    """'no' (fails at intake), 'maybe' (sheets: only if the deterministic mapping is incomplete), 'yes'."""
    if entry.get("expect_error_code"):
        return "no"
    return "maybe" if Path(entry["file"]).suffix.lower() in SHEET_EXT else "yes"


def dup_key(gt_invoice: dict) -> str | None:
    return compute_dup_key(gt_invoice)


def dup_seed_keys(entry: dict, entries: list[dict]) -> list[str]:
    """Keys to pre-seed in the per-file seen store: the originals of an injected DUPLICATE_INVOICE."""
    if (entry.get("injected") or {}).get("code") != "DUPLICATE_INVOICE":
        return []
    originals = set()
    for e in entries:
        if e["file"] == entry["file"] or e.get("injected"):
            continue
        for inv in e.get("invoices") or []:
            k = dup_key(inv)
            if k:
                originals.add(k)
    return [k for k in (dup_key(i) for i in entry.get("invoices") or []) if k in originals]


# ---------------------------------------------------------------- quota logic

def _chain_text(exc: BaseException | None) -> str:
    parts, seen = [], set()
    while exc is not None and id(exc) not in seen:
        seen.add(id(exc))
        parts.append(f"{type(exc).__name__}: {exc}")
        exc = exc.__cause__ or exc.__context__
    return " | ".join(parts)


def is_quota_error(exc: BaseException) -> bool:
    text = _chain_text(exc).lower()
    return any(m in text for m in _QUOTA_MARKERS)


def is_daily_quota(exc: BaseException) -> bool:
    text = _chain_text(exc).lower()
    return is_quota_error(exc) and any(m in text for m in _DAILY_MARKERS)


_CONFIG_MARKERS = ("404", "no longer available", "not found", "api key", "permission denied", "not supported")


class CallBudget:
    """Hard cap on LLM calls shared by all files of one invocation."""

    def __init__(self, limit: int | None) -> None:
        self.limit = limit
        self.used = 0
        self._lock = threading.Lock()

    def take(self) -> bool:
        with self._lock:
            if self.limit is not None and self.used >= self.limit:
                return False
            self.used += 1
            return True

    @property
    def exhausted(self) -> bool:
        return self.limit is not None and self.used >= self.limit


class EvalLLM:
    """LLMClient proxy: counts attempts, sums usage, flags quota / config errors, optional tripwire."""

    def __init__(self, inner: Any | None, tripwire: bool = False, budget: CallBudget | None = None) -> None:
        self.inner = inner
        self.tripwire = tripwire
        self.budget = budget
        self.budget_hit = False
        self._lock = threading.Lock()
        self.attempts = 0
        self.by_schema: Counter = Counter()
        self.usage = Usage()
        self.tripped = False
        self.quota_error: str | None = None
        self.daily_quota = False
        self.config_error: str | None = None

    def extract(self, schema, system, user, *, images=None, model):
        with self._lock:
            self.attempts += 1
            self.by_schema[getattr(schema, "__name__", str(schema))] += 1
            if self.tripwire:
                self.tripped = True
        if self.tripwire:
            raise LLMConfigError("no-llm tripwire: this file needs the LLM")
        if self.budget is not None and not self.budget.take():
            self.budget_hit = True
            raise LLMConfigError("--max-llm-calls budget reached")
        if self.inner is None:
            raise LLMConfigError("no LLM client configured (set GOOGLE_API_KEY)")
        try:
            out, usage = self.inner.extract(schema, system, user, images=images, model=model)
        except Exception as exc:  # noqa: BLE001 - recorded, then re-raised for the pipeline to handle
            with self._lock:
                if is_quota_error(exc):
                    self.quota_error = _chain_text(exc)[:300]
                    self.daily_quota = self.daily_quota or is_daily_quota(exc)
                elif isinstance(exc, LLMConfigError) or any(
                        m in _chain_text(exc).lower() for m in _CONFIG_MARKERS):
                    self.config_error = _chain_text(exc)[:300]
            raise
        with self._lock:
            self.usage = self.usage + usage
        return out, usage


# ---------------------------------------------------------------- records

def slim_result(res: dict) -> dict:
    """Keep only what the metrics need from a PipelineResult.model_dump(mode='json')."""
    invs = []
    for inv in res.get("invoices") or []:
        s = {f: inv.get(f) for f in FIELDS}
        s.update({
            "unit_id": inv.get("unit_id"), "status": inv.get("status"), "tier": inv.get("tier"),
            "source_pages": inv.get("source_pages") or [],
            "line_items": [{k: li.get(k) for k in ("description", "quantity", "unit_price", "amount")}
                           for li in inv.get("line_items") or []],
            "issues": [{"code": i.get("code"), "severity": i.get("severity"), "field": i.get("field")}
                       for i in inv.get("issues") or []],
        })
        invs.append(s)
    ec = res.get("error_code")
    return {
        "status": res.get("status"),
        "error_code": getattr(ec, "value", ec),
        "file_type": res.get("file_type"),
        "page_count": res.get("page_count"),
        "invoices": invs,
        "warnings": sorted({w.get("code") for w in res.get("warnings") or [] if w.get("code")}),
        "history": [{"node": h.get("node"), "seconds": h.get("seconds"), "ok": h.get("ok")}
                    for h in res.get("history") or []],
        "pipeline_llm_usage": res.get("llm_usage") or {},
    }


def _base_record(entry: dict, state: str, reason: str | None = None) -> dict:
    return {"file": entry["file"], "group": entry["group"], "state": state, "reason": reason,
            "status": None, "error_code": None, "file_type": None, "seconds": None, "crash": None,
            "invoices": [], "warnings": [], "history": [], "llm_calls": 0, "llm_calls_by_schema": {},
            "input_tokens": 0, "output_tokens": 0}


def default_process(path: str, deps: dict) -> dict:
    from app.pipeline.graph import process
    return process(path, deps=deps).model_dump(mode="json")


def default_make_deps(cfg) -> Callable:
    def make(llm, store):
        return {"config": cfg, "llm": llm, "seen_store": store, "mapping_cache": None}
    return make


def run_one(entry: dict, entries: list[dict], fixtures: Path, *, llm_inner: Any, tripwire: bool,
            process_fn: Callable, make_deps: Callable,
            budget: CallBudget | None = None) -> tuple[dict, EvalLLM]:
    proxy = EvalLLM(llm_inner, tripwire=tripwire, budget=budget)
    store = InMemorySeenStore(dup_seed_keys(entry, entries))
    deps = make_deps(proxy, store)
    rec = _base_record(entry, STATE_RAN)
    t0 = time.perf_counter()
    try:
        res = process_fn(str(Path(fixtures) / entry["file"]), deps)
        rec.update(slim_result(res))
    except Exception as exc:  # noqa: BLE001 - an unexpected pipeline crash is itself a result
        rec["crash"] = f"{type(exc).__name__}: {exc}"
    rec["seconds"] = round(time.perf_counter() - t0, 4)
    rec["llm_calls"] = proxy.attempts
    rec["llm_calls_by_schema"] = dict(proxy.by_schema)
    rec["input_tokens"] = proxy.usage.input_tokens
    rec["output_tokens"] = proxy.usage.output_tokens
    if proxy.budget_hit:
        rec.update(state=STATE_NOT_SELECTED, reason="--max-llm-calls reached (partial result discarded)")
    elif proxy.tripped:
        rec.update(state=STATE_LLM_REQUIRED, reason="--no-llm: file needs an LLM call")
    elif proxy.quota_error:
        rec.update(state=STATE_QUOTA, reason=f"LLM quota/rate limit: {proxy.quota_error}")
    elif proxy.config_error:
        rec.update(state=STATE_LLM_ERROR, reason=f"LLM config error: {proxy.config_error}")
    return rec, proxy


def _order(entries: list[dict], groups: list[str] | None) -> list[dict]:
    rank = {g: i for i, g in enumerate(groups if groups else GROUP_ORDER)}
    chosen = [e for e in entries if not groups or e["group"] in rank]
    return sorted(chosen, key=lambda e: (rank.get(e["group"], len(rank)), e["file"]))


def run_eval(entries: list[dict], *, fixtures: Path, existing: dict[str, dict], groups: list[str] | None,
             no_llm: bool, max_llm_files: int | None, sleep_between_llm: float, llm_inner: Any,
             process_fn: Callable = default_process, make_deps: Callable | None = None,
             sleep_fn: Callable = time.sleep, quota_cooldown_s: float = QUOTA_COOLDOWN_S,
             only_files: set[str] | None = None, files: list[str] | None = None,
             max_llm_calls: int | None = None, log: Callable = lambda m: None) -> dict[str, dict]:
    make_deps = make_deps or default_make_deps(None)
    records = dict(existing)
    llm_files = 0
    consecutive_quota = 0
    daily_hit = False
    pool = [e for e in entries if only_files is None or e["file"] in only_files]
    budget = CallBudget(max_llm_calls) if max_llm_calls is not None else None
    if files:
        by_name = {e["file"]: e for e in pool}
        ordered = [by_name[f] for f in files if f in by_name]
    else:
        ordered = _order(pool, groups)

    for entry in ordered:
        name = entry["file"]
        if records.get(name, {}).get("state") == STATE_RAN:
            log(f"keep   {name} (already ran)")
            continue
        hint = needs_llm_hint(entry)
        budget_left = max_llm_files is None or llm_files < max_llm_files

        if hint == "yes" and no_llm:
            records[name] = _base_record(entry, STATE_LLM_REQUIRED, "--no-llm: pdf/image always needs the LLM")
            continue
        if hint == "yes" and daily_hit:
            records[name] = _base_record(entry, STATE_QUOTA, "stopped after daily quota")
            log(f"skip   {name} (stopped after daily quota)")
            continue
        if hint == "yes" and budget is not None and budget.exhausted:
            records[name] = _base_record(entry, STATE_NOT_SELECTED, "--max-llm-calls reached")
            continue
        if hint == "yes" and not budget_left:
            records[name] = _base_record(entry, STATE_NOT_SELECTED, "--max-llm-files reached")
            continue

        tripwire = no_llm or hint == "no" or daily_hit or not budget_left
        rec, proxy = run_one(entry, entries, fixtures, llm_inner=llm_inner, tripwire=tripwire,
                             process_fn=process_fn, make_deps=make_deps, budget=budget)
        records[name] = rec
        log(f"{rec['state']:<22s} {name}  {rec['status']}  {rec['seconds']}s  llm_calls={rec['llm_calls']}")

        if rec["state"] == STATE_QUOTA:
            consecutive_quota += 1
            if proxy.daily_quota or consecutive_quota >= MAX_CONSECUTIVE_QUOTA:
                daily_hit = True
            elif quota_cooldown_s:
                sleep_fn(quota_cooldown_s)
        elif rec["llm_calls"]:
            consecutive_quota = 0
        if rec["llm_calls"] and rec["state"] == STATE_RAN:
            llm_files += 1
            if sleep_between_llm:
                sleep_fn(sleep_between_llm)

    for e in entries:
        records.setdefault(e["file"], _base_record(e, STATE_NOT_SELECTED, "outside --groups"))
    return records


# ----------------------------------------------------------- run comparison

def diff_runs(a: dict[str, dict], b: dict[str, dict]) -> dict:
    """File-level differences between two runs over the files that ran in both."""
    both = [f for f in a if f in b and a[f]["state"] == STATE_RAN and b[f]["state"] == STATE_RAN]
    changed = []
    for f in both:
        ra, rb = a[f], b[f]
        reasons = []
        if ra["status"] != rb["status"]:
            reasons.append(f"status {ra['status']} -> {rb['status']}")
        if len(ra["invoices"]) != len(rb["invoices"]):
            reasons.append(f"invoices {len(ra['invoices'])} -> {len(rb['invoices'])}")
        else:
            for ia, ib in zip(ra["invoices"], rb["invoices"]):
                for fld in FIELDS:
                    ga, gb = ia.get(fld), ib.get(fld)
                    if (ga is None) != (gb is None) or (ga is not None and not values_equal(fld, ga, gb)):
                        reasons.append(f"{fld}: {ga!r} -> {gb!r}")
        if reasons:
            changed.append({"file": f, "changes": reasons})
    return {"n_compared": len(both), "n_changed": len(changed), "changed": changed}


# ------------------------------------------------------------------- metadata

def _git(*args: str) -> str:
    try:
        return subprocess.run(["git", *args], capture_output=True, text=True, timeout=10).stdout.strip()
    except Exception:  # noqa: BLE001
        return "unknown"


def _pkg(name: str) -> str:
    try:
        from importlib.metadata import version
        return version(name)
    except Exception:  # noqa: BLE001
        return "n/a"


def collect_meta(cfg, fixtures_seed: int) -> dict:
    import os
    dirty = bool(_git("status", "--porcelain"))
    return {
        "date": datetime.now().astimezone().isoformat(timespec="seconds"),
        "commit": _git("rev-parse", "--short", "HEAD") + (" (+uncommitted changes)" if dirty else ""),
        "machine": f"{platform.system()} {platform.release()}, {platform.processor() or platform.machine()}, "
                   f"{os.cpu_count()} logical CPUs",
        "python": platform.python_version(),
        "models": {"text": cfg.LLM_MODEL_TEXT, "vision": cfg.LLM_MODEL_VISION},
        "llm_concurrency": cfg.LLM_CONCURRENCY,
        "packages": {p: _pkg(p) for p in ("langgraph", "langchain-google-genai", "pydantic",
                                          "pymupdf", "rapidocr-onnxruntime", "pandas")},
        "fixtures_seed": fixtures_seed,
        "mode": "in-process: app.pipeline.graph.process(); no API, DB or Celery queue",
    }


def build_real_llm(cfg):
    from dotenv import load_dotenv
    load_dotenv()
    from app.pipeline.llm.client import build_llm_client
    return build_llm_client(cfg)


# ------------------------------------------------------------- optional benches

def throughput_bench(records: dict[str, dict], fixtures: Path, cfg, workers: tuple[int, ...] = (1, 4)) -> dict:
    """Re-run the files that needed zero LLM calls at each concurrency; wall-clock only."""
    files = [f for f, r in records.items() if r["state"] == STATE_RAN and r["llm_calls"] == 0
             and r.get("status") is not None]
    out = {"n_files": len(files), "files": sorted(files), "runs": {}}
    mk = default_make_deps(cfg)

    def one(f: str) -> None:
        try:
            default_process(str(Path(fixtures) / f), mk(EvalLLM(None, tripwire=True), InMemorySeenStore()))
        except Exception:  # noqa: BLE001
            pass
    for w in workers:
        t0 = time.perf_counter()
        if w == 1:
            for f in files:
                one(f)
        else:
            with ThreadPoolExecutor(max_workers=w) as pool:
                list(pool.map(one, files))
        secs = time.perf_counter() - t0
        out["runs"][str(w)] = {"seconds": round(secs, 3),
                               "files_per_min": round(len(files) / secs * 60, 1) if secs else None}
    return out


def parser_bench(n_pages: int, cfg) -> dict:
    """Parser stages only (intake + pdf_triage + pdf_text), no LLM, on a generated digital PDF."""
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas
    from app.pipeline.nodes.intake import make_intake
    from app.pipeline.nodes.pdf_text import pdf_text
    from app.pipeline.nodes.pdf_triage import pdf_triage

    with tempfile.TemporaryDirectory(prefix="dociq_bench_") as tmp:
        path = Path(tmp) / "bench_large.pdf"
        c = canvas.Canvas(str(path), pagesize=A4)
        for p in range(n_pages):
            c.setFont("Helvetica", 10)
            c.drawString(50, 800, f"Invoice INV-BENCH-{p:05d}   Page {p + 1} of {n_pages}")
            for i in range(40):
                y = 770 - i * 16
                c.drawString(50, y, f"Line {i + 1}  Consulting Services item {i}")
                c.drawString(380, y, f"{i + 1}  x  {10 + i}.00   =   {(i + 1) * (10 + i)}.00")
            c.showPage()
        c.save()
        size_mb = path.stat().st_size / (1024 * 1024)
        state: dict = {"file_path": str(path), "history": []}
        timings = {}
        steps = [("intake", make_intake({"config": cfg})), ("pdf_triage", pdf_triage), ("pdf_text", pdf_text)]
        for name, fn in steps:
            t0 = time.perf_counter()
            upd = fn(state) or {}
            timings[name] = round(time.perf_counter() - t0, 3)
            for k, v in upd.items():
                if k == "history":
                    state["history"] = state["history"] + v
                else:
                    state[k] = v
            if state.get("error"):
                return {"pages": n_pages, "error": state.get("error"), "error_code": str(state.get("error_code"))}
    total = sum(timings.values())
    return {"pages": n_pages, "size_mb": round(size_mb, 2), "seconds_by_stage": timings,
            "total_seconds": round(total, 3),
            "pages_per_min": round(n_pages / total * 60, 1) if total else None,
            "label": "parser stages only (intake, pdf_triage, pdf_text); no OCR, no LLM, not end-to-end"}
