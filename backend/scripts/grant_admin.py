"""Назначить пользователю is_admin=true. Запуск из каталога backend.

  cd backend && python scripts/grant_admin.py "user@example.com"

На проде (в контейнере api):

  docker compose exec api python scripts/grant_admin.py "denischivkunov@icloud.com"
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

_env = ROOT_DIR / ".env"
if _env.is_file():
    from dotenv import load_dotenv

    load_dotenv(_env, override=False)

from app.db import SessionLocal  # noqa: E402
from app.models.user import User  # noqa: E402


def grant_admin_by_email(*, db, email: str) -> User:
    normalized = email.lower().strip()
    user = db.query(User).filter(User.email == normalized).first()
    if user is None:
        raise LookupError(f"no user with email {normalized!r}")
    user.is_admin = True
    db.add(user)
    return user


def main() -> int:
    parser = argparse.ArgumentParser(description="Grant is_admin by user email.")
    parser.add_argument("email", help="Registered user email (case-insensitive)")
    args = parser.parse_args()
    email = str(args.email).strip()
    if not email:
        print("error: empty email", file=sys.stderr)
        return 2

    db = SessionLocal()
    try:
        user = grant_admin_by_email(db=db, email=email)
        db.commit()
        print(f"ok: is_admin=true for {user.email} (user_id={user.id})")
        return 0
    except LookupError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
