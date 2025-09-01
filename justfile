set windows-shell := ["powershell.exe", "-NoLogo", "-Command"]

default:
    @just --list

# Install every dependency including the dev group.
install:
    uv sync

# Run the bot locally against the .env in the repo root.
dev:
    uv run python -m chaptercut

test:
    uv run pytest -q

# Skip the tests that shell out to ffmpeg.
test-fast:
    uv run pytest -q -m "not ffmpeg"

cov:
    uv run pytest -q --cov --cov-report=term-missing

lint:
    uv run ruff check src tests
    uv run ruff format --check src tests
    uv run pyright

fmt:
    uv run ruff check --fix src tests
    uv run ruff format src tests

up:
    docker compose up -d --build

down:
    docker compose down

logs:
    docker compose logs -f bot

shell:
    docker compose exec bot bash

# One-time migration: release the token from Telegram's cloud server so the
# self-hosted one will accept it. Wait ~10 minutes afterwards.
logout-cloud:
    uv run python -m chaptercut.tools.logout_cloud
