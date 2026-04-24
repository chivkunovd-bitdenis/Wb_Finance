#!/usr/bin/env bash
# Запуск на VPS из репозитория: подтягивает код, собирает api, ждёт /health.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

echo "==> git pull"
git pull

echo "==> поднимаем redis и очищаем Celery очередь (FLUSHDB)"
docker compose up -d redis
docker compose stop celery_worker celery_beat || true
docker compose exec -T redis redis-cli FLUSHDB

echo "==> docker compose build api"
docker compose build api

echo "==> docker compose up -d postgres redis api"
docker compose up -d postgres redis api

echo "==> ждём http://127.0.0.1:8000/health (до ~120 с)"
ok=0
for i in $(seq 1 24); do
  if curl -4 -sf --max-time 5 "http://127.0.0.1:8000/health" >/dev/null 2>&1; then
    ok=1
    break
  fi
  echo "   ... ожидание api, попытка $i/24"
  sleep 5
done

if [[ "$ok" -eq 1 ]]; then
  echo "==> OK:"
  curl -4 -sS "http://127.0.0.1:8000/health"
  echo ""
  docker compose ps api
  exit 0
fi

echo "==> Не дождались /health. Логи api:"
docker compose ps api
docker compose logs api --tail 100
exit 1
