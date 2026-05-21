# Default recipe lists commands
default:
    @just --list

# Run all banks (daily sync)
sync:
    uv run python scripts/sync.py

# Run one bank (on-demand single sync)
sync-bank bank:
    uv run python scripts/sync.py --bank {{bank}}

# Run one bank interactively (manual escape hatch)
sync-interactive bank:
    uv run python scripts/sync.py --bank {{bank}} --interactive

# Run historical backfill for one bank
backfill bank since:
    uv run python scripts/backfill.py --bank {{bank}} --since {{since}}

# One-shot Notion schema migration (rename SimpleFIN fields, add new fields)
migrate:
    uv run python scripts/migrate_schema.py

# Start the FastAPI HTTP server (foreground)
serve:
    uv run uvicorn notion_finance_sync.server.app:app --host 127.0.0.1 --port 8765

# Install the launchd daily-sync job
install-launchd:
    cp deploy/com.alexmiller.notion-finance-sync.plist ~/Library/LaunchAgents/
    launchctl load ~/Library/LaunchAgents/com.alexmiller.notion-finance-sync.plist

# Uninstall the launchd job
uninstall-launchd:
    launchctl unload ~/Library/LaunchAgents/com.alexmiller.notion-finance-sync.plist
    rm ~/Library/LaunchAgents/com.alexmiller.notion-finance-sync.plist

# Run tests
test:
    uv run pytest tests/ -v

# Run a single test by name
test-one name:
    uv run pytest tests/ -v -k {{name}}

# Lint check
lint:
    uv run ruff check src/ scripts/ tests/
    uv run ruff format --check src/ scripts/ tests/

# Auto-format
fmt:
    uv run ruff format src/ scripts/ tests/
    uv run ruff check --fix src/ scripts/ tests/
