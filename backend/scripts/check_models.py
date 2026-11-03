"""Check which Gemini models are available for the configured API key.

Usage:
    python -m scripts.check_models

Reads GOOGLE_API_KEY from environment (or .env if python-dotenv is available).
Prints model names that support generateContent; never prints the key.
"""
from __future__ import annotations

import os
import sys

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass


def main() -> None:
    key = os.environ.get("GOOGLE_API_KEY")
    if not key:
        print("ERROR: GOOGLE_API_KEY not set in environment or .env", file=sys.stderr)
        sys.exit(1)

    try:
        from google import genai
    except ImportError:
        print("ERROR: google-genai not installed. Run: pip install google-genai", file=sys.stderr)
        sys.exit(1)

    client = genai.Client(api_key=key)

    target_text = os.environ.get("LLM_MODEL_TEXT", "gemini-2.5-flash")
    target_vision = os.environ.get("LLM_MODEL_VISION", "gemini-2.5-pro")

    print("Available models supporting generateContent:")
    found: list[str] = []
    for m in client.models.list():
        if not hasattr(m, "supported_actions"):
            name = getattr(m, "name", str(m))
            # Just list all
            print(f"  {name}")
            found.append(name)
        else:
            if "generateContent" in (m.supported_actions or []):
                name = m.name
                print(f"  {name}")
                found.append(name)

    print()
    for label, target in [("LLM_MODEL_TEXT", target_text), ("LLM_MODEL_VISION", target_vision)]:
        match = any(target in n for n in found)
        status = "OK" if match else "NOT FOUND"
        print(f"  {label}={target!r}  [{status}]")

    if not all(any(t in n for n in found) for t in [target_text, target_vision]):
        print()
        print("If models are missing, update LLM_MODEL_TEXT / LLM_MODEL_VISION in .env")
        print("Then re-run this check.")
        sys.exit(1)

    print()
    print("All configured models found.")


if __name__ == "__main__":
    main()
