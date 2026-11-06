#!/usr/bin/env bash
# Publish one exact main commit without changing the server checkout or shared state.
set -Eeuo pipefail
umask 077

fail() { printf 'release-ozon: %s\n' "$*" >&2; exit 1; }
is_sha() { [[ "$1" =~ ^[0-9a-f]{40}$ ]]; }
validate_source() {
  local source="$1" untracked
  [[ "$(git -C "$source" rev-parse HEAD)" == "$EXPECTED_SHA" ]] || fail "release source worktree has the wrong SHA"
  git -C "$source" diff --quiet "$EXPECTED_SHA" -- || fail "release source worktree has tracked changes; refusing to build"
  untracked="$(git -C "$source" ls-files --others)" || fail "cannot inspect release source worktree files"
  [[ -z "$untracked" ]] || fail "release source worktree has untracked files, including ignored files; refusing to build"
}

if [[ "${1:-}" == "--rollback" ]]; then
  fail "manual rollback uses the saved rollback-compose.yml described in docs/ozon-finance/RELEASE.md"
fi
EXPECTED_SHA="${1:-}"
SERVER_ROOT="${2:-/root/wb-finance}"
PREPARED="${3:-}"
is_sha "$EXPECTED_SHA" || fail "expected a full 40-character commit SHA"

RELEASE_ROOT="${WB_FINANCE_RELEASE_ROOT:-/root/wb-finance-releases}"
BACKUP_ROOT="${WB_FINANCE_BACKUP_ROOT:-/root/backups/wb-finance}"
LOCK_PATH="${WB_FINANCE_RELEASE_LOCK:-/run/lock/wb-finance-ozon-release.lock}"
COMPOSE_FILE="$SERVER_ROOT/docker-compose.yml"
FRONTEND_DIST="$SERVER_ROOT/frontend/dist"

[[ -d "$SERVER_ROOT/.git" || -f "$SERVER_ROOT/.git" ]] || fail "server checkout is not a Git worktree"
[[ -f "$COMPOSE_FILE" && -d "$FRONTEND_DIST" ]] || fail "server checkout is missing compose or frontend dist"
command -v docker >/dev/null || fail "docker is unavailable"
command -v flock >/dev/null || fail "flock is unavailable"

mkdir -p "$(dirname "$LOCK_PATH")" "$RELEASE_ROOT" "$BACKUP_ROOT/releases"
chmod 700 "$BACKUP_ROOT" "$BACKUP_ROOT/releases"
exec 9>"$LOCK_PATH"
flock -n 9 || fail "another release is already running"

SERVER_BRANCH="$(git -C "$SERVER_ROOT" branch --show-current)"
[[ "$SERVER_BRANCH" == "main" ]] || fail "server checkout must remain on main"
REMOTE_MAIN="$(git -C "$SERVER_ROOT" rev-parse --verify 'origin/main^{commit}' 2>/dev/null || true)"
[[ "$REMOTE_MAIN" == "$EXPECTED_SHA" ]] || fail "expected SHA does not equal fetched origin/main"
git -C "$SERVER_ROOT" cat-file -e "$EXPECTED_SHA^{commit}" 2>/dev/null || fail "expected commit is not available locally"

SOURCE_DIR="$RELEASE_ROOT/$EXPECTED_SHA/source"
if [[ "$PREPARED" != "--prepared" ]]; then
  if [[ -e "$SOURCE_DIR" ]]; then
    validate_source "$SOURCE_DIR"
  else
    mkdir -p "$(dirname "$SOURCE_DIR")"
    git -C "$SERVER_ROOT" worktree add --detach "$SOURCE_DIR" "$EXPECTED_SHA" >/dev/null 2>&1
    validate_source "$SOURCE_DIR"
  fi
  exec bash "$SOURCE_DIR/scripts/release-ozon.sh" "$EXPECTED_SHA" "$SERVER_ROOT" --prepared
fi

validate_source "$SOURCE_DIR"
SERVER_SHA="$(git -C "$SERVER_ROOT" rev-parse HEAD)"
[[ "$SERVER_SHA" != "$EXPECTED_SHA" ]] || fail "server checkout already points at this commit; refusing ambiguous redeploy"

[[ -f "$SOURCE_DIR/frontend/dist/index.html" ]] || fail "release has no built frontend index"
[[ -d "$SOURCE_DIR/frontend/dist/assets" ]] || fail "release has no built frontend assets"

# Check every same-name static file before stopping services. Old and new hashed assets can coexist;
# mutable public files such as icons.svg must be byte-identical for this narrow in-place publish.
while IFS= read -r -d '' source_file; do
  relative="${source_file#"$SOURCE_DIR/frontend/dist/"}"
  [[ "$relative" != "index.html" ]] || continue
  target_file="$FRONTEND_DIST/$relative"
  if [[ -e "$target_file" ]] && ! cmp -s "$source_file" "$target_file"; then
    fail "frontend asset name collision at $relative; application services were not stopped"
  fi
done < <(find "$SOURCE_DIR/frontend/dist" -type f -print0)

# Building the release image unpacks large browser layers into Docker's data directory. A full disk
# mid-build fails late, so require free space before any backup, image tag, or service change.
MIN_FREE_GB="${WB_FINANCE_MIN_FREE_GB:-4}"
[[ "$MIN_FREE_GB" =~ ^[0-9]+$ ]] || fail "WB_FINANCE_MIN_FREE_GB must be a whole number of gigabytes"
DOCKER_ROOT="$(docker info --format '{{.DockerRootDir}}')" || fail "cannot determine the Docker data directory"
[[ -d "$DOCKER_ROOT" ]] || fail "Docker data directory $DOCKER_ROOT is not visible to this script"
free_kb="$(df -Pk "$DOCKER_ROOT" | awk 'NR == 2 { print $4 }')"
[[ "$free_kb" =~ ^[0-9]+$ ]] || fail "cannot read free disk space for $DOCKER_ROOT"
if (( free_kb < MIN_FREE_GB * 1024 * 1024 )); then
  fail "only $((free_kb / 1024)) MB free for Docker at $DOCKER_ROOT; the release image build needs at least ${MIN_FREE_GB} GB. Nothing was backed up, built, or stopped"
fi

compose() {
  local override="$1"
  shift
  docker compose --project-directory "$SERVER_ROOT" -f "$COMPOSE_FILE" -f "$override" "$@"
}

for service in postgres redis qdrant api celery_worker celery_beat; do
  container_id="$(docker compose --project-directory "$SERVER_ROOT" -f "$COMPOSE_FILE" ps --status running -q "$service")"
  [[ -n "$container_id" ]] || fail "required service $service is not running; refusing to start infrastructure"
  if [[ "$service" == postgres || "$service" == redis || "$service" == qdrant ]]; then
    health="$(docker inspect --format '{{.State.Health.Status}}' "$container_id")"
    [[ "$health" == healthy ]] || fail "required service $service is not healthy; refusing to start infrastructure"
  fi
done
curl -fsS --max-time 5 http://127.0.0.1:8000/health >/dev/null || fail "current API health check failed"

RUN_ID="$(date -u +%Y%m%dT%H%M%SZ)"
BACKUP_DIR="$BACKUP_ROOT/releases/$EXPECTED_SHA-$RUN_ID"
mkdir -m 700 "$BACKUP_DIR" || fail "backup directory already exists or cannot be created"
mkdir -m 700 "$BACKUP_DIR/frontend-dist-before"
cp -a "$FRONTEND_DIST/." "$BACKUP_DIR/frontend-dist-before/"
chmod -R go-rwx "$BACKUP_DIR/frontend-dist-before"

# A consistent PostgreSQL dump is captured before stopping or rebuilding any app service.
docker compose --project-directory "$SERVER_ROOT" -f "$COMPOSE_FILE" exec -T postgres \
  pg_dump -U wb_finance -d wb_finance --format=custom >"$BACKUP_DIR/database.dump" || fail "PostgreSQL backup failed; app services were not changed"
[[ -s "$BACKUP_DIR/database.dump" ]] || fail "PostgreSQL backup is empty; app services were not changed"
chmod 600 "$BACKUP_DIR/database.dump"

PREVIOUS_SOURCE="$RELEASE_ROOT/$EXPECTED_SHA/previous-source-$RUN_ID"
mkdir -m 700 -p "$PREVIOUS_SOURCE/backend"
PREVIOUS_APP_SOURCE=""
PREVIOUS_CELERY_SOURCE=""
PREVIOUS_SCRIPTS_SOURCE=""

mount_source() {
  local service="$1" destination="$2" container_id
  container_id="$(docker compose --project-directory "$SERVER_ROOT" -f "$COMPOSE_FILE" ps -q "$service")"
  docker inspect --format "{{range .Mounts}}{{if eq .Destination \"$destination\"}}{{.Source}}{{end}}{{end}}" "$container_id"
}

for entry in "api app" "celery_worker app" "celery_beat app" "api celery_app" "celery_worker celery_app" "celery_beat celery_app" "api scripts" "celery_worker scripts"; do
  read -r service directory <<<"$entry"
  case "$directory" in
    app) destination=/app/app ;;
    celery_app) destination=/app/celery_app ;;
    scripts) destination=/app/scripts ;;
  esac
  source_path="$(mount_source "$service" "$destination")"
  [[ -d "$source_path" ]] || fail "cannot verify the live $service source mount at $destination"
  source_path="$(realpath "$source_path")"
  destination_path="$PREVIOUS_SOURCE/backend/$directory"
  case "$directory" in
    app) prior_source="$PREVIOUS_APP_SOURCE" ;;
    celery_app) prior_source="$PREVIOUS_CELERY_SOURCE" ;;
    scripts) prior_source="$PREVIOUS_SCRIPTS_SOURCE" ;;
  esac
  if [[ -n "$prior_source" ]]; then
    [[ "$prior_source" == "$source_path" ]] || fail "live services use different $directory source trees"
  else
    case "$directory" in
      app) PREVIOUS_APP_SOURCE="$source_path" ;;
      celery_app) PREVIOUS_CELERY_SOURCE="$source_path" ;;
      scripts) PREVIOUS_SCRIPTS_SOURCE="$source_path" ;;
    esac
    cp -a "$source_path" "$destination_path"
  fi
done
api_container="$(docker compose --project-directory "$SERVER_ROOT" -f "$COMPOSE_FILE" ps -q api)"
previous_image_id="$(docker inspect --format '{{.Image}}' "$api_container")"
previous_image_revision="$(docker image inspect --format '{{ index .Config.Labels "org.opencontainers.image.revision" }}' "$previous_image_id" 2>/dev/null || true)"
if ! is_sha "$previous_image_revision"; then previous_image_revision="legacy-unlabeled"; fi

IMAGE_TAG_API=""
IMAGE_TAG_WORKER=""
IMAGE_TAG_BEAT=""
for service in api celery_worker celery_beat; do
  container_id="$(docker compose --project-directory "$SERVER_ROOT" -f "$COMPOSE_FILE" ps -q "$service")"
  image_id="$(docker inspect --format '{{.Image}}' "$container_id")"
  image_tag="wb-finance-rollback-${service}:${EXPECTED_SHA}-${RUN_ID}"
  docker image tag "$image_id" "$image_tag"
  case "$service" in
    api) IMAGE_TAG_API="$image_tag" ;;
    celery_worker) IMAGE_TAG_WORKER="$image_tag" ;;
    celery_beat) IMAGE_TAG_BEAT="$image_tag" ;;
  esac
done

DEPLOY_OVERRIDE="$BACKUP_DIR/deploy-compose.yml"
ROLLBACK_OVERRIDE="$BACKUP_DIR/rollback-compose.yml"
cat >"$DEPLOY_OVERRIDE" <<EOF
services:
  api:
    image: wb-finance-api:${EXPECTED_SHA}-${RUN_ID}
    build:
      context: ${SOURCE_DIR}/backend
      dockerfile: Dockerfile
      args:
        VCS_REF: ${EXPECTED_SHA}
    volumes:
      - ${SERVER_ROOT}/backend/.env:/app/.env
      - ${SOURCE_DIR}/backend/app:/app/app
      - ${SOURCE_DIR}/backend/celery_app:/app/celery_app
      - ${SOURCE_DIR}/backend/scripts:/app/scripts
      - ${SOURCE_DIR}/backend/alembic:/app/alembic
      - ${SOURCE_DIR}/backend/alembic.ini:/app/alembic.ini
      - ${SERVER_ROOT}/backend/data:/app/data
  celery_worker:
    image: wb-finance-worker:${EXPECTED_SHA}-${RUN_ID}
    build:
      context: ${SOURCE_DIR}/backend
      dockerfile: Dockerfile
      args:
        VCS_REF: ${EXPECTED_SHA}
    volumes:
      - ${SERVER_ROOT}/backend/.env:/app/.env
      - ${SOURCE_DIR}/backend/app:/app/app
      - ${SOURCE_DIR}/backend/celery_app:/app/celery_app
      - ${SOURCE_DIR}/backend/scripts:/app/scripts
      - ${SOURCE_DIR}/backend/alembic:/app/alembic
      - ${SOURCE_DIR}/backend/alembic.ini:/app/alembic.ini
      - ${SERVER_ROOT}/backend/data:/app/data
      - ${SERVER_ROOT}/backend/wb_storage_state.json:/app/wb_storage_state.json:ro
  celery_beat:
    image: wb-finance-beat:${EXPECTED_SHA}-${RUN_ID}
    build:
      context: ${SOURCE_DIR}/backend
      dockerfile: Dockerfile
      args:
        VCS_REF: ${EXPECTED_SHA}
    volumes:
      - ${SERVER_ROOT}/backend/.env:/app/.env
      - ${SOURCE_DIR}/backend/app:/app/app
      - ${SOURCE_DIR}/backend/celery_app:/app/celery_app
      - ${SOURCE_DIR}/backend/alembic:/app/alembic
      - ${SOURCE_DIR}/backend/alembic.ini:/app/alembic.ini
      - ${SERVER_ROOT}/backend/data:/app/data
EOF
cat >"$ROLLBACK_OVERRIDE" <<EOF
services:
  api:
    image: ${IMAGE_TAG_API}
    volumes:
      - ${SERVER_ROOT}/backend/.env:/app/.env
      - ${PREVIOUS_SOURCE}/backend/app:/app/app
      - ${PREVIOUS_SOURCE}/backend/celery_app:/app/celery_app
      - ${PREVIOUS_SOURCE}/backend/scripts:/app/scripts
      - ${SOURCE_DIR}/backend/alembic:/app/alembic
      - ${SOURCE_DIR}/backend/alembic.ini:/app/alembic.ini
      - ${SERVER_ROOT}/backend/data:/app/data
  celery_worker:
    image: ${IMAGE_TAG_WORKER}
    volumes:
      - ${SERVER_ROOT}/backend/.env:/app/.env
      - ${PREVIOUS_SOURCE}/backend/app:/app/app
      - ${PREVIOUS_SOURCE}/backend/celery_app:/app/celery_app
      - ${PREVIOUS_SOURCE}/backend/scripts:/app/scripts
      - ${SOURCE_DIR}/backend/alembic:/app/alembic
      - ${SOURCE_DIR}/backend/alembic.ini:/app/alembic.ini
      - ${SERVER_ROOT}/backend/data:/app/data
      - ${SERVER_ROOT}/backend/wb_storage_state.json:/app/wb_storage_state.json:ro
  celery_beat:
    image: ${IMAGE_TAG_BEAT}
    volumes:
      - ${SERVER_ROOT}/backend/.env:/app/.env
      - ${PREVIOUS_SOURCE}/backend/app:/app/app
      - ${PREVIOUS_SOURCE}/backend/celery_app:/app/celery_app
      - ${SOURCE_DIR}/backend/alembic:/app/alembic
      - ${SOURCE_DIR}/backend/alembic.ini:/app/alembic.ini
      - ${SERVER_ROOT}/backend/data:/app/data
EOF
chmod 600 "$DEPLOY_OVERRIDE" "$ROLLBACK_OVERRIDE"
printf 'release-ozon: pre-release database, source, and frontend snapshots are under %s\n' "$BACKUP_DIR"

DEPLOY_OVERRIDE_PATH="$DEPLOY_OVERRIDE"
ROLLBACK_OVERRIDE_PATH="$ROLLBACK_OVERRIDE"
SERVICES_STOPPED=0
FRONTEND_SWITCHED=0
rollback_after_failure() {
  local code=$?
  trap - EXIT
  if [[ "$code" != "0" && "$SERVICES_STOPPED" == "1" ]]; then
    printf 'release-ozon: deployment failed; restoring previous code and image tags\n' >&2
    docker compose --project-directory "$SERVER_ROOT" -f "$COMPOSE_FILE" -f "$ROLLBACK_OVERRIDE_PATH" \
      up -d --no-deps api celery_worker celery_beat >/dev/null 2>&1 || true
    local restored=0
    for _ in $(seq 1 36); do
      if curl -fsS --max-time 5 http://127.0.0.1:8000/health >/dev/null 2>&1; then
        restored=1
        break
      fi
      sleep 5
    done
    if [[ "$FRONTEND_SWITCHED" == "1" ]]; then
      install -m 0644 "$BACKUP_DIR/frontend-dist-before/index.html" "$FRONTEND_DIST/.index.rollback.$$"
      mv -f "$FRONTEND_DIST/.index.rollback.$$" "$FRONTEND_DIST/index.html"
    fi
    for service in api celery_worker celery_beat; do
      local restored_container expected_image restored_image
      restored_container="$(docker compose --project-directory "$SERVER_ROOT" -f "$COMPOSE_FILE" -f "$ROLLBACK_OVERRIDE_PATH" ps --status running -q "$service")"
      case "$service" in
        api) expected_image="$IMAGE_TAG_API" ;;
        celery_worker) expected_image="$IMAGE_TAG_WORKER" ;;
        celery_beat) expected_image="$IMAGE_TAG_BEAT" ;;
      esac
      if [[ -z "$restored_container" ]]; then
        restored=0
        continue
      fi
      restored_image="$(docker inspect --format '{{.Config.Image}}' "$restored_container" 2>/dev/null || true)"
      if [[ "$restored_image" != "$expected_image" ]]; then
        restored=0
      fi
    done
    if [[ "$restored" != "1" ]]; then
      printf 'release-ozon: automatic code rollback did not pass API and worker checks; inspect the saved manifest and service logs\n' >&2
    else
      printf 'release-ozon: previous API and workers are healthy; database schema and data were left intact\n' >&2
    fi
  fi
  exit "$code"
}
trap rollback_after_failure EXIT

# The three services share one Dockerfile and context. Build the image once and tag it for the workers:
# a parallel Compose build of all three unpacks the browser layers three times and can fill the disk.
compose "$DEPLOY_OVERRIDE" build --build-arg "VCS_REF=$EXPECTED_SHA" api
docker image tag "wb-finance-api:${EXPECTED_SHA}-${RUN_ID}" "wb-finance-worker:${EXPECTED_SHA}-${RUN_ID}"
docker image tag "wb-finance-api:${EXPECTED_SHA}-${RUN_ID}" "wb-finance-beat:${EXPECTED_SHA}-${RUN_ID}"
for service in api celery_worker celery_beat; do
  image="wb-finance-${service#celery_}:${EXPECTED_SHA}-${RUN_ID}"
  case "$service" in
    api) image="wb-finance-api:${EXPECTED_SHA}-${RUN_ID}" ;;
    celery_worker) image="wb-finance-worker:${EXPECTED_SHA}-${RUN_ID}" ;;
    celery_beat) image="wb-finance-beat:${EXPECTED_SHA}-${RUN_ID}" ;;
  esac
  revision="$(docker image inspect --format '{{ index .Config.Labels "org.opencontainers.image.revision" }}' "$image")"
  [[ "$revision" == "$EXPECTED_SHA" ]] || fail "built $service image has an unexpected revision label"
done

# Exercise the built API image and its live Compose environment before stopping any service.
# Import the app and migration graph, then make only an explicitly read-only database query.
PROBE_LOG="$BACKUP_DIR/api-preflight.log"
if ! compose "$DEPLOY_OVERRIDE" run --rm --no-deps --entrypoint python api -c '
import app.main
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import text
from app.db import engine

assert ScriptDirectory.from_config(Config("/app/alembic.ini")).get_heads()
with engine.connect() as connection:
    connection.exec_driver_sql("SET TRANSACTION READ ONLY")
    assert connection.execute(text("SELECT 1")).scalar_one() == 1
' >/dev/null 2>&1; then
  printf 'status=failed\n' >"$PROBE_LOG"
  chmod 600 "$PROBE_LOG"
  fail "built API preflight failed; private diagnostics contain only a sanitized status marker at $PROBE_LOG"
fi
printf 'status=passed\n' >"$PROBE_LOG"
chmod 600 "$PROBE_LOG"

SERVICES_STOPPED=1
compose "$DEPLOY_OVERRIDE" stop -t 60 celery_worker celery_beat api
compose "$DEPLOY_OVERRIDE" up -d --no-deps --no-build api
health_timeout="${WB_FINANCE_HEALTH_TIMEOUT_SECONDS:-180}"
deadline=$((SECONDS + health_timeout))
until curl -fsS --max-time 5 http://127.0.0.1:8000/health >/dev/null 2>&1; do
  (( SECONDS < deadline )) || fail "new API did not become healthy"
  sleep 5
done
compose "$DEPLOY_OVERRIDE" up -d --no-deps --no-build celery_worker celery_beat

# Keep existing hashed assets for clients with cached HTML; publish the new index last.
while IFS= read -r -d '' source_file; do
  relative="${source_file#"$SOURCE_DIR/frontend/dist/"}"
  [[ "$relative" != "index.html" ]] || continue
  target_file="$FRONTEND_DIST/$relative"
  mkdir -p "$(dirname "$target_file")"
  [[ -e "$target_file" ]] && continue
  temp_file="$target_file.$EXPECTED_SHA.tmp"
  install -m 0644 "$source_file" "$temp_file"
  mv "$temp_file" "$target_file"
done < <(find "$SOURCE_DIR/frontend/dist" -type f -print0)
temp_index="$FRONTEND_DIST/.index.$EXPECTED_SHA.tmp"
install -m 0644 "$SOURCE_DIR/frontend/dist/index.html" "$temp_index"
FRONTEND_SWITCHED=1
mv -f "$temp_index" "$FRONTEND_DIST/index.html"

for service in api celery_worker celery_beat; do
  [[ -n "$(compose "$DEPLOY_OVERRIDE" ps --status running -q "$service")" ]] || fail "$service is not running after release"
done
curl -fsS --max-time 5 http://127.0.0.1:8000/health >/dev/null || fail "API health failed after release"

cat >"$BACKUP_DIR/release-manifest.json" <<EOF
{
  "release_sha": "${EXPECTED_SHA}",
  "server_checkout_sha": "${SERVER_SHA}",
  "previous_image_revision": "${previous_image_revision}",
  "previous_source_snapshot": "${PREVIOUS_SOURCE}",
  "images": {
    "api": "wb-finance-api:${EXPECTED_SHA}-${RUN_ID}",
    "celery_worker": "wb-finance-worker:${EXPECTED_SHA}-${RUN_ID}",
    "celery_beat": "wb-finance-beat:${EXPECTED_SHA}-${RUN_ID}"
  },
  "previous_image_tags": {
    "api": "${IMAGE_TAG_API}",
    "celery_worker": "${IMAGE_TAG_WORKER}",
    "celery_beat": "${IMAGE_TAG_BEAT}"
  },
  "release_worktree": "${SOURCE_DIR}",
  "database_backup": "${BACKUP_DIR}/database.dump",
  "frontend_before": "${BACKUP_DIR}/frontend-dist-before",
  "rollback_compose": "${ROLLBACK_OVERRIDE}",
  "deployed_at_utc": "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
}
EOF
chmod 600 "$BACKUP_DIR/release-manifest.json"
SERVICES_STOPPED=0
printf 'release-ozon: deployed %s; manifest %s/release-manifest.json\n' "$EXPECTED_SHA" "$BACKUP_DIR"
