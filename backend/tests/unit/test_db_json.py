"""JSONB columns must accept date/datetime/Decimal values (real LLM output hit this)."""
import json
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy.dialects.postgresql import JSONB

from app.db.base import make_engine


def _serialize(payload) -> str:
    engine = make_engine("postgresql+psycopg2://u:p@127.0.0.1:1/x")  # never connects
    proc = JSONB().bind_processor(engine.dialect)
    return proc(payload)


def test_jsonb_serializes_date_datetime_and_decimal():
    out = _serialize({
        "d": date(2026, 10, 6),
        "t": datetime(2026, 10, 6, 12, 30, 0),
        "m": Decimal("1234.50"),
        "nested": [{"due": date(2026, 11, 5)}],
    })
    assert json.loads(out) == {
        "d": "2026-10-06",
        "t": "2026-10-06T12:30:00",
        "m": "1234.50",
        "nested": [{"due": "2026-11-05"}],
    }


def test_jsonb_plain_values_unchanged():
    out = _serialize({"a": 1, "b": "x", "c": None, "d": [1, 2]})
    assert json.loads(out) == {"a": 1, "b": "x", "c": None, "d": [1, 2]}
