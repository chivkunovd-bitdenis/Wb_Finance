"""Mocked safety checks for the SHA-pinned production release helper."""

from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "release-ozon.sh"


class ReleaseOzonScriptTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(prefix="wb-release-test-")
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.server = self.base / "server"
        self.server.mkdir()
        self._git("init", "--initial-branch=main", str(self.server))
        self._git("-C", str(self.server), "config", "user.name", "Release Test")
        self._git("-C", str(self.server), "config", "user.email", "release-test@example.invalid")
        self._write_base_files()
        (self.server / ".gitignore").write_text("*.local\n")
        self._git("-C", str(self.server), "add", ".")
        self._git("-C", str(self.server), "commit", "-m", "base")
        self.base_sha = self._git("-C", str(self.server), "rev-parse", "HEAD").stdout.strip()

        self._git("-C", str(self.server), "checkout", "-b", "release")
        (self.server / "scripts").mkdir(parents=True, exist_ok=True)
        shutil.copy2(SCRIPT, self.server / "scripts" / "release-ozon.sh")
        (self.server / "backend" / "Dockerfile").write_text("FROM python:3.12-slim\nARG VCS_REF=unknown\n")
        new_dist = self.server / "frontend" / "dist"
        (new_dist / "index.html").write_text('<script src="/assets/index-new.js"></script>\n')
        (new_dist / "assets" / "index-new.js").write_text("new bundle\n")
        (new_dist / "assets" / "index-new.css").write_text("new styles\n")
        self._git("-C", str(self.server), "add", ".")
        self._git("-C", str(self.server), "commit", "-m", "release")
        self.release_sha = self._git("-C", str(self.server), "rev-parse", "HEAD").stdout.strip()
        self._git("-C", str(self.server), "update-ref", "refs/remotes/origin/main", self.release_sha)
        self._git("-C", str(self.server), "checkout", "main")

        self.bin = self.base / "bin"
        self.bin.mkdir()
        self.log = self.base / "docker.log"
        self.rollback_marker = self.base / "rolled-back"
        self.new_api_marker = self.base / "new-api"
        self.probe_marker = self.base / "api-probed"
        self.env = os.environ.copy()
        self.env.update(
            {
                "PATH": f"{self.bin}{os.pathsep}{self.env['PATH']}",
                "WB_FINANCE_RELEASE_ROOT": str(self.base / "releases"),
                "WB_FINANCE_BACKUP_ROOT": str(self.base / "backups"),
                "WB_FINANCE_RELEASE_LOCK": str(self.base / "release.lock"),
                "WB_FINANCE_HEALTH_TIMEOUT_SECONDS": "1",
                "FAKE_DOCKER_LOG": str(self.log),
                "FAKE_IMAGE_TAGS": str(self.base / "image-tags.log"),
                "FAKE_ROLLBACK_MARKER": str(self.rollback_marker),
                "FAKE_NEW_API_MARKER": str(self.new_api_marker),
                "FAKE_PROBE_MARKER": str(self.probe_marker),
                "FAKE_EXPECTED_SHA": self.release_sha,
                "FAKE_SERVER_ROOT": str(self.server),
            }
        )
        self._write_fake_commands()

    def test_remote_sha_mismatch_has_no_docker_side_effects(self) -> None:
        result = self._run("0" * 40)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("does not equal fetched origin/main", result.stderr)
        self.assertFalse(self.log.exists())

    def test_dirty_exact_sha_source_is_rejected_before_docker(self) -> None:
        source = self.base / "releases" / self.release_sha / "source"
        source.parent.mkdir(parents=True)
        self._git("-C", str(self.server), "worktree", "add", "--detach", str(source), self.release_sha)
        (source / "backend" / "app" / "main.py").write_text("locally changed\n")

        result = self._run(self.release_sha)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("tracked changes", result.stderr)
        self.assertFalse(self.log.exists())

    def test_nonignored_untracked_source_is_rejected_before_docker(self) -> None:
        source = self.base / "releases" / self.release_sha / "source"
        source.parent.mkdir(parents=True)
        self._git("-C", str(self.server), "worktree", "add", "--detach", str(source), self.release_sha)
        (source / "backend" / "unexpected.py").write_text("untracked source\n")

        result = self._run(self.release_sha)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("untracked files, including ignored files", result.stderr)
        self.assertFalse(self.log.exists())

    def test_ignored_source_file_is_rejected_before_reexec_or_docker(self) -> None:
        source = self.base / "releases" / self.release_sha / "source"
        source.parent.mkdir(parents=True)
        self._git("-C", str(self.server), "worktree", "add", "--detach", str(source), self.release_sha)
        (source / "backend" / "private.local").write_text("ignored build context file\n")

        result = self._run(self.release_sha)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("untracked files, including ignored files", result.stderr)
        self.assertFalse(self.log.exists())

    def test_database_backup_failure_does_not_stop_or_build_services(self) -> None:
        self.env["FAKE_DUMP_FAIL"] = "1"
        result = self._run(self.release_sha)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("PostgreSQL backup failed", result.stderr)
        log = self.log.read_text()
        self.assertNotIn(" build ", f" {log} ")
        self.assertNotIn(" stop ", f" {log} ")
        self.assertNotIn(" up ", f" {log} ")

    def test_api_preflight_failure_does_not_stop_running_services_or_leak_dsn(self) -> None:
        self.env["FAKE_PROBE_FAIL"] = "1"
        result = self._run(self.release_sha)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("built API preflight failed", result.stderr)
        self.assertNotIn("postgresql://secret-user:secret-password", result.stderr)
        self.assertTrue(self.probe_marker.exists())
        log = self.log.read_text()
        self.assertNotIn(" stop ", f" {log} ")
        self.assertNotIn(" up ", f" {log} ")
        self.assertNotIn("postgresql://secret-user:secret-password", log)
        backup_dir = next((self.base / "backups" / "releases").iterdir())
        probe_log = backup_dir / "api-preflight.log"
        self.assertEqual(probe_log.read_text(), "status=failed\n")
        self.assertNotIn("postgresql://secret-user:secret-password", probe_log.read_text())
        self.assertEqual(probe_log.stat().st_mode & 0o777, 0o600)

    def test_exact_sha_builds_and_publishes_without_removing_cached_assets(self) -> None:
        self.env["FAKE_HEALTH_SUCCESS"] = "1"
        result = self._run(self.release_sha)
        self.assertEqual(result.returncode, 0, result.stderr)
        backup_dirs = list((self.base / "backups" / "releases").iterdir())
        self.assertEqual(len(backup_dirs), 1)
        manifest = json.loads((backup_dirs[0] / "release-manifest.json").read_text())
        self.assertEqual(manifest["release_sha"], self.release_sha)
        self.assertEqual((self.server / "frontend" / "dist" / "index.html").read_text(), '<script src="/assets/index-new.js"></script>\n')
        self.assertTrue((self.server / "frontend" / "dist" / "assets" / "index-old.js").exists())
        self.assertTrue((self.server / "frontend" / "dist" / "assets" / "index-new.js").exists())
        log = self.log.read_text()
        self.assertIn(f"VCS_REF={self.release_sha}", log)
        self.assertIn("run --rm --no-deps --entrypoint python api -c", log)
        self.assertTrue(self.probe_marker.exists())
        backup_dir = next((self.base / "backups" / "releases").iterdir())
        self.assertEqual((backup_dir / "api-preflight.log").read_text(), "status=passed\n")
        self.assertIn(self.release_sha, manifest["release_worktree"])
        self.assertNotIn("FLUSHDB", log)

    def test_failed_api_health_restores_previous_images_and_source(self) -> None:
        result = self._run(self.release_sha)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("previous API and workers are healthy", result.stderr)
        self.assertTrue(self.rollback_marker.exists())
        self.assertEqual((self.server / "frontend" / "dist" / "index.html").read_text(), "old index\n")
        log = self.log.read_text()
        self.assertIn("rollback-compose.yml", log)
        backup_dir = next((self.base / "backups" / "releases").iterdir())
        rollback_compose = (backup_dir / "rollback-compose.yml").read_text()
        self.assertIn("previous-source-", rollback_compose)

    def test_failed_rollback_does_not_claim_previous_images_are_restored(self) -> None:
        self.env["FAKE_BAD_ROLLBACK_IMAGE"] = "1"
        result = self._run(self.release_sha)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("automatic code rollback did not pass", result.stderr)
        self.assertNotIn("previous API and workers are healthy", result.stderr)

    def _run(self, sha: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["bash", str(SCRIPT), sha, str(self.server)],
            cwd=ROOT,
            env=self.env,
            text=True,
            capture_output=True,
            timeout=30,
            check=False,
        )

    def _write_base_files(self) -> None:
        files = {
            "docker-compose.yml": "services: {}\n",
            "backend/.env": "placeholder-do-not-read\n",
            "backend/Dockerfile": "FROM python:3.12-slim\n",
            "backend/alembic.ini": "[alembic]\n",
            "backend/wb_storage_state.json": "{}\n",
            "backend/app/main.py": "old api\n",
            "backend/celery_app/tasks.py": "old worker\n",
            "backend/scripts/helper.py": "old script\n",
            "backend/alembic/versions/old.py": "old migration\n",
            "backend/data/.keep": "persistent data stays in place\n",
            "frontend/dist/index.html": "old index\n",
            "frontend/dist/favicon.svg": "same favicon\n",
            "frontend/dist/icons.svg": "same icons\n",
            "frontend/dist/assets/index-old.js": "old bundle\n",
            "frontend/dist/assets/index-old.css": "old styles\n",
        }
        for relative, content in files.items():
            path = self.server / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content)

    def _write_fake_commands(self) -> None:
        docker = r'''#!/usr/bin/env bash
set -euo pipefail
printf '%s\n' "$*" >> "$FAKE_DOCKER_LOG"
if [[ "$1" == compose ]]; then
  [[ "$*" == *rollback-compose.yml\ up\ * ]] && touch "$FAKE_ROLLBACK_MARKER"
  [[ "$*" == *deploy-compose.yml\ up\ * ]] && touch "$FAKE_NEW_API_MARKER"
  if [[ " $* " == *" pg_dump "* ]]; then
    [[ "${FAKE_DUMP_FAIL:-0}" != 1 ]] || exit 31
    printf 'fake postgres dump\n'
    exit 0
  fi
  if [[ " $* " == *" run --rm --no-deps --entrypoint python api -c "* ]]; then
    touch "$FAKE_PROBE_MARKER"
    if [[ "${FAKE_PROBE_FAIL:-0}" == 1 ]]; then
      printf 'connection to postgresql://secret-user:secret-password@db/private failed\n'
      exit 32
    fi
    exit 0
  fi
  if [[ " $* " == *" ps "* ]]; then
    printf 'container-%s\n' "${*: -1}"
  fi
  exit 0
fi
if [[ "$1" == inspect && "$2" == --format ]]; then
  template="$3"
  if [[ "$template" == *Config.Image* ]]; then
    case "$4" in
      container-api) service=api ;;
      container-celery_worker) service=celery_worker ;;
      container-celery_beat) service=celery_beat ;;
      *) exit 2 ;;
    esac
    if [[ "${FAKE_BAD_ROLLBACK_IMAGE:-0}" == 1 && -e "$FAKE_ROLLBACK_MARKER" ]]; then
      printf 'wrong-image\n'
      exit 0
    fi
    awk -v service="$service" '$1 == service { print $2 }' "$FAKE_IMAGE_TAGS"
    exit 0
  fi
  if [[ "$template" == *State.Health.Status* ]]; then printf 'healthy\n'; exit 0; fi
  if [[ "$template" == *Mounts* ]]; then
    case "$template" in
      *"/app/app"*) printf '%s\n' "$FAKE_SERVER_ROOT/backend/app" ;;
      *"/app/celery_app"*) printf '%s\n' "$FAKE_SERVER_ROOT/backend/celery_app" ;;
      *"/app/scripts"*) printf '%s\n' "$FAKE_SERVER_ROOT/backend/scripts" ;;
      *) exit 2 ;;
    esac
    exit 0
  fi
  if [[ "$template" == *'.Image'* ]]; then printf 'sha256:old-%s\n' "$4"; exit 0; fi
fi
if [[ "$1" == image && "$2" == inspect ]]; then
  if [[ "$4" == *org.opencontainers.image.revision* ]]; then
    [[ "$5" == sha256:* ]] && { printf 'legacy-unlabeled\n'; exit 0; }
    printf '%s\n' "$FAKE_EXPECTED_SHA"
  fi
  exit 0
fi
if [[ "$1" == image && "$2" == tag ]]; then
  case "$3" in
    *container-api) service=api ;;
    *container-celery_worker) service=celery_worker ;;
    *container-celery_beat) service=celery_beat ;;
    *) exit 2 ;;
  esac
  printf '%s %s\n' "$service" "$4" >> "$FAKE_IMAGE_TAGS"
  exit 0
fi
exit 0
'''
        curl = r'''#!/usr/bin/env bash
if [[ -e "$FAKE_ROLLBACK_MARKER" ]]; then exit 0; fi
if [[ -e "$FAKE_NEW_API_MARKER" && "${FAKE_HEALTH_SUCCESS:-0}" != 1 ]]; then exit 22; fi
exit 0
'''
        sleep = "#!/usr/bin/env bash\nexit 0\n"
        flock = "#!/usr/bin/env bash\nexit 0\n"
        for name, content in (("docker", docker), ("curl", curl), ("sleep", sleep), ("flock", flock)):
            path = self.bin / name
            path.write_text(content)
            path.chmod(0o755)

    @staticmethod
    def _git(*args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(["git", *args], text=True, capture_output=True, check=True)


if __name__ == "__main__":
    unittest.main()
