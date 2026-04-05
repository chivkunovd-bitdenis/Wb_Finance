"""
Старт API в Docker: миграции Alembic, затем uvicorn.

Если в alembic_version уже одна строка и она совпадает с единственным head в
скриптах — upgrade не вызываем. Так обходится сбой Alembic «overlaps» при
попытке upgrade head, когда схема и версия уже актуальны.
"""
from __future__ import annotations

import os
import subprocess
import sys
import traceback
from pathlib import Path

_APP = Path("/app")
_ALEMBIC_INI = _APP / "alembic.ini"
_ALEMBIC_DIR = _APP / "alembic"


def _script_heads() -> list[str]:
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    cfg = Config(str(_ALEMBIC_INI))
    cfg.set_main_option("script_location", str(_ALEMBIC_DIR))
    return list(ScriptDirectory.from_config(cfg).get_heads())


def _db_version_rows(url: str) -> list[str]:
    from sqlalchemy import create_engine, text

    eng = create_engine(url)
    with eng.connect() as conn:
        rows = conn.execute(text("SELECT version_num FROM alembic_version ORDER BY version_num")).fetchall()
    return [str(r[0]).strip() for r in rows]


def _exec_uvicorn() -> None:
    os.execvp(
        sys.executable,
        [
            sys.executable,
            "-m",
            "uvicorn",
            "app.main:app",
            "--host",
            "0.0.0.0",
            "--port",
            "8000",
        ],
    )


def main() -> None:
    os.chdir(_APP)
    db_url = (os.environ.get("DATABASE_URL") or "").strip()

    if db_url:
        try:
            db_revs = _db_version_rows(db_url)
            heads = [h.strip() for h in _script_heads()]
            print(f"docker_entrypoint_api: alembic_version={db_revs!r} script_heads={heads!r}", flush=True)
            if len(heads) > 1:
                print(
                    f"docker_entrypoint_api: в каталоге alembic несколько heads {heads!r} — проверьте down_revision",
                    flush=True,
                )
            if len(db_revs) > 1:
                print(f"docker_entrypoint_api: в alembic_version несколько строк {db_revs!r}", flush=True)
            if len(db_revs) == 1 and len(heads) == 1 and db_revs[0] == heads[0]:
                print(f"docker_entrypoint_api: DB at head {heads[0]!r}, skipping alembic upgrade", flush=True)
                _exec_uvicorn()
        except Exception:
            print("docker_entrypoint_api: pre-check failed, traceback:", flush=True)
            traceback.print_exc()
            print("docker_entrypoint_api: falling through to alembic upgrade head", flush=True)

    print("docker_entrypoint_api: running alembic upgrade head", flush=True)
    subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], check=True)
    _exec_uvicorn()


if __name__ == "__main__":
    try:
        main()
    except subprocess.CalledProcessError as exc:
        sys.exit(exc.returncode)
