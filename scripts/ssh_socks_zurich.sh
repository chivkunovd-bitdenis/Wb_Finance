#!/usr/bin/env bash
# SOCKS5 на Mac → исходящий трафик с IP VPS (см. SERVER_ZURICH.md).
# Запуск: из корня репо `./scripts/ssh_socks_zurich.sh` (окно не закрывать).
# В фоне: `ssh -f -N -n ...` (см. man ssh).
set -euo pipefail
HOST="${SSH_ZURICH_HOST:-194.87.96.144}"
USER="${SSH_ZURICH_USER:-root}"
PORT="${SOCKS_LOCAL_PORT:-7890}"
exec ssh -N -D "127.0.0.1:${PORT}" "${USER}@${HOST}"
