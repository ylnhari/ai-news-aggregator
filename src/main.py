"""Main orchestration script for AI News Aggregator.

Pipeline:
  1. Fetch real news (RSS blogs, arXiv, Hacker News) — free + verified
  2. Optionally fetch social signals (Gemini search grounding) — unverified leads
  3. Synthesize a daily brief with Gemini
  4. Export to PDF + Markdown
"""

import argparse
import json
import logging
import os
import sys
import time
from datetime import datetime
from pathlib import Path

# Add src to path so the script works when run from anywhere
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


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        prog="ai-news-aggregator",
        description="Generate a daily AI news brief (PDF + Markdown) from free sources.",
    )
    parser.add_argument("--days-back", type=int, default=None, metavar="N",
                        help="look back N days (default: settings.yaml / DAYS_BACK env)")
    parser.add_argument("--no-social", action="store_true",
                        help="skip the unverified social-signals section (saves quota)")
    parser.add_argument("--output-dir", default=None, metavar="DIR",
                        help="directory for generated reports (default: ./reports)")
    parser.add_argument("--keep-cache-days", type=int, default=14, metavar="N",
                        help="delete cached raw-news JSON older than N days (0 = keep forever)")
    parser.add_argument("--quiet", action="store_true", help="only log warnings and errors")
    return parser.parse_args(argv)


def cleanup_cache(data_dir: Path, keep_days: int) -> None:
    """Delete news_*.json cache files older than keep_days."""
    if keep_days <= 0:
        return
    cutoff = time.time() - keep_days * 86400
    removed = 0
    for f in data_dir.glob("news_*.json"):
        try:
            if f.stat().st_mtime < cutoff:
                f.unlink()
                removed += 1
        except OSError:
            pass
    if removed:
        logger.info(f"Cache cleanup: removed {removed} file(s) older than {keep_days} days")


def main(argv=None) -> int:
    args = parse_args(argv)
    if args.quiet:
        logging.getLogger().setLevel(logging.WARNING)
    # CLI flags override env/settings via the env vars Config already reads
    if args.days_back is not None:
        os.environ["DAYS_BACK"] = str(args.days_back)
    if args.output_dir:
        os.environ["OUTPUT_DIR"] = args.output_dir

    logger.info("=" * 60)
    logger.info("AI NEWS AGGREGATOR - Starting")
    logger.info("=" * 60)

    try:
        config = Config()
        problems = config.validate()
        if problems:
            for p in problems:
                logger.error(f"Configuration problem: {p}")
            return 2

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        cleanup_cache(config.data_dir, args.keep_cache_days)

        # 1. Real, verified sources
        logger.info("Fetching from verified sources (RSS / arXiv / Hacker News)...")
        items = fetch_all_sources(config)

        # 2. Optional unverified social signals
        social_items = []
        social_enabled = (not args.no_social
                          and config.get_setting("twitter", "enabled", default=True))
        if social_enabled:
            try:
                from twitter_fetcher import SocialSignalsFetcher
                logger.info("Fetching social signals (unverified, via Gemini search)...")
                social_items = SocialSignalsFetcher(config).fetch()
            except Exception as e:
                logger.warning(f"Social signals unavailable, continuing without: {e}")

        if not items and not social_items:
            logger.error("No items fetched from any source. Check your network and feeds.")
            return 1

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
        logger.info("Process completed successfully!")
        logger.info(f"  Items: {len(items_data)} verified + {len(social_data)} social signals")
        logger.info(f"  Report (PDF): {pdf_path}")
        logger.info(f"  Report (MD):  {md_path}")
        logger.info("=" * 60)
        return 0

    except KeyboardInterrupt:
        logger.warning("Interrupted by user.")
        return 130
    except Exception as e:
        logger.error(f"Error in main execution: {e}", exc_info=True)
        return 1


if __name__ == "__main__":
    sys.exit(main())
