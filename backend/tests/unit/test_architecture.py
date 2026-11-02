"""Architecture purity test.

Rule 6 from CLAUDE.md: pipeline/ must not import db, api, services, workers,
sqlalchemy, fastapi, celery, or redis.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

FORBIDDEN = {"db", "api", "services", "workers", "sqlalchemy", "fastapi", "celery", "redis"}

PIPELINE_ROOT = Path(__file__).parent.parent.parent / "app" / "pipeline"


def _collect_imports(source: str) -> list[str]:
    """Return all module names referenced in import statements."""
    tree = ast.parse(source)
    names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.append(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                names.append(node.module.split(".")[0])
    return names


def _pipeline_py_files() -> list[Path]:
    return list(PIPELINE_ROOT.rglob("*.py"))


@pytest.mark.parametrize("py_file", _pipeline_py_files(), ids=lambda p: str(p.relative_to(PIPELINE_ROOT)))
def test_no_forbidden_imports(py_file: Path):
    source = py_file.read_text(encoding="utf-8")
    imports = _collect_imports(source)
    bad = [m for m in imports if m in FORBIDDEN]
    assert not bad, (
        f"{py_file.relative_to(PIPELINE_ROOT)} imports forbidden modules: {bad}"
    )


def test_pipeline_root_exists():
    assert PIPELINE_ROOT.is_dir(), f"pipeline/ not found at {PIPELINE_ROOT}"


def test_at_least_one_pipeline_file():
    files = _pipeline_py_files()
    assert files, "No .py files found under pipeline/"
