#!/usr/bin/env bash
# Закрыть всё входящее кроме SSH и HTTP(S). Запускать на VPS под root.
set -euo pipefail

if [[ "$(id -u)" -ne 0 ]]; then
  echo "Запусти от root: sudo $0" >&2
  exit 1
fi

if ! command -v ufw >/dev/null 2>&1; then
  apt-get update -qq && apt-get install -y -qq ufw
fi

ufw --force reset
ufw default deny incoming
ufw default allow outgoing
ufw allow 22/tcp comment 'SSH'
ufw allow 80/tcp comment 'HTTP'
ufw allow 443/tcp comment 'HTTPS'
# Локальный Caddy dev-порт на VPS (если используешь)
ufw allow 8444/tcp comment 'Caddy alt HTTPS' || true
ufw --force enable
ufw status verbose

echo "UFW: снаружи доступны только SSH + 80/443 (+8444). Redis/Postgres/Qdrant с интернета закрыты."
