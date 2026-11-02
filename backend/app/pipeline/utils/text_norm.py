"""Text normalisation utilities."""
from __future__ import annotations

import re


def normalise(text: str) -> str:
    """Casefold and strip non-alphanumeric characters."""
    return re.sub(r"[^a-z0-9]", "", text.casefold())


def is_garbled(text: str, ratio_max: float = 0.20) -> bool:
    """Return True if the ratio of non-printable/non-ASCII chars exceeds ratio_max.

    Implemented in M3 (pdf_triage). Stub for M1 callers.
    """
    raise NotImplementedError("Implemented in M3")
