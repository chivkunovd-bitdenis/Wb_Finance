#!/usr/bin/env bash
# Генерирует REDIS_PASSWORD в backend/.env и синхронизирует REDIS_URL / WIP_REDIS_URL.
# Безопасно запускать повторно: существующий пароль не перезаписывает.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ENV_FILE="$ROOT/backend/.env"
EXAMPLE="$ROOT/backend/.env.example"

if [[ ! -f "$ENV_FILE" ]]; then
  if [[ -f "$EXAMPLE" ]]; then
    cp "$EXAMPLE" "$ENV_FILE"
    echo "Создан $ENV_FILE из .env.example — заполни секреты при необходимости."
  else
    echo "Нет $ENV_FILE" >&2
    exit 1
  fi
fi

get_var() {
  local key="$1"
  grep -E "^${key}=" "$ENV_FILE" 2>/dev/null | tail -1 | cut -d= -f2- || true
}

pass="$(get_var REDIS_PASSWORD)"
if [[ -z "$pass" || "$pass" == "change-me-local-dev-only" ]]; then
  pass="$(openssl rand -hex 24)"
  if grep -qE '^REDIS_PASSWORD=' "$ENV_FILE"; then
    sed -i.bak "s/^REDIS_PASSWORD=.*/REDIS_PASSWORD=${pass}/" "$ENV_FILE"
    rm -f "$ENV_FILE.bak"
  else
    printf '\n# Redis (сгенерировано %s)\nREDIS_PASSWORD=%s\n' "$(date -Iseconds)" "$pass" >>"$ENV_FILE"
  fi
  echo "REDIS_PASSWORD сгенерирован и записан в backend/.env"
else
  echo "REDIS_PASSWORD уже задан — не меняем."
fi

pass="$(get_var REDIS_PASSWORD)"
url0="redis://:${pass}@redis:6379/0"
url2="redis://:${pass}@redis:6379/2"

set_or_append() {
  local key="$1"
  local val="$2"
  if grep -qE "^${key}=" "$ENV_FILE"; then
    sed -i.bak "s|^${key}=.*|${key}=${val}|" "$ENV_FILE"
    rm -f "$ENV_FILE.bak"
  else
    echo "${key}=${val}" >>"$ENV_FILE"
  fi
}

set_or_append REDIS_URL "$url0"
set_or_append WIP_REDIS_URL "$url2"
echo "REDIS_URL и WIP_REDIS_URL обновлены."
