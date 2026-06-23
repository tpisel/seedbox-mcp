set shell := ["zsh", "-cu"]

# list just commands
list:
    just --list

# set up python environment (requires uv)
setup:
    uv sync

# deploy latest committed code to the seedbox (ssh + git pull + restart)
deploy:
    bash scripts/deploy.sh

# run the MCP server
run:
    uv run python -m seedbox_mcp.server

# run the chat server
run-chat:
    open http://127.0.0.1:${CHAT_PORT:-17433} &
    uv run python -m seedbox_mcp.chat.server

# run local-only tests
test:
    uv run pytest

# test if MCP server is live
test-smoke:
    uv run scripts/healthcheck.sh

# run chat server tests
test-chat:
    uv run pytest tests/chat

# run live chat tests (needs ANTHROPIC_API_KEY and MCP running)
test-chat-live:
    LIVE_TESTS=1 uv run pytest tests/chat -m live -v

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