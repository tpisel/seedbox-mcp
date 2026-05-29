set shell := ["zsh", "-cu"]

setup:
    uv sync

run:
    uv run python -m whatbox_media_mcp.server

test:
    uv run pytest

lint:
    uv run ruff check .

format:
    uv run ruff format .

check:
    uv run ruff check .
    uv run mypy src
    uv run pytest

smoke:
    uv run scripts/healthcheck.sh
