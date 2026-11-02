"""Text normalisation utilities. Implemented in M1."""
from __future__ import annotations


def normalise(text: str) -> str:
    """Collapse whitespace, strip control characters."""
    raise NotImplementedError("Implemented in M1")


def is_garbled(text: str, ratio_max: float = 0.20) -> bool:
    """Return True if the non-printable ratio exceeds ratio_max."""
    raise NotImplementedError("Implemented in M1")
