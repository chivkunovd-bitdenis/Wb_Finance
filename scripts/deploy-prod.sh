#!/usr/bin/env bash
# Прод-деплой из /root/wb-finance: git pull, Redis password, закрытые порты, полный стек.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

echo "==> ensure REDIS_PASSWORD"
bash "$ROOT/scripts/ensure-redis-password.sh"

# shellcheck disable=SC1091
set -a
source "$ROOT/backend/.env"
set +a

echo "==> git pull"
git pull

echo "==> UFW (firewall)"
if [[ "$(id -u)" -eq 0 ]]; then
  bash "$ROOT/scripts/harden-server-ufw.sh"
else
  echo "   (пропуск UFW: не root; на VPS запусти: sudo bash scripts/harden-server-ufw.sh)"
fi

echo "==> docker compose build"
docker compose build api celery_worker celery_beat wb_image_pipeline_api wb_image_pipeline_worker

echo "==> поднимаем инфраструктуру"
docker compose up -d postgres redis qdrant

echo "==> ждём redis"
for i in $(seq 1 30); do
  if docker compose exec -T redis redis-cli -a "$REDIS_PASSWORD" ping 2>/dev/null | grep -q PONG; then
    break
  fi
  sleep 1
done

echo "==> перезапуск приложения"
docker compose up -d --remove-orphans

echo "==> ждём api /health"
ok=0
for i in $(seq 1 30); do
  if curl -4 -sf --max-time 5 "http://127.0.0.1:8000/health" >/dev/null 2>&1; then
    ok=1
    break
  fi
  sleep 5
done

echo "==> celery ping"
docker compose exec -T celery_worker celery -A celery_app.celery inspect ping || true

echo "==> docker compose ps"
docker compose ps

if [[ "$ok" -eq 1 ]]; then
  curl -4 -sS "http://127.0.0.1:8000/health"
  echo ""
  echo "==> Deploy OK"
  exit 0
fi

echo "==> API health failed; logs:"
docker compose logs api --tail 80
exit 1
