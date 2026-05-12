#!/bin/sh
set -e
mkdir -p "${WIP_MEDIA_ROOT:-/data/media}"
cd /app
alembic upgrade head
exec "$@"
