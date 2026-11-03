"""Run the document intelligence pipeline on one or more files and print a results table.

Usage (offline crash-test — NullLLM, no network):
    python -m scripts.run_pipeline data/fixtures/*

Usage (--live, real LLM from .env):
    python -m scripts.run_pipeline data/fixtures/digital_A_001.pdf --live

Offline mode uses NullLLM: any file that reaches an LLM node shows
status=failed(LLM_CONFIG_ERROR). This is a **crash test** — it proves the
non-LLM pipeline stages (intake, triage, OCR, sheet, segmentation) don't crash.
The --live table is the real proof-of-work.
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path


def _fmt_row(file: str, ftype: str, status: str, n_inv: int,
             n_iss: int, secs: float, tier: str) -> str:
    return (
        f"  {file:<35s}  {ftype:<7s}  {status:<14s}  "
        f"{n_inv:>6}  {n_iss:>6}  {secs:>7.2f}s  {tier}"
    )


def _header() -> str:
    return _fmt_row("file", "type", "status", 0, 0, 0.0, "tier").replace(
        "     0", "  #inv"
    ).replace(
        "     0", "  #iss"
    ).replace(
        "  0.00s", "    sec"
    )


def _dominant_tier(result) -> str:
    tiers = [
        (inv.tier.value if hasattr(inv.tier, "value") else str(inv.tier))
        for inv in result.invoices
    ]
    if not tiers:
        return "-"
    return max(set(tiers), key=tiers.count)


def _issue_count(result) -> int:
    total = 0
    for inv in result.invoices:
        total += len(inv.issues)
    total += len(result.warnings)
    return total


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run the document intelligence pipeline on files."
    )
    parser.add_argument("files", nargs="+", help="Files to process")
    parser.add_argument(
        "--live", action="store_true",
        help="Use real LLM client (requires GOOGLE_API_KEY in .env)"
    )
    args = parser.parse_args(argv)

    if args.live:
        from dotenv import load_dotenv
        load_dotenv()
        from app.pipeline.config import get_config
        from app.pipeline.llm.client import build_llm_client
        from app.pipeline.state import Deps
        cfg = get_config()
        try:
            llm = build_llm_client(cfg)
        except Exception as exc:
            print(f"ERROR: could not create LLMClient: {exc}", file=sys.stderr)
            return 1
        deps = Deps(config=cfg, llm=llm, seen_store=None, mapping_cache=None)
        mode_label = "LIVE run"
    else:
        from app.pipeline.graph import _default_deps
        deps = _default_deps()
        mode_label = "OFFLINE crash-test (NullLLM — LLM nodes expected to fail)"

    from app.pipeline.graph import process

    print()
    print(f"=== {mode_label} ===")
    print()
    header = (
        f"  {'file':<35s}  {'type':<7s}  {'status':<14s}  "
        f"{'#inv':>6}  {'#iss':>6}  {'sec':>8}  tier"
    )
    sep = "-" * len(header)
    print(header)
    print(sep)

    counts: dict[str, int] = {}
    rows: list[str] = []

    for fpath in args.files:
        p = Path(fpath)
        fname = p.name
        t0 = time.perf_counter()
        try:
            result = process(str(p), deps=deps)
            elapsed = time.perf_counter() - t0
            status = result.status.value if hasattr(result.status, "value") else str(result.status)
            ftype = result.file_type or "?"
            n_inv = len(result.invoices)
            n_iss = _issue_count(result)
            tier = _dominant_tier(result)
            if result.error_code:
                ec = result.error_code.value if hasattr(result.error_code, "value") else str(result.error_code)
                status_str = f"{status}({ec})"[:14]
            else:
                status_str = status
        except Exception as exc:  # noqa: BLE001
            elapsed = time.perf_counter() - t0
            ftype = p.suffix.lstrip(".") or "?"
            status_str = "CRASH"
            n_inv = 0
            n_iss = 0
            tier = "-"
            print(f"  CRASH in {fname}: {exc}", file=sys.stderr)

        row = (
            f"  {fname:<35s}  {ftype:<7s}  {status_str:<14s}  "
            f"{n_inv:>6}  {n_iss:>6}  {elapsed:>7.2f}s  {tier}"
        )
        print(row)
        rows.append(row)
        counts[status_str] = counts.get(status_str, 0) + 1

    print(sep)
    print()
    print("Summary:")
    for s, n in sorted(counts.items()):
        print(f"  {s:<30s}: {n}")
    print(f"  {'TOTAL':<30s}: {sum(counts.values())}")
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
