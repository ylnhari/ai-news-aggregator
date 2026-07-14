.PHONY: install run engine-run engine-collect engine-digest engine-site engine-doctor test

install:
	uv sync

# Legacy brief pipeline (Gemini synthesis -> PDF/MD)
run:
	uv run python src/main.py

# Engine (stdlib-only collection core; config: engine.config.json)
engine-run:
	python -m engine run

engine-collect:
	python -m engine collect

engine-digest:
	python -m engine digest

engine-site:
	python -m engine site

engine-doctor:
	python -m engine doctor

test:
	uv run pytest tests/ -q
