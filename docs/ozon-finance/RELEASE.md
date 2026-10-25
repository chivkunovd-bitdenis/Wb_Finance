# Ozon finance release on production

The production workflow now deploys the exact commit that passed the existing verification job. On the server it checks that the fetched `origin/main` equals the workflow SHA, creates a detached source worktree for that commit, and leaves `/root/wb-finance` on its existing `main` checkout. The checkout's dirty `Caddyfile` and unrelated untracked files are not reset, removed, or copied over.

Before stopping application services, the helper saves a PostgreSQL custom-format dump under `/root/backups/wb-finance/releases/`, copies the code from the currently running containers' bind mounts, and snapshots the currently served frontend. This records the real prior source even when the old image has no revision label and the server checkout still points at an older commit. Backup directories are private (mode `0700`) and the database dump is mode `0600`. If the backup fails, the helper stops before changing application services. A per-server `flock` prevents automatic and manual releases from overlapping.

The helper first checks that the detached release worktree is at the expected commit and has no tracked changes or untracked files, including files ignored by Git. Ignored files can still enter a Docker build context unless that context excludes them. If a retry finds a dirty release worktree, it stops without cleaning or changing it; inspect that worktree manually before retrying. Before taking any backup or touching an image or service, it checks free space in Docker's data directory (`docker info` reports it, usually `/var/lib/docker`) and stops with a clear message if less than 4 GB is free; set `WB_FINANCE_MIN_FREE_GB` to use another whole-number threshold. The three application services share one Dockerfile and build context, so the helper builds the image once as `api` and tags that same image for `celery_worker` and `celery_beat`. Building all three in parallel unpacked the large Playwright/Chromium layers three times and ran the 50 GB production disk out of space on 25.10.2026. The commit SHA is recorded in the OCI image revision label and checked on all three tags, and the new API, worker, and scheduler are started with `--no-build`, so Compose cannot fall back to building them again. Before stopping services, it runs the built API in a one-off Compose container, imports the application and migration graph, and executes a read-only database query using the existing environment. A failure stops deployment while the old application continues running; raw output is discarded and only a fixed status is written to a private backup log. It then stops those three services, starts the API first, and waits for its health check. The existing API entrypoint runs Alembic (the database migration tool) before Uvicorn; no second migration command runs. Once the API is healthy, it starts the worker and scheduler. Existing Postgres, Redis, Qdrant, image-pipeline, auth, and Caddy containers are not rebuilt or recreated. The Celery queue and WB sync/backfill rows are left intact, and the helper does not edit credentials or firewall rules.

The backend code is bind-mounted in Compose, so the release override mounts source from the exact-SHA worktree while keeping `.env` and persistent `backend/data` on the server checkout. Caddy serves `frontend/dist` from the checkout; the helper checks for same-name asset conflicts before stopping services, adds new content-hashed assets without deleting old ones, then atomically replaces `index.html` last. It does not restart Caddy or edit `Caddyfile`. Each successful deployment prints the path to a private `release-manifest.json` that records the deployed SHA, source worktree, database backup, prior source snapshot, prior frontend snapshot, and rollback compose file.

## Rollback

If the new API fails its health check after application services stop, the helper automatically brings the prior API, worker, and scheduler back using image tags and source paths saved before the build. If the frontend index was already switched, it restores the previous index atomically. It does not restore the database automatically.

For a deliberate rollback after a successful release, use the backup directory printed in that release's manifest. Run the following as one block after replacing the placeholder. The same release lock covers both the container changes and frontend restore; if another release holds it, stop and wait. Keep both old and new hashed assets in place for cached pages.

```bash
(
  set -euo pipefail
  RELEASE_BACKUP='/root/backups/wb-finance/releases/<release-sha>-<utc-run-id>'
  exec 9>/run/lock/wb-finance-ozon-release.lock
  flock -n 9 || { echo 'Another release is running; rollback was not started.' >&2; exit 1; }
  docker compose --project-directory /root/wb-finance \
    -f /root/wb-finance/docker-compose.yml \
    -f "$RELEASE_BACKUP/rollback-compose.yml" \
    up -d --no-deps api celery_worker celery_beat
  curl --fail --silent --show-error --retry 30 --retry-connrefused --retry-delay 5 \
    --max-time 5 http://127.0.0.1:8000/health
  install -m 0644 "$RELEASE_BACKUP/frontend-dist-before/index.html" \
    /root/wb-finance/frontend/dist/.index.rollback
  mv -f /root/wb-finance/frontend/dist/.index.rollback /root/wb-finance/frontend/dist/index.html
)
```

The Ozon schema changes in this release only add tables and indexes. The migrations' `downgrade()` functions drop Ozon tables, so rollback must not run `alembic downgrade` or drop those tables: doing so would erase imported accruals, settings, and user-entered costs. The rollback override keeps the forward release's migration files mounted, allowing the existing entrypoint to recognize the database's newer head while the previous application code runs. If startup failed before the migrations began, the previous image may apply those additive migrations while starting against the preserved forward files. The first release rollback exercised this path: the Ozon tables were added and WB row checksums remained unchanged. Keep the database at that additive schema version. A database restore from the saved dump is a separate recovery decision because it discards writes made after the snapshot.

## Limits of this release path

The deploy workflow retains its existing lint, type, and test verification steps. This helper does not bypass them. Ozon connection credentials remain an application setting and require the already configured encryption key; release automation never reads, creates, or changes secret values.
