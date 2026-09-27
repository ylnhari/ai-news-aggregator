# Maintenance review — 2026-09-27

## Purpose

Make the generated AI Signal archive easier to browse without introducing network calls, external dependencies, or editorial changes.

## Baseline

The repository was clean before this review. The HTML archive already supported month and week filters, but had no text search across edition dates and headlines.

## Changes

Added a date-and-headline search to the archive index. It composes with the existing month/week filters, exposes a live result count to assistive technology, and searches only visible index metadata. Added renderer regression coverage for accessibility markup and HTML escaping.

## Verification

- `python -m unittest tests.test_engine_core tests.test_render_robustness` — 32 tests passed; storage uses temporary SQLite and the renderer uses temporary Markdown fixtures. No network, provider, scheduler, or application entry point was used.

## Remaining gaps and preserved work

No other source behavior was changed. The full legacy-pipeline suite was not run in this maintenance pass.
