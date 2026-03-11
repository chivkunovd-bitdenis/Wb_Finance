"""
Celery-задачи: синк с WB и пересчёт P&L.
"""
from datetime import date

from celery_app.celery import celery_app
from sqlalchemy import delete

from app.db import SessionLocal
from app.models.user import User
from app.models.raw_sales import RawSale
from app.services.wb_client import fetch_sales


@celery_app.task(name="sync_sales")
def sync_sales(user_id: str, date_from: str, date_to: str) -> dict:
    """
    Синхронизация продаж с WB за период [date_from, date_to].
    date_from, date_to в формате YYYY-MM-DD.
    """
    db = SessionLocal()
    try:
        user = db.get(User, user_id)
        if not user:
            return {"ok": False, "error": "user_not_found"}
        if not user.wb_api_key or not user.wb_api_key.strip():
            return {"ok": False, "error": "no_wb_api_key"}

        rows = fetch_sales(date_from, date_to, user.wb_api_key.strip())
        if not rows:
            return {"ok": True, "count": 0}

        # Удалить старые записи этого пользователя за период, затем вставить новые (как в GAS updateDataBatch по датам)
        db.execute(
            delete(RawSale).where(
                RawSale.user_id == user_id,
                RawSale.date >= date.fromisoformat(date_from),
                RawSale.date <= date.fromisoformat(date_to),
            )
        )

        for r in rows:
            sale = RawSale(
                user_id=user_id,
                date=date.fromisoformat(r["date"]),
                nm_id=int(r["nm_id"]) if r.get("nm_id") is not None else 0,
                doc_type=r.get("doc_type") or None,
                retail_price=r.get("retail_price"),
                ppvz_for_pay=r.get("ppvz_for_pay"),
                delivery_rub=r.get("delivery_rub"),
                penalty=r.get("penalty"),
                additional_payment=r.get("additional_payment"),
                storage_fee=r.get("storage_fee"),
                quantity=int(r["quantity"]) if r.get("quantity") is not None else 1,
            )
            db.add(sale)

        db.commit()
        return {"ok": True, "count": len(rows)}
    except Exception as e:
        db.rollback()
        return {"ok": False, "error": str(e)}
    finally:
        db.close()
