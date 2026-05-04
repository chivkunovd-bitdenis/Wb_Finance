"""Выдать пользователю lifetime-доступ (лицензия в БД). Запуск из каталога backend.

  cd backend && python scripts/grant_lifetime_access.py "user@example.com"

DATABASE_URL берётся из окружения или backend/.env (если файл есть).
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
from app.services.billing_service import grant_lifetime  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Grant lifetime billing access by user email.")
    parser.add_argument("email", help="Registered user email (case-insensitive)")
    args = parser.parse_args()
    email = str(args.email).lower().strip()
    if not email:
        print("error: empty email", file=sys.stderr)
        return 2

    db = SessionLocal()
    try:
        user = db.query(User).filter(User.email == email).first()
        if user is None:
            print(f"error: no user with email {email!r}", file=sys.stderr)
            return 1
        grant_lifetime(db, str(user.id))
        db.commit()
        print(f"ok: lifetime granted to {email} (user_id={user.id})")
        return 0
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
