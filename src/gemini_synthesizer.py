"""Gemini AI integration for synthesizing news reports.

Uses the current google-genai SDK (the old google-generativeai SDK is EOL).
Model selection: one models.list() call, intersected with the configured
preference order — no per-candidate "ping" requests that waste free-tier quota.
"""

import logging
from typing import List, Dict, Optional

from google import genai
from google.genai import errors as genai_errors

logger = logging.getLogger(__name__)


class GeminiSynthesizer:
    """Synthesize news reports using Gemini AI."""

    def __init__(self, config):
        self.config = config
        api_key = config.gemini_api_key
        if not api_key:
            raise ValueError(
                "Gemini API key not found. Please set GEMINI_API_KEY in .env."
            )
        self.client = genai.Client(api_key=api_key)
        self.model_candidates = self._select_models()
        if not self.model_candidates:
            raise RuntimeError("No usable Gemini text model found for this API key.")
        logger.info(f"Gemini model preference order: {self.model_candidates}")

    def _select_models(self) -> List[str]:
        """Build an ordered list of usable model IDs.

        Intersects the configured preference list with what the API key can
        actually access (single models.list() call). Falls back to the
        configured list as-is if listing fails.
        """
        preferred: List[str] = self.config.get_setting(
            "gemini", "model_candidates",
            default=["gemini-flash-latest", "gemini-2.5-flash",
                     "gemini-2.5-flash-lite", "gemini-flash-lite-latest"],
        )
        try:
            available = set()
            for m in self.client.models.list():
                actions = getattr(m, "supported_actions", None) or []
                if not actions or "generateContent" in actions:
                    available.add(m.name.removeprefix("models/"))
        except Exception as e:
            logger.warning(f"models.list() failed ({e}); using configured list as-is")
            return preferred

        usable = [m for m in preferred if m in available]
        # Keep any configured models the listing missed, at the end, just in case
        usable += [m for m in preferred if m not in available]
        return usable

    def _generate(self, prompt: str, **gen_kwargs) -> str:
        """Generate content, falling through the model list on quota/404 errors."""
        last_error: Optional[Exception] = None
        for model in self.model_candidates:
            try:
                response = self.client.models.generate_content(
                    model=model,
                    contents=prompt,
                    config={
                        "temperature": self.config.get_setting(
                            "gemini", "temperature", default=0.4),
                        "max_output_tokens": self.config.get_setting(
                            "gemini", "max_tokens", default=4000),
                        **gen_kwargs,
                    },
                )
                text = response.text
                if text:
                    if model != self.model_candidates[0]:
                        logger.info(f"Used fallback model: {model}")
                    return text
                last_error = RuntimeError(f"Empty response from {model}")
            except genai_errors.APIError as e:
                last_error = e
                if e.code in (404, 429, 503):
                    logger.warning(f"Model {model} unavailable ({e.code}); trying next")
                    continue
                raise
            except Exception as e:  # network blips etc.
                last_error = e
                logger.warning(f"Model {model} failed: {e}; trying next")
        raise RuntimeError(f"All Gemini models failed. Last error: {last_error}")

    # ------------------------------------------------------------------
    # Report synthesis
    # ------------------------------------------------------------------

    def synthesize_report(self, items: List[Dict], social_items: Optional[List[Dict]] = None) -> str:
        """Synthesize a daily brief from news items.

        Args:
            items: verified items (blogs, arXiv, HN) as dicts
            social_items: optional unverified search-grounded social updates
        """
        if not items and not social_items:
            return "No news items to synthesize."

        prompt = self._create_synthesis_prompt(items or [], social_items or [])
        logger.info(
            f"Synthesizing report from {len(items or [])} verified items "
            f"and {len(social_items or [])} social signals"
        )
        report = self._generate(prompt)
        logger.info("Report synthesis completed successfully")
        return report

    def _format_items(self, items: List[Dict], cap: int = 120) -> str:
        lines = []
        for it in items[:cap]:
            title = it.get("title") or (it.get("text", "")[:120])
            date = (it.get("published_at") or it.get("created_at") or "")[:10]
            score = it.get("score") or 0
            score_txt = f" | {score} points" if score else ""
            author = it.get("author") or it.get("author_handle") or ""
            author_txt = f" | by {author}" if author else ""
            summary = (it.get("summary") or it.get("text") or "")[:300]
            url = it.get("url", "")
            src = it.get("source") or it.get("category") or ""
            lines.append(
                f"- [{src}] {title} ({date}{score_txt}{author_txt})\n"
                f"  {summary}\n  Link: {url}"
            )
        return "\n".join(lines)

    def _create_synthesis_prompt(self, items: List[Dict], social_items: List[Dict]) -> str:
        by_type: Dict[str, List[Dict]] = {}
        for it in items:
            by_type.setdefault(it.get("source_type", "blog"), []).append(it)

        sections = []
        labels = {
            "blog": "OFFICIAL BLOGS & ANNOUNCEMENTS (verified)",
            "research": "NEW RESEARCH PAPERS — arXiv (verified)",
            "community": "COMMUNITY DISCUSSION — Hacker News (verified, ranked by points)",
        }
        for key, label in labels.items():
            if by_type.get(key):
                sections.append(f"### {label}\n{self._format_items(by_type[key])}")

        if social_items:
            sections.append(
                "### SOCIAL SIGNALS — X/Twitter via LLM web search "
                "(UNVERIFIED — details/links may be imprecise, treat as leads only)\n"
                + self._format_items(social_items, cap=60)
            )

        source_block = "\n\n".join(sections)

        return f"""You are an expert AI-industry analyst writing a daily brief for a lead AI engineer \
who needs to stay current on AI developments. Today's collected items:

{source_block}

Write a daily brief in markdown with exactly this structure:

## Executive Summary
2-3 paragraphs on the most significant developments. Lead with what matters most for a practitioner.

## Top Stories
The 5-7 most important items. For each: one bold headline line, 2-3 sentences of context and why it \
matters, and the source link.

## Research Radar
The most notable new papers (from the arXiv section). For each: title, one-sentence takeaway, link. \
Skip incremental papers; pick ones a lead engineer should know exist.

## Community Pulse
What the community is discussing (Hacker News section). 3-5 bullets with links.

## Social Signals (Unverified)
Only if social items were provided: 3-5 bullets summarizing what notable accounts appear to be \
discussing. Clearly note this section comes from LLM web search and may be imprecise.

## Action Items
3-5 specific, concrete next steps for a lead AI engineer (things to read, try, or evaluate).

Rules:
- Use ONLY the items provided above. Never invent stories, papers, numbers, or links.
- Always carry over the exact links provided.
- If a section has no relevant items, write "Nothing significant today."
- Professional but direct tone. No filler.
"""

    def summarize_category(self, items: List[Dict], category: str) -> str:
        """Summarize items from a specific category in 2-3 sentences."""
        if not items:
            return f"No items for {category}."
        text = "\n".join(f"- {i.get('title') or i.get('text', '')}" for i in items[:10])
        return self._generate(
            f"Summarize the following {category} AI news items in 2-3 sentences. "
            f"Highlight only the most important news:\n\n{text}"
        )
