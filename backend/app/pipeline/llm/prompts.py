"""Prompt templates and version constants.

Every prompt change must bump the version constant and note in docs/prompts-log.md.
"""

EXTRACT_PROMPT_VERSION = "extract-v1"
VISION_PROMPT_VERSION = "vision-v1"
SHEET_PROMPT_VERSION = "sheet-v1"


def get_extract_system() -> str:
    """System prompt for llm_extract. Filled in M6."""
    return ""


def get_vision_system() -> str:
    """System prompt for vision_extract. Filled in M6."""
    return ""


def get_sheet_system() -> str:
    """System prompt for sheet_map. Filled in M6."""
    return ""
