"""List Gemini models available to your API key, with capability flags.

Usage:
    python scripts/list_models.py

Helps you pick/refresh the model lists in config/settings.yaml
(gemini.model_candidates and twitter.search_grounding_models).
"""

import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from google import genai

load_dotenv(Path(__file__).parent.parent / ".env")


def main() -> int:
    api_key = os.getenv("GEMINI_API_KEY", "")
    if not api_key or api_key == "your_gemini_api_key_here":
        print("GEMINI_API_KEY is not set. Copy .env.example to .env and add your key.")
        return 2

    client = genai.Client(api_key=api_key)
    rows = []
    for m in client.models.list():
        actions = getattr(m, "supported_actions", None) or []
        if actions and "generateContent" not in actions:
            continue  # skip embeddings/image/video-only models
        rows.append((m.name.removeprefix("models/"), m.display_name or ""))

    rows.sort()
    width = max(len(r[0]) for r in rows) if rows else 20
    print(f"{'MODEL ID':<{width}}  DISPLAY NAME")
    print("-" * (width + 30))
    for model_id, display in rows:
        print(f"{model_id:<{width}}  {display}")
    print(f"\n{len(rows)} text-generation model(s) available to this key.")
    print("Note: quota status is not exposed by the list API — the aggregator discovers")
    print("quota exhaustion at call time and falls through to the next candidate.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
