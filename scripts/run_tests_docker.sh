#!/usr/bin/env bash
set -euo pipefail

# Запуск тестов в Docker так, чтобы Postgres/Redis были подняты.
# Работает одинаково на Mac и на сервере (где есть docker compose).

cd "$(dirname "$0")/.."

echo "[tests] Starting postgres/redis..."
docker compose up -d postgres redis

echo "[tests] Running pytest in api container..."
# Важно: монтируем актуальные тесты с хоста → /app/tests,
# иначе docker compose run может запускать тесты из образа (старые) и количество будет отличаться.
docker compose run --rm \
  -v "$(pwd)/backend/tests:/app/tests" \
  api python -m pytest -q

