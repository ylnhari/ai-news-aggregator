"""Main orchestration script for AI News Aggregator.

Pipeline:
  1. Fetch real news (RSS blogs, arXiv, Hacker News) — free + verified
  2. Optionally fetch social signals (Gemini search grounding) — unverified leads
  3. Synthesize a daily brief with Gemini
  4. Export to PDF
"""

import json
import logging
import sys
from datetime import datetime
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent))

from config import Config
from content_fetcher import fetch_all_sources
from gemini_synthesizer import GeminiSynthesizer
from pdf_exporter import PDFExporter

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def main():
    logger.info("=" * 60)
    logger.info("AI NEWS AGGREGATOR - Starting")
    logger.info("=" * 60)

    try:
        config = Config()
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

        # 1. Real, verified sources
        logger.info("Fetching from verified sources (RSS / arXiv / Hacker News)...")
        items = fetch_all_sources(config)

        # 2. Optional unverified social signals
        social_items = []
        if config.get_setting("twitter", "enabled", default=True):
            try:
                from twitter_fetcher import SocialSignalsFetcher
                logger.info("Fetching social signals (unverified, via Gemini search)...")
                social_items = SocialSignalsFetcher(config).fetch()
            except Exception as e:
                logger.warning(f"Social signals unavailable, continuing without: {e}")

        if not items and not social_items:
            logger.warning("No items fetched from any source. Exiting.")
            return None

        # 3. Cache raw data
        items_data = [it.to_dict() for it in items]
        social_data = [it.to_dict() for it in social_items]
        cache_path = config.data_dir / f"news_{timestamp}.json"
        with open(cache_path, "w", encoding="utf-8") as f:
            json.dump({"items": items_data, "social_signals": social_data},
                      f, indent=2, ensure_ascii=False)
        logger.info(f"Raw items cached to {cache_path}")

        # 4. Synthesize
        logger.info("Synthesizing daily brief with Gemini...")
        synthesizer = GeminiSynthesizer(config)
        report = synthesizer.synthesize_report(items_data, social_data)

        # 5. Export PDF
        logger.info("Exporting report to PDF...")
        pdf_exporter = PDFExporter(config)
        pdf_path = pdf_exporter.export_report_to_pdf(
            report,
            metadata={
                "title": config.get_setting("report", "title", default="AI News Daily Digest"),
                "date": datetime.now().strftime("%B %d, %Y"),
            },
        )

        # Also save the markdown version (handy for skimming / piping elsewhere)
        md_path = pdf_path.with_suffix(".md")
        md_path.write_text(report, encoding="utf-8")

        logger.info("=" * 60)
        logger.info("✓ Process completed successfully!")
        logger.info(f"  Items: {len(items_data)} verified + {len(social_data)} social signals")
        logger.info(f"  Report (PDF): {pdf_path}")
        logger.info(f"  Report (MD):  {md_path}")
        logger.info("=" * 60)

        return pdf_path

    except Exception as e:
        logger.error(f"Error in main execution: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
