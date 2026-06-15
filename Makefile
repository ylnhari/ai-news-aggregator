.PHONY: install run

install:
	uv sync

run:
	uv run python src/main.py
