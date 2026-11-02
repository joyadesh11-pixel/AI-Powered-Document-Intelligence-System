"""Session-scoped fixtures for real file I/O tests.

fixture_dir  — generates all M2 fixture files once per test session into a temp dir.
fx           — maps logical scenario names to concrete paths in fixture_dir.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from scripts.generate_fixtures import generate_fixtures


@pytest.fixture(scope="session")
def fixture_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Generate all fixture files once per session into a shared temp directory."""
    d = tmp_path_factory.mktemp("fixtures")
    generate_fixtures(d, seed=42)
    return d


@pytest.fixture(scope="session")
def fx(fixture_dir: Path) -> dict[str, Path]:
    """Map of logical scenario names to actual fixture paths."""
    return {
        "pdf":        fixture_dir / "digital_A_001.pdf",
        "image":      fixture_dir / "valid_001.png",
        "xlsx":       fixture_dir / "tabular_001.xlsx",
        "csv":        fixture_dir / "utf8_001.csv",
        "freeform_csv": fixture_dir / "freeform_001.csv",
        "corrupt":    fixture_dir / "bad_truncated.pdf",
        "unsupported": fixture_dir / "bad_docx.docx",
        "mixed":      fixture_dir / "mixed.pdf",
    }


@pytest.fixture(scope="session")
def gt(fixture_dir: Path) -> dict[str, dict]:
    """Ground-truth index keyed by filename."""
    import json
    with open(fixture_dir / "ground_truth.json") as f:
        entries = json.load(f)
    return {e["file"]: e for e in entries}
