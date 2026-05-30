set shell := ["zsh", "-cu"]

# list just commands
list:
    just --list

# set up python environment (requires uv)
setup:
    uv sync

# run the MCP server
run:
    uv run python -m whatbox_media_mcp.server

# run local-only tests
test:
    uv run pytest

# test if MCP server is live
test-smoke:
    uv run scripts/healthcheck.sh

# run tests that validate connections to services
test-live:
    LIVE_TESTS=1 uv run pytest -m live -v

# format code
format:
    uv run ruff format .

# run ruff and type checks
check:
    uv run ruff check --fix .
    uv run mypy src