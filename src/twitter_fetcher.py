"""Social signals via Gemini Google-Search grounding (supplementary, UNVERIFIED).

X/Twitter cannot be read for free anymore (the API read tier is paid, and
scrapers like snscrape are dead since X removed guest access). This module asks
Gemini + Google Search what the configured accounts have publicly said or done
recently. Results are *leads, not facts*: timestamps, engagement numbers and
even URLs may be imprecise, so every item is marked verified=False and the
report renders them in a clearly-labeled "Unverified" section.

The old tweepy/snscrape code paths were removed because neither works on a
free tier.
"""

import json
import logging
import re
import time
from datetime import datetime, timezone
from typing import Dict, List

from google import genai
from google.genai import types

from content_fetcher import NewsItem

logger = logging.getLogger(__name__)


class SocialSignalsFetcher:
    """Fetch unverified social updates using Gemini Google Search grounding."""

    # Default fallback list if not configured in settings.yaml
    _DEFAULT_MODELS = [
        "gemini-flash-latest",
        "gemini-3.1-flash-lite",
        "gemini-3.1-flash-lite-preview",
        "gemini-3-flash-preview",
        "gemini-2.5-flash",
        "gemini-2.5-flash-lite",
        "gemini-flash-lite-latest",
    ]

    def __init__(self, config):
        self.config = config
        if not config.gemini_api_key:
            raise ValueError("Gemini API key not found. Please set GEMINI_API_KEY in .env.")
        self.client = genai.Client(api_key=config.gemini_api_key)
        # Support both old single-model key and new list key
        configured = self.config.get_setting("twitter", "search_grounding_models")
        if not configured:
            legacy = self.config.get_setting("twitter", "search_grounding_model")
            configured = [legacy] if legacy else []
        self.model_candidates: List[str] = configured or self._DEFAULT_MODELS
        logger.info(f"Social signals model order: {self.model_candidates}")

    def fetch(self) -> List[NewsItem]:
        accounts = self.config.twitter_accounts
        days_back = self.config.days_back
        batch_size = int(self.config.get_setting("twitter", "search_batch_size", default=25))
        delay = int(self.config.get_setting("twitter", "batch_delay_seconds", default=15))

        items: List[NewsItem] = []
        batches = [accounts[i:i + batch_size] for i in range(0, len(accounts), batch_size)]
        logger.info(
            f"Fetching social signals for {len(accounts)} accounts "
            f"in {len(batches)} batch(es)")

        for b_idx, batch in enumerate(batches, 1):
            try:
                items.extend(self._fetch_batch_with_fallback(batch, days_back, b_idx))
            except Exception as e:
                logger.error(f"  Batch {b_idx} failed: {e}")
            if b_idx < len(batches):
                time.sleep(delay)  # respect free-tier RPM limits

        logger.info(f"Social signals fetched: {len(items)} (unverified)")
        return items

    def _fetch_batch_with_fallback(self, batch: List[Dict], days_back: int,
                                   b_idx: int) -> List[NewsItem]:
        """Try each model in order, falling through on quota/availability errors."""
        from google.genai import errors as genai_errors
        last_error = None
        for model_name in self.model_candidates:
            try:
                result = self._fetch_batch(batch, days_back, model_name, b_idx)
                if model_name != self.model_candidates[0]:
                    logger.info(f"  Batch {b_idx}: used fallback model {model_name}")
                return result
            except genai_errors.APIError as e:
                last_error = e
                if e.code in (429, 404, 503):
                    logger.warning(f"  Batch {b_idx}: model {model_name} returned {e.code}; trying next")
                    continue
                raise
            except Exception as e:
                last_error = e
                logger.warning(f"  Batch {b_idx}: model {model_name} failed ({e}); trying next")
        raise RuntimeError(f"All social-signals models failed. Last error: {last_error}")

    def _fetch_batch(self, batch: List[Dict], days_back: int,
                     model_name: str, b_idx: int) -> List[NewsItem]:
        prompt = (
            f"Search the web for significant public updates, announcements, or posts "
            f"from the past {days_back} day(s) by these AI-field figures/orgs "
            f"(their X/Twitter handles are given for identification):\n"
        )
        for acc in batch:
            prompt += f"- @{acc.get('handle')} ({acc.get('name')}) — {acc.get('category', 'General')}\n"
        prompt += """
Return ONLY a JSON list inside a ```json code block. Up to 2 items per account,
and ONLY for accounts where you actually found something recent and significant.
Each item must have exactly these keys:
{
  "author": "Full Name",
  "author_handle": "handle without @",
  "text": "what they announced/said (1-2 sentences)",
  "published_at": "ISO date, e.g. 2026-06-09",
  "url": "link to the source you found (the actual page, not a guess)",
  "category": "their category"
}
Do not fabricate items. If you found nothing for an account, omit it. Never invent URLs.
"""
        response = self.client.models.generate_content(
            model=model_name,
            contents=prompt,
            config=types.GenerateContentConfig(tools=[{"google_search": {}}]),
        )
        text = response.text or ""
        match = re.search(r"```json\s*(.*?)\s*```", text, re.DOTALL)
        json_str = match.group(1) if match else text.strip()
        if not json_str.startswith("["):
            start, end = json_str.find("["), json_str.rfind("]")
            if start == -1 or end == -1:
                logger.warning(f"  Batch {b_idx}: no JSON found in response")
                return []
            json_str = json_str[start:end + 1]

        try:
            raw_items = json.loads(json_str)
        except json.JSONDecodeError as e:
            logger.warning(f"  Batch {b_idx}: JSON decode failed: {e}")
            return []

        items: List[NewsItem] = []
        for i, item in enumerate(raw_items):
            if not isinstance(item, dict) or not item.get("text"):
                continue
            handle = str(item.get("author_handle", "")).lstrip("@")
            try:
                published = datetime.fromisoformat(
                    str(item.get("published_at", "")).replace("Z", "+00:00"))
                if published.tzinfo is None:
                    published = published.replace(tzinfo=timezone.utc)
            except ValueError:
                published = datetime.now(timezone.utc)
            items.append(NewsItem(
                id=f"social_{b_idx}_{i}",
                title=f"@{handle}: {item.get('text', '')[:80]}",
                summary=item.get("text", ""),
                url=item.get("url", ""),
                source=f"X/@{handle} (via web search)",
                source_type="social",
                published_at=published,
                author=item.get("author", handle),
                verified=False,
            ))
        logger.info(f"  Batch {b_idx}: {len(items)} signal(s)")
        return items


# Backwards-compatible alias (old name used in main.py / docs)
TwitterFetcher = SocialSignalsFetcher
