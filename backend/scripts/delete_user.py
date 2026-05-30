"""Полностью удалить пользователя и все связанные данные. Запуск из каталога backend.

  cd backend && python scripts/delete_user.py "user@example.com" --dry-run
  cd backend && python scripts/delete_user.py "user@example.com" --yes

На проде (в контейнере api):

  docker compose exec api python scripts/delete_user.py "user@example.com" --dry-run
  docker compose exec api python scripts/delete_user.py "user@example.com" --yes

Удаляет строку users (CASCADE по большинству таблиц), предварительно чистит FK без CASCADE
(store_access_*, monthly_plan), затем файлы на диске (WB storage_state, product gen refs).
"""
from __future__ import annotations

import argparse
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

_env = ROOT_DIR / ".env"
if _env.is_file():
    try:
        from dotenv import load_dotenv

        load_dotenv(_env, override=False)
    except ImportError:
        pass

from sqlalchemy.orm import Session  # noqa: E402

from app.db import SessionLocal  # noqa: E402
from app.models.monthly_plan import MonthlyPlan  # noqa: E402
from app.models.store_access_audit_event import StoreAccessAuditEvent  # noqa: E402
from app.models.store_access_grant import StoreAccessGrant  # noqa: E402
from app.models.user import User  # noqa: E402
from app.services.ai_wb_access_service import (  # noqa: E402
    user_storage_state_path,
    user_wb_reconnect_flag_path,
)
from app.services.product_generation_assets import references_dir_root  # noqa: E402


@dataclass(frozen=True)
class DeleteUserPlan:
    email: str
    user_id: str
    store_access_grants: int
    store_access_audit_events: int
    monthly_plan_rows: int
    file_paths: tuple[Path, ...]


def _normalize_email(email: str) -> str:
    normalized = email.lower().strip()
    if not normalized:
        raise ValueError("empty email")
    return normalized


def _collect_file_paths(*, user_id: str) -> tuple[Path, ...]:
    paths: list[Path] = [
        user_storage_state_path(user_id=user_id),
        user_wb_reconnect_flag_path(user_id=user_id),
        references_dir_root() / user_id,
    ]
    return tuple(paths)


def build_delete_plan(*, db: Session, email: str) -> DeleteUserPlan:
    normalized = _normalize_email(email)
    user = db.query(User).filter(User.email == normalized).first()
    if user is None:
        raise LookupError(f"no user with email {normalized!r}")

    user_id = str(user.id)
    grants = (
        db.query(StoreAccessGrant)
        .filter(
            (StoreAccessGrant.store_owner_user_id == user_id)
            | (StoreAccessGrant.viewer_user_id == user_id)
        )
        .count()
    )
    audit = (
        db.query(StoreAccessAuditEvent)
        .filter(
            (StoreAccessAuditEvent.store_owner_user_id == user_id)
            | (StoreAccessAuditEvent.viewer_user_id == user_id)
            | (StoreAccessAuditEvent.actor_user_id == user_id)
        )
        .count()
    )
    monthly = db.query(MonthlyPlan).filter(MonthlyPlan.user_id == user_id).count()
    return DeleteUserPlan(
        email=str(user.email),
        user_id=user_id,
        store_access_grants=grants,
        store_access_audit_events=audit,
        monthly_plan_rows=monthly,
        file_paths=_collect_file_paths(user_id=user_id),
    )


def _delete_non_cascade_rows(*, db: Session, user_id: str) -> tuple[int, int, int]:
    grants_deleted = (
        db.query(StoreAccessGrant)
        .filter(
            (StoreAccessGrant.store_owner_user_id == user_id)
            | (StoreAccessGrant.viewer_user_id == user_id)
        )
        .delete(synchronize_session=False)
    )
    audit_deleted = (
        db.query(StoreAccessAuditEvent)
        .filter(
            (StoreAccessAuditEvent.store_owner_user_id == user_id)
            | (StoreAccessAuditEvent.viewer_user_id == user_id)
            | (StoreAccessAuditEvent.actor_user_id == user_id)
        )
        .delete(synchronize_session=False)
    )
    monthly_deleted = (
        db.query(MonthlyPlan).filter(MonthlyPlan.user_id == user_id).delete(synchronize_session=False)
    )
    return grants_deleted, audit_deleted, monthly_deleted


def _cleanup_files(*, file_paths: tuple[Path, ...]) -> list[str]:
    removed: list[str] = []
    for path in file_paths:
        if path.is_dir():
            if path.exists():
                shutil.rmtree(path)
                removed.append(f"dir:{path}")
        elif path.is_file():
            path.unlink()
            removed.append(f"file:{path}")
    return removed


def delete_user_by_email(*, db: Session, email: str, dry_run: bool) -> dict[str, object]:
    plan = build_delete_plan(db=db, email=email)
    if dry_run:
        existing_files = [str(p) for p in plan.file_paths if p.exists()]
        return {
            "dry_run": True,
            "email": plan.email,
            "user_id": plan.user_id,
            "would_delete": {
                "store_access_grants": plan.store_access_grants,
                "store_access_audit_events": plan.store_access_audit_events,
                "monthly_plan_rows": plan.monthly_plan_rows,
                "user_row": 1,
            },
            "files_on_disk": existing_files,
        }

    grants_deleted, audit_deleted, monthly_deleted = _delete_non_cascade_rows(db=db, user_id=plan.user_id)
    user_deleted = db.query(User).filter(User.id == plan.user_id).delete(synchronize_session=False)
    if user_deleted != 1:
        raise RuntimeError(f"expected to delete 1 user row, deleted {user_deleted}")

    db.commit()
    removed_files = _cleanup_files(file_paths=plan.file_paths)
    return {
        "dry_run": False,
        "email": plan.email,
        "user_id": plan.user_id,
        "deleted": {
            "store_access_grants": grants_deleted,
            "store_access_audit_events": audit_deleted,
            "monthly_plan_rows": monthly_deleted,
            "user_row": user_deleted,
        },
        "removed_files": removed_files,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Delete user and all related data by email.")
    parser.add_argument("email", help="Registered user email (case-insensitive)")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be deleted without making changes",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="Required for actual deletion (without --dry-run)",
    )
    args = parser.parse_args()

    dry_run = bool(args.dry_run)
    if not dry_run and not args.yes:
        print("error: pass --yes to confirm deletion, or use --dry-run first", file=sys.stderr)
        return 2

    db = SessionLocal()
    try:
        result = delete_user_by_email(db=db, email=str(args.email), dry_run=dry_run)
        print(result)
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
