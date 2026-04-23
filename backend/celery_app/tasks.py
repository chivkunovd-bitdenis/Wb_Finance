"""
Celery-задачи: синк с WB и пересчёт P&L.
"""
import logging
import random
import time
from datetime import date, timedelta
from typing import cast

import requests
from celery_app.celery import celery_app
from sqlalchemy import delete, func
from sqlalchemy.dialects.postgresql import insert

from app.db import SessionLocal
from app.core.feature_flags import is_daily_brief_enabled
from app.models.user import User
from app.models.article import Article
from app.models.raw_sales import RawSale
from app.models.raw_ads import RawAd
from app.models.funnel_daily import FunnelDaily
from app.models.funnel_backfill_state import FunnelBackfillState
from app.models.finance_backfill_state import FinanceBackfillState
from app.models.pnl_daily import PnlDaily
from app.models.operational_expense import OperationalExpense
from app.models.sku_daily import SkuDaily
from app.services.wb_client import (
    FUNNEL_CHUNK_SIZE,
    FUNNEL_SLEEP_SEC,
    fetch_ads,
    fetch_funnel,
    fetch_funnel_products_for_day_with_retry,
    fetch_sales,
)
from app.services.billing_service import collect_due_reminders
from app.services.daily_brief_service import generate_brief_text
from app.models.daily_brief import DailyBrief

logger = logging.getLogger(__name__)

TAX_RATE = 0.06
FUNNEL_YTD_DAYS_PER_RUN = 2
FUNNEL_YTD_CHAIN_COUNTDOWN_SEC = 25
FUNNEL_YTD_429_RETRY_BASE_SEC = 90
FUNNEL_YTD_429_RETRY_MAX_SEC = 1800
FUNNEL_YTD_429_RETRY_LIMIT = 12
FUNNEL_YTD_HTTP_RETRY_CODES = {429, 500, 502, 503, 504}

# WB иногда возвращает реальное окно "когда можно снова" через заголовки.
# В логах это видно как reset_sec=... (X-RateLimit-Reset). Значение может быть часами.
WB_MAX_RETRY_AFTER_SEC = 12 * 60 * 60  # safety cap: 12h

FUNNEL_BACKFILL_START_DATE = date(2026, 1, 1)


def _funnel_nm_ids(db, *, user_id: str, date_from: date, date_to: date) -> list[int]:
    """
    Список nm_id для запросов funnel.

    Основной источник: articles.
    Fallback: raw_sales/raw_ads — если articles ещё пустой, но данные уже есть.
    """
    nm_ids = sorted(
        {
            int(a.nm_id)
            for a in db.query(Article).filter(Article.user_id == user_id).all()
            if a.nm_id is not None and int(a.nm_id) > 0
        }
    )
    if nm_ids:
        return nm_ids

    sales_nm = {
        int(x[0])
        for x in db.query(RawSale.nm_id)
        .filter(
            RawSale.user_id == user_id,
            RawSale.date >= date_from,
            RawSale.date <= date_to,
        )
        .distinct()
        .all()
        if x and x[0] is not None and int(x[0]) > 0
    }
    ads_nm = {
        int(x[0])
        for x in db.query(RawAd.nm_id)
        .filter(
            RawAd.user_id == user_id,
            RawAd.date >= date_from,
            RawAd.date <= date_to,
            RawAd.nm_id.isnot(None),
        )
        .distinct()
        .all()
        if x and x[0] is not None and int(x[0]) > 0
    }
    return sorted(sales_nm | ads_nm)


def _funnel_insert_only(db, rows: list[dict], *, user_id: str) -> int:
    """
    Записать funnel rows в funnel_daily, не перетирая существующие данные.

    Идемпотентность и защита от гонок обеспечиваются unique constraint + ON CONFLICT DO NOTHING.
    """
    if not rows:
        return 0
    values: list[dict] = []
    for r in rows:
        try:
            d = date.fromisoformat(str(r["date"])[:10])
            nm_id = int(r["nm_id"])
        except Exception:
            continue
        values.append(
            {
                "user_id": user_id,
                "date": d,
                "nm_id": nm_id,
                "vendor_code": r.get("vendor_code"),
                "open_count": r.get("open_count", 0),
                "cart_count": r.get("cart_count", 0),
                "order_count": r.get("order_count", 0),
                "order_sum": r.get("order_sum"),
                "buyout_percent": r.get("buyout_percent"),
                "cr_to_cart": r.get("cr_to_cart"),
                "cr_to_order": r.get("cr_to_order"),
            }
        )
    if not values:
        return 0
    stmt = insert(FunnelDaily).values(values)
    # Важно: DO NOTHING не "лечит" витрину. Если один раз записались нули из-за гонки/частичного ответа,
    # последующие запуски не смогут исправить данные.
    #
    # Поэтому используем upsert, который:
    # - обновляет метрики, но не затирает большие значения меньшими/нулями (GREATEST)
    # - не перетирает vendor_code, если он уже заполнен
    excluded = stmt.excluded  # type: ignore[attr-defined]
    stmt = stmt.on_conflict_do_update(
        index_elements=["user_id", "date", "nm_id"],
        set_={
            # vendor_code: если в БД уже есть непустой — сохраняем, иначе берём новый
            "vendor_code": func.coalesce(
                func.nullif(func.btrim(FunnelDaily.vendor_code), ""),
                excluded.vendor_code,
            ),
            # counts/sums: не даём "откатиться" назад к 0
            "open_count": func.greatest(FunnelDaily.open_count, excluded.open_count),
            "cart_count": func.greatest(FunnelDaily.cart_count, excluded.cart_count),
            "order_count": func.greatest(FunnelDaily.order_count, excluded.order_count),
            "order_sum": func.greatest(
                func.coalesce(FunnelDaily.order_sum, 0),
                func.coalesce(excluded.order_sum, 0),
            ),
            # проценты: если новое None — оставляем старое, иначе берём новое
            "buyout_percent": func.coalesce(excluded.buyout_percent, FunnelDaily.buyout_percent),
            "cr_to_cart": func.coalesce(excluded.cr_to_cart, FunnelDaily.cr_to_cart),
            "cr_to_order": func.coalesce(excluded.cr_to_order, FunnelDaily.cr_to_order),
        },
    )
    res = db.execute(stmt)
    # rowcount is driver-dependent; fall back to len(values) if None.
    return int(res.rowcount) if getattr(res, "rowcount", None) is not None else len(values)


def _retry429_count(raw: str | None) -> int:
    if not raw or not raw.startswith("__retry_429__:"):
        return 0
    try:
        return int(raw.split(":", 1)[1])
    except Exception:
        return 0


def _retry429_delay_sec(retry_n: int) -> int:
    # Экспоненциальный backoff + небольшой jitter, чтобы не бить WB пачкой в один момент.
    core = min(FUNNEL_YTD_429_RETRY_MAX_SEC, FUNNEL_YTD_429_RETRY_BASE_SEC * (2 ** max(0, retry_n - 1)))
    return int(core + random.randint(0, 30))


def _retry_http_marker(code: int, retry_n: int) -> str:
    return f"__retry_http__:{code}:{retry_n}"


def _retry_http_parse(raw: str | None) -> tuple[int | None, int]:
    if not raw:
        return None, 0
    if raw.startswith("__retry_http__:"):
        parts = raw.split(":")
        if len(parts) == 4:
            try:
                return int(parts[2]), int(parts[3])
            except Exception:
                return None, 0
    if raw.startswith("__retry_429__:"):
        return 429, _retry429_count(raw)
    return None, 0


def _retry_http_delay_sec(code: int, retry_n: int) -> int:
    # Для 5xx пробуем чуть чаще, чем для 429, но всё равно с экспонентой.
    base = 45 if code >= 500 else FUNNEL_YTD_429_RETRY_BASE_SEC
    core = min(FUNNEL_YTD_429_RETRY_MAX_SEC, base * (2 ** max(0, retry_n - 1)))
    return int(core + random.randint(0, 30))


def _wb_retry_after_sec(resp: requests.Response) -> int | None:
    """
    Попробовать извлечь реальный retry-after из ответа WB.

    При глобальном лимите WB возвращает:
    - X-RateLimit-Reset: секунды до снятия ограничения (может быть большим)
    - Retry-After: иногда тоже присутствует
    """
    try:
        reset = resp.headers.get("X-RateLimit-Reset")
        if reset is not None:
            v = int(str(reset).strip())
            if v > 0:
                return v
    except Exception:
        pass
    try:
        ra = resp.headers.get("Retry-After")
        if ra is not None and str(ra).strip().isdigit():
            v2 = int(str(ra).strip())
            if v2 > 0:
                return v2
    except Exception:
        pass
    return None


def _retry_http_delay_with_headers(code: int, retry_n: int, resp: requests.Response | None) -> int:
    """
    Delay для retry HTTP с учётом заголовков WB (важно для 429).
    """
    fallback = _retry_http_delay_sec(code, retry_n)
    if code != 429 or resp is None:
        return fallback
    retry_after = _wb_retry_after_sec(resp)
    if retry_after is None:
        return fallback
    # Уважаем окно WB, но оставляем safety cap и небольшой jitter.
    capped = min(WB_MAX_RETRY_AFTER_SEC, retry_after)
    jitter = random.randint(0, 30)
    return int(max(fallback, capped + jitter))


def _build_desc_days_batch(cursor: date, year_start: date, limit: int) -> list[date]:
    """Собрать batch дат в обратном порядке: cursor, cursor-1, ..."""
    days_batch: list[date] = []
    d = cursor
    while d >= year_start and len(days_batch) < limit:
        days_batch.append(d)
        d -= timedelta(days=1)
    return days_batch


def _month_start(d: date) -> date:
    return date(d.year, d.month, 1)


def _build_desc_month_chunk(cursor: date, year_start: date) -> tuple[date, date]:
    """
    Ретроспективный чанк для финансов: берём от начала месяца cursor до cursor включительно.
    Например cursor=2026-03-26 -> (2026-03-01, 2026-03-26).
    """
    start = _month_start(cursor)
    if start < year_start:
        start = year_start
    return start, cursor


def _ensure_articles_from_raw(db, user_id: str, start: date, end: date, subject_by_nm: dict[int, str | None] | None = None) -> int:
    """
    Автозаполнение таблицы articles по nm_id из raw_sales/raw_ads за период.
    Это нужно, чтобы вкладка «Себестоимость» и воронка (sync_funnel) не были пустыми после первого синка.
    """
    # nm_id из продаж
    sale_nm_ids = {
        int(x[0])
        for x in db.query(RawSale.nm_id)
        .filter(
            RawSale.user_id == user_id,
            RawSale.date >= start,
            RawSale.date <= end,
        )
        .distinct()
        .all()
        if x and x[0] is not None and int(x[0]) > 0
    }
    # nm_id из рекламы
    ad_nm_ids = {
        int(x[0])
        for x in db.query(RawAd.nm_id)
        .filter(
            RawAd.user_id == user_id,
            RawAd.date >= start,
            RawAd.date <= end,
            RawAd.nm_id.isnot(None),
        )
        .distinct()
        .all()
        if x and x[0] is not None and int(x[0]) > 0
    }
    nm_ids = sale_nm_ids | ad_nm_ids
    if not nm_ids:
        return 0

    existing = {
        int(x[0])
        for x in db.query(Article.nm_id)
        .filter(Article.user_id == user_id, Article.nm_id.in_(list(nm_ids)))
        .all()
    }
    missing = [nm for nm in nm_ids if nm not in existing]
    for nm in missing:
        db.add(
            Article(
                user_id=user_id,
                nm_id=nm,
                subject_name=(subject_by_nm or {}).get(nm) or None,
            )
        )

    # Если subject_name уже есть в исходных данных и статья уже существовала —
    # заполним только пустые значения.
    if subject_by_nm:
        for nm, subject in subject_by_nm.items():
            if not subject:
                continue
            art = (
                db.query(Article)
                .filter(Article.user_id == user_id, Article.nm_id == nm)
                .first()
            )
            if art and not art.subject_name:
                art.subject_name = subject
    return len(missing)


@celery_app.task(name="sync_sales")
def sync_sales(user_id: str, date_from: str, date_to: str, retry_raw: str | None = None) -> dict:
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

        try:
            rows = fetch_sales(date_from, date_to, user.wb_api_key.strip())
        except requests.HTTPError as exc:
            code = exc.response.status_code if exc.response is not None else None
            if code in FUNNEL_YTD_HTTP_RETRY_CODES:
                prev_code, prev_n = _retry_http_parse(retry_raw)
                retry_n = (prev_n + 1) if prev_code == code else 1
                if retry_n <= FUNNEL_YTD_429_RETRY_LIMIT:
                    delay = _retry_http_delay_with_headers(int(code), retry_n, exc.response)
                    sync_sales.apply_async(
                        kwargs={
                            "user_id": user_id,
                            "date_from": date_from,
                            "date_to": date_to,
                            "retry_raw": _retry_http_marker(int(code), retry_n),
                        },
                        countdown=delay,
                    )
                    return {
                        "ok": False,
                        "error": "wb_retry_scheduled",
                        "http_code": int(code),
                        "retry": retry_n,
                        "delay_sec": delay,
                    }
                return {"ok": False, "error": "wb_retry_limit", "http_code": int(code)}
            raise
        if not rows:
            return {"ok": True, "count": 0}

        subject_by_nm = {}
        for r in rows:
            nm = r.get("nm_id")
            subject = r.get("subject_name")
            if nm is None or subject is None:
                continue
            try:
                nm_i = int(nm)
            except Exception:
                continue
            if nm_i > 0:
                subject_by_nm[nm_i] = subject

        # Удалить старые записи этого пользователя за период, затем вставить новые (как в GAS updateDataBatch по датам)
        db.execute(
            delete(RawSale).where(
                RawSale.user_id == user_id,
                RawSale.date >= date.fromisoformat(date_from),
                RawSale.date <= date.fromisoformat(date_to),
            )
        )

        inserted = 0
        for r in rows:
            nm_raw = r.get("nm_id")
            try:
                nm_id = int(nm_raw) if nm_raw is not None else 0
            except Exception:
                nm_id = 0
            # В WB иногда встречаются строки без nm_id (или с 0).
            # Их НУЖНО хранить для общего P&L (storage/logistics/penalties за период),
            # но НЕЛЬЗЯ атрибутировать к конкретному артикулу. Это решается на уровне sku_daily:
            # nm_id<=0 игнорируются при построении витрины по артикулам.

            sale = RawSale(
                user_id=user_id,
                date=date.fromisoformat(r["date"]),
                nm_id=nm_id,
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
            inserted += 1

        db.commit()
        # гарантируем, что articles заполнится для вкладки «Себестоимость» и sync_funnel
        try:
            created = _ensure_articles_from_raw(
                db,
                user_id,
                date.fromisoformat(date_from),
                date.fromisoformat(date_to),
                subject_by_nm=subject_by_nm,
            )
            if created:
                db.commit()
        except Exception:
            db.rollback()
        recalculate_pnl.delay(user_id, date_from, date_to)
        recalculate_sku_daily.delay(user_id, date_from, date_to)
        return {"ok": True, "count": inserted}
    except Exception as e:
        db.rollback()
        return {"ok": False, "error": str(e)}
    finally:
        db.close()


@celery_app.task(name="sync_ads")
def sync_ads(user_id: str, date_from: str, date_to: str) -> dict:
    """
    Синхронизация рекламы с WB за период: adv/v1/upd + детали кампаний, расход по nm_id поровну.
    """
    db = SessionLocal()
    try:
        user = db.get(User, user_id)
        if not user:
            return {"ok": False, "error": "user_not_found"}
        if not user.wb_api_key or not user.wb_api_key.strip():
            return {"ok": False, "error": "no_wb_api_key"}

        rows = fetch_ads(date_from, date_to, user.wb_api_key.strip())
        if not rows:
            return {"ok": True, "count": 0}

        db.execute(
            delete(RawAd).where(
                RawAd.user_id == user_id,
                RawAd.date >= date.fromisoformat(date_from),
                RawAd.date <= date.fromisoformat(date_to),
            )
        )

        for r in rows:
            ad = RawAd(
                user_id=user_id,
                date=date.fromisoformat(r["date"]),
                nm_id=int(r["nm_id"]) if r.get("nm_id") is not None else None,
                campaign_id=int(r["campaign_id"]) if r.get("campaign_id") is not None else None,
                spend=r.get("spend"),
            )
            db.add(ad)

        db.commit()
        # гарантируем, что articles заполнится для вкладки «Себестоимость» и sync_funnel
        try:
            created = _ensure_articles_from_raw(
                db,
                user_id,
                date.fromisoformat(date_from),
                date.fromisoformat(date_to),
                subject_by_nm=None,
            )
            if created:
                db.commit()
        except Exception:
            db.rollback()
        recalculate_pnl.delay(user_id, date_from, date_to)
        recalculate_sku_daily.delay(user_id, date_from, date_to)
        return {"ok": True, "count": len(rows)}
    except Exception as e:
        db.rollback()
        return {"ok": False, "error": str(e)}
    finally:
        db.close()


def _default_funnel_dates() -> tuple[str, str]:
    """Окно «последние 7 дней»: вчера − 6 дней .. вчера (как в GAS)."""
    end = date.today() - timedelta(days=1)
    start = end - timedelta(days=6)
    return start.isoformat(), end.isoformat()


@celery_app.task(name="after_initial_sync_enqueue_funnel")
def after_initial_sync_enqueue_funnel(_results: list[object], user_id: str) -> None:
    """
    Колбэк chord после пары sync_sales + sync_ads из POST /sync/initial.
    Воронка требует строк в articles; они появляются в этих задачах — нельзя ставить sync_funnel параллельно с ними.
    """
    sync_funnel.delay(user_id, None, None)


@celery_app.task(name="after_period_sync_enqueue_funnel")
def after_period_sync_enqueue_funnel(
    _results: list[object],
    user_id: str,
    date_from: str,
    date_to: str,
) -> None:
    """
    Колбэк chord для синка произвольного периода:
    сначала sync_sales + sync_ads, затем sync_funnel за тот же период.
    """
    sync_funnel.delay(user_id, date_from, date_to)


@celery_app.task(name="sync_funnel")
def sync_funnel(
    user_id: str,
    date_from: str | None = None,
    date_to: str | None = None,
    retry_raw: str | None = None,
) -> dict:
    """
    Синхронизация воронки продаж с WB за период.
    nm_ids берутся из таблицы articles пользователя; чанки по 20, пауза 25 сек.
    Если date_from/date_to не переданы — используется окно «последние 7 дней».
    """
    db = SessionLocal()
    try:
        user = db.get(User, user_id)
        if not user:
            return {"ok": False, "error": "user_not_found"}
        if not user.wb_api_key or not user.wb_api_key.strip():
            return {"ok": False, "error": "no_wb_api_key"}

        if date_from and date_to:
            start_s, end_s = date_from, date_to
        else:
            start_s, end_s = _default_funnel_dates()

        start_d = date.fromisoformat(start_s)
        end_d = date.fromisoformat(end_s)
        nm_ids = _funnel_nm_ids(db, user_id=user_id, date_from=start_d, date_to=end_d)
        if not nm_ids:
            return {"ok": True, "count": 0}

        n_chunks = max(1, (len(nm_ids) + FUNNEL_CHUNK_SIZE - 1) // FUNNEL_CHUNK_SIZE)
        logger.info(
            "funnel_sync op=sync_funnel user_id=%s period=%s..%s nm_ids=%s chunks=%s",
            user_id,
            start_s,
            end_s,
            len(nm_ids),
            n_chunks,
        )
        try:
            rows = fetch_funnel(
                start_s,
                end_s,
                nm_ids,
                user.wb_api_key.strip(),
                log_context=f"user_id={user_id} op=sync_funnel",
                sleep_on_retry=False,
            )
        except requests.HTTPError as exc:
            code = exc.response.status_code if exc.response is not None else None
            if code in FUNNEL_YTD_HTTP_RETRY_CODES:
                prev_code, prev_n = _retry_http_parse(retry_raw)
                retry_n = (prev_n + 1) if prev_code == code else 1
                if retry_n <= FUNNEL_YTD_429_RETRY_LIMIT:
                    delay = _retry_http_delay_with_headers(int(code), retry_n, exc.response)
                    sync_funnel.apply_async(
                        kwargs={
                            "user_id": user_id,
                            "date_from": start_s,
                            "date_to": end_s,
                            "retry_raw": _retry_http_marker(int(code), retry_n),
                        },
                        countdown=delay,
                    )
                    return {
                        "ok": False,
                        "error": "wb_retry_scheduled",
                        "http_code": int(code),
                        "retry": retry_n,
                        "delay_sec": delay,
                    }
                return {"ok": False, "error": "wb_retry_limit", "http_code": int(code)}
            raise
        if not rows:
            # Важно: пустой ответ WB не должен стирать уже накопленную витрину.
            return {"ok": True, "count": 0}

        # Если Wildberries вернул subject_name в ответе funnel — сохраним его в articles.
        subject_by_nm = {}
        for r in rows:
            nm = r.get("nm_id")
            subject = r.get("subject_name")
            if nm is None or subject is None:
                continue
            try:
                nm_i = int(nm)
            except Exception:
                continue
            if nm_i > 0 and subject:
                # Берём первое непустое значение.
                if nm_i not in subject_by_nm:
                    subject_by_nm[nm_i] = subject

        inserted = _funnel_insert_only(db, rows, user_id=user_id)
        logger.info(
            "funnel_sync op=sync_funnel user_id=%s period=%s..%s result=ok rows=%s inserted=%s",
            user_id,
            start_s,
            end_s,
            len(rows),
            inserted,
        )

        if subject_by_nm:
            for nm, subject in subject_by_nm.items():
                if not subject:
                    continue
                art = (
                    db.query(Article)
                    .filter(Article.user_id == user_id, Article.nm_id == nm)
                    .first()
                )
                if art and not art.subject_name:
                    art.subject_name = subject
        db.commit()
        return {"ok": True, "count": inserted}
    except Exception as e:
        db.rollback()
        p0 = locals().get("start_s") or date_from or "?"
        p1 = locals().get("end_s") or date_to or "?"
        logger.exception(
            "funnel_sync op=sync_funnel user_id=%s period=%s..%s result=error err=%s",
            user_id,
            p0,
            p1,
            type(e).__name__,
        )
        return {"ok": False, "error": str(e)}
    finally:
        db.close()


@celery_app.task(name="sync_funnel_ytd_step")
def sync_funnel_ytd_step(user_id: str, year: int | None = None) -> dict:
    """
    Фоновая догрузка воронки с начала календарного года через sales-funnel/products (по одному дню).
    Приоритет — свежие даты: идём от вчера назад по дням.
    За один запуск обрабатывает не более FUNNEL_YTD_DAYS_PER_RUN дней, затем ставит себя снова в очередь.
    """
    db = SessionLocal()
    # Фича: ретроспективно идём строго до 2026-01-01.
    y = 2026 if year is None else int(year)
    try:
        user = db.get(User, user_id)
        if not user:
            return {"ok": False, "error": "user_not_found"}
        if not user.wb_api_key or not user.wb_api_key.strip():
            return {"ok": False, "error": "no_wb_api_key"}

        key = user.wb_api_key.strip()
        year_start = FUNNEL_BACKFILL_START_DATE
        year_end_cap = date(2026, 12, 31)
        yesterday = date.today() - timedelta(days=1)
        through = yesterday if yesterday <= year_end_cap else year_end_cap
        if through < year_start:
            return {"ok": True, "message": "nothing_to_backfill", "year": y}

        state = (
            db.query(FunnelBackfillState)
            .filter(
                FunnelBackfillState.user_id == user_id,
                FunnelBackfillState.calendar_year == y,
            )
            .first()
        )
        if not state:
            state = FunnelBackfillState(
                user_id=user_id,
                calendar_year=y,
                status="idle",
            )
            db.add(state)
            db.commit()
            db.refresh(state)

        nm_ids = _funnel_nm_ids(db, user_id=user_id, date_from=year_start, date_to=through)

        # Раньше при пустых articles задача «прокручивала» даты и ставила complete без строк в funnel_daily.
        if (
            state.status == "complete"
            and state.last_completed_date
            and state.last_completed_date <= year_start
            and nm_ids
        ):
            n_rows = (
                db.query(func.count(FunnelDaily.id))
                .filter(
                    FunnelDaily.user_id == user_id,
                    FunnelDaily.date >= year_start,
                    FunnelDaily.date <= through,
                )
                .scalar()
                or 0
            )
            if n_rows == 0:
                state.status = "idle"
                state.last_completed_date = None
                state.error_message = None
                db.add(state)
                db.commit()

        if state.status == "complete" and state.last_completed_date and state.last_completed_date <= year_start:
            return {"ok": True, "message": "already_complete", "year": y}

        if not nm_ids:
            state.status = "idle"
            if state.error_message != "__retry_scheduled__":
                state.error_message = "__retry_scheduled__"
                db.add(state)
                db.commit()
                sync_funnel_ytd_step.apply_async(
                    args=[user_id, y],
                    countdown=90,
                )
            else:
                state.error_message = None
                db.add(state)
                db.commit()
            return {"ok": True, "message": "no_articles_yet", "year": y}

        state.status = "running"
        db.add(state)
        db.commit()

        # 1) Weekly fill: если за вчера нет данных funnel_daily — дергаем history-метод за 7 дней
        # и дополняем витрину без перетирания.
        has_yesterday = (
            db.query(FunnelDaily.id)
            .filter(FunnelDaily.user_id == user_id, FunnelDaily.date == through)
            .first()
            is not None
        )
        if not has_yesterday:
            week_start = through - timedelta(days=6)
            if week_start < year_start:
                week_start = year_start
            ws, we = week_start.isoformat(), through.isoformat()
            logger.info(
                "funnel_sync op=sync_funnel_ytd_step phase=weekly_history user_id=%s week=%s..%s nm_ids=%s",
                user_id,
                ws,
                we,
                len(nm_ids),
            )
            week_rows = fetch_funnel(
                ws,
                we,
                nm_ids,
                key,
                log_context=f"user_id={user_id} op=ytd_weekly",
                sleep_on_retry=False,
            )
            # Даже если WB вернул пусто — ничего не стираем; просто продолжаем daily-backfill.
            _funnel_insert_only(db, week_rows, user_id=user_id)
            logger.info(
                "funnel_sync op=sync_funnel_ytd_step phase=weekly_history user_id=%s week=%s..%s rows=%s",
                user_id,
                ws,
                we,
                len(week_rows),
            )
            # Вставки funnel — это вход в sku_daily: пересчёт запустим после обработки батча дней ниже.
            db.commit()

        cursor = through if state.last_completed_date is None else (state.last_completed_date - timedelta(days=1))
        if cursor < year_start:
            state.status = "complete"
            state.error_message = None
            db.add(state)
            db.commit()
            return {"ok": True, "message": "complete", "year": y}

        days_batch = _build_desc_days_batch(
            cursor=cursor,
            year_start=year_start,
            limit=FUNNEL_YTD_DAYS_PER_RUN,
        )
        if not days_batch:
            state.status = "complete"
            state.error_message = None
            db.add(state)
            db.commit()
            return {"ok": True, "message": "complete", "year": y}

        for day in days_batch:
            day_s = day.isoformat()
            n_chunks = max(1, (len(nm_ids) + FUNNEL_CHUNK_SIZE - 1) // FUNNEL_CHUNK_SIZE)
            logger.info(
                "funnel_sync op=sync_funnel_ytd_step phase=daily_products user_id=%s day=%s chunks_total=%s",
                user_id,
                day_s,
                n_chunks,
            )

            merged: list[dict] = []
            for i in range(0, len(nm_ids), FUNNEL_CHUNK_SIZE):
                chunk = nm_ids[i : i + FUNNEL_CHUNK_SIZE]
                chunk_n = i // FUNNEL_CHUNK_SIZE + 1
                rows = fetch_funnel_products_for_day_with_retry(
                    day_s,
                    chunk,
                    key,
                    log_context=f"user_id={user_id} op=ytd_daily",
                )
                merged.extend(rows)
                logger.info(
                    "funnel_sync op=sync_funnel_ytd_step phase=daily_products user_id=%s day=%s "
                    "chunk=%s/%s nm_in_chunk=%s rows=%s",
                    user_id,
                    day_s,
                    chunk_n,
                    n_chunks,
                    len(chunk),
                    len(rows),
                )
                if i + FUNNEL_CHUNK_SIZE < len(nm_ids):
                    time.sleep(FUNNEL_SLEEP_SEC)

            subject_by_nm: dict[int, str] = {}
            for r in merged:
                subj = r.get("subject_name")
                try:
                    nm_i = int(r["nm_id"])
                except Exception:
                    continue
                if subj and nm_i not in subject_by_nm:
                    subject_by_nm[nm_i] = subj

            _funnel_insert_only(db, merged, user_id=user_id)

            for nm, subj in subject_by_nm.items():
                art = (
                    db.query(Article)
                    .filter(Article.user_id == user_id, Article.nm_id == nm)
                    .first()
                )
                if art and not art.subject_name:
                    art.subject_name = subj

            # День успешно обработан: все чанки получили 200. Даже если заказы нулевые — это валидно.
            state.last_completed_date = day
            state.error_message = None
            db.add(state)
            db.commit()
            logger.info(
                "funnel_sync op=sync_funnel_ytd_step phase=daily_products user_id=%s day=%s closed=1",
                user_id,
                day_s,
            )

        batch_first = days_batch[0].isoformat()
        batch_last = days_batch[-1].isoformat()
        recalculate_sku_daily.delay(user_id, batch_first, batch_last)

        last_d = state.last_completed_date
        if last_d is not None and last_d <= year_start:
            state.status = "complete"
            state.error_message = None
            db.add(state)
            db.commit()
        else:
            sync_funnel_ytd_step.apply_async(
                args=[user_id, y],
                countdown=FUNNEL_YTD_CHAIN_COUNTDOWN_SEC,
            )

        return {
            "ok": True,
            "year": y,
            "days_processed": len(days_batch),
            "last_completed": state.last_completed_date.isoformat() if state.last_completed_date else None,
        }
    except Exception as e:
        db.rollback()
        st = (
            db.query(FunnelBackfillState)
            .filter(
                FunnelBackfillState.user_id == user_id,
                FunnelBackfillState.calendar_year == y,
            )
            .first()
        )
        code = e.response.status_code if isinstance(e, requests.HTTPError) and e.response is not None else None
        if st and code in FUNNEL_YTD_HTTP_RETRY_CODES:
            prev_code, prev_n = _retry_http_parse(st.error_message)
            retry_n = (prev_n + 1) if prev_code == code else 1
            if retry_n <= FUNNEL_YTD_429_RETRY_LIMIT:
                st.status = "running"
                st.error_message = _retry_http_marker(code, retry_n)
                db.add(st)
                db.commit()
                delay = _retry_http_delay_with_headers(code, retry_n, e.response if isinstance(e, requests.HTTPError) else None)
                sync_funnel_ytd_step.apply_async(args=[user_id, y], countdown=delay)
                return {
                    "ok": False,
                    "error": "wb_retry_scheduled",
                    "http_code": code,
                    "retry": retry_n,
                    "delay_sec": delay,
                }
            st.status = "error"
            st.error_message = f"{code} retry limit reached ({retry_n - 1}); last={str(e)[:1600]}"
            db.add(st)
            db.commit()
            return {"ok": False, "error": "wb_retry_limit", "http_code": code}
        if st:
            st.status = "error"
            st.error_message = str(e)[:1900]
            db.add(st)
            db.commit()
        return {"ok": False, "error": str(e)}
    finally:
        db.close()


@celery_app.task(name="sync_finance_backfill_step")
def sync_finance_backfill_step(user_id: str, year: int) -> dict:
    """
    Надёжная фоновая догрузка финансов за год (sales+ads → pnl/sku) ретроспективно:
    сначала свежие числа, затем углубляемся назад.

    Делает один месячный чанк за запуск и ставит себя снова в очередь.
    2025 стартует автоматически только после завершения 2026.
    """
    db = SessionLocal()
    y = int(year)
    try:
        user = db.get(User, user_id)
        if not user:
            return {"ok": False, "error": "user_not_found"}
        if not user.wb_api_key or not user.wb_api_key.strip():
            return {"ok": False, "error": "no_wb_api_key"}

        year_start = date(y, 1, 1)
        year_end_cap = date(y, 12, 31)
        yesterday = date.today() - timedelta(days=1)
        through = yesterday if yesterday <= year_end_cap else year_end_cap
        if through < year_start:
            return {"ok": True, "message": "nothing_to_backfill", "year": y}

        state = (
            db.query(FinanceBackfillState)
            .filter(
                FinanceBackfillState.user_id == user_id,
                FinanceBackfillState.calendar_year == y,
            )
            .first()
        )
        if not state:
            state = FinanceBackfillState(user_id=user_id, calendar_year=y, status="idle")
            db.add(state)
            db.commit()
            db.refresh(state)

        if state.status == "complete" and state.last_completed_date and state.last_completed_date <= year_start:
            return {"ok": True, "message": "already_complete", "year": y}

        state.status = "running"
        db.add(state)
        db.commit()

        cursor = through if state.last_completed_date is None else (state.last_completed_date - timedelta(days=1))
        if cursor < year_start:
            state.status = "complete"
            state.error_message = None
            db.add(state)
            db.commit()
            if y == 2026:
                sync_finance_backfill_step.delay(user_id, 2025)
            return {"ok": True, "message": "complete", "year": y}

        df_d, dt_d = _build_desc_month_chunk(cursor=cursor, year_start=year_start)
        df = df_d.isoformat()
        dt = dt_d.isoformat()

        try:
            # атомарно по смыслу: sales + ads + пересчёт витрин за чанк
            r_sales = sync_sales(user_id, df, dt)
            r_ads = sync_ads(user_id, df, dt)
            recalculate_pnl(user_id, df, dt)
            recalculate_sku_daily(user_id, df, dt)
        except requests.HTTPError as exc:
            code = exc.response.status_code if exc.response is not None else None
            if code in FUNNEL_YTD_HTTP_RETRY_CODES:
                prev_code, prev_n = _retry_http_parse(state.error_message)
                retry_n = (prev_n + 1) if prev_code == code else 1
                if retry_n <= FUNNEL_YTD_429_RETRY_LIMIT:
                    state.status = "running"
                    state.error_message = _retry_http_marker(int(code), retry_n)
                    db.add(state)
                    db.commit()
                    delay = _retry_http_delay_with_headers(int(code), retry_n, exc.response)
                    sync_finance_backfill_step.apply_async(args=[user_id, y], countdown=delay)
                    return {
                        "ok": False,
                        "error": "wb_retry_scheduled",
                        "http_code": code,
                        "retry": retry_n,
                        "delay_sec": delay,
                    }
            raise

        state.last_completed_date = dt_d
        state.error_message = None
        db.add(state)
        db.commit()

        sync_finance_backfill_step.apply_async(args=[user_id, y], countdown=20)
        return {
            "ok": True,
            "year": y,
            "chunk": {"date_from": df, "date_to": dt},
            "sales": r_sales,
            "ads": r_ads,
            "last_completed": state.last_completed_date.isoformat() if state.last_completed_date else None,
        }
    except Exception as e:
        db.rollback()
        st = (
            db.query(FinanceBackfillState)
            .filter(
                FinanceBackfillState.user_id == user_id,
                FinanceBackfillState.calendar_year == y,
            )
            .first()
        )
        if st:
            st.status = "error"
            st.error_message = str(e)[:1900]
            db.add(st)
            db.commit()
        return {"ok": False, "error": str(e)}
    finally:
        db.close()


@celery_app.task(name="recalculate_pnl")
def recalculate_pnl(user_id: str, date_from: str, date_to: str) -> dict:
    """
    Пересчитать P&L по дням за период: raw_sales + raw_ads + articles → pnl_daily.
    Логика как в GAS updatePnlForPeriod. Вызывать после sync_sales/sync_ads.
    """
    db = SessionLocal()
    try:
        user = db.get(User, user_id)
        if not user:
            return {"ok": False, "error": "user_not_found"}

        start = date.fromisoformat(date_from)
        end = date.fromisoformat(date_to)
        cost_map = {a.nm_id: (a.cost_price or 0) for a in db.query(Article).filter(Article.user_id == user_id).all()}

        expenses_by_date: dict[date, float] = {}
        expenses_rows = db.query(OperationalExpense).filter(
            OperationalExpense.user_id == user_id,
            OperationalExpense.date >= start,
            OperationalExpense.date <= end,
        ).all()
        for e in expenses_rows:
            # Возможны несколько записей за день — суммируем.
            exp_date = cast(date, e.date)
            expenses_by_date[exp_date] = expenses_by_date.get(exp_date, 0.0) + float(e.amount or 0)

        # Собираем даты из продаж и рекламы
        dates_set = set()
        for r in db.query(RawSale).filter(
            RawSale.user_id == user_id,
            RawSale.date >= start,
            RawSale.date <= end,
        ):
            dates_set.add(r.date)
        for r in db.query(RawAd).filter(
            RawAd.user_id == user_id,
            RawAd.date >= start,
            RawAd.date <= end,
        ):
            dates_set.add(r.date)

        # Также включаем дни, где были только операционные расходы.
        for ed in expenses_by_date.keys():
            dates_set.add(ed)

        window_dates = sorted(dates_set)

        user_tax_rate = float(user.tax_rate) if user.tax_rate is not None else TAX_RATE

        pnl_rows = []
        for d in window_dates:
            rev = 0.0
            ppvz = 0.0
            logistics = 0.0
            penalties = 0.0
            storage = 0.0
            ads_spend = 0.0
            cogs = 0.0
            op_expenses = float(expenses_by_date.get(cast(date, d)) or 0.0)
            for r in db.query(RawSale).filter(
                RawSale.user_id == user_id,
                RawSale.date == d,
            ):
                qty = r.quantity or 1
                cost = float(cost_map.get(r.nm_id) or 0)
                s = float(r.retail_price or 0)
                p = float(r.ppvz_for_pay or 0)
                is_sale = (r.doc_type or "").strip().lower() == "продажа"
                is_return = (r.doc_type or "").strip().lower() == "возврат"
                if is_sale:
                    rev += s
                    ppvz += p
                    cogs += cost * qty
                elif is_return:
                    rev -= s
                    ppvz -= p
                    cogs -= cost * qty
                logistics += float(r.delivery_rub or 0)
                penalties += float(r.penalty or 0) + float(r.additional_payment or 0)
                storage += float(r.storage_fee or 0)
            for r in db.query(RawAd).filter(
                RawAd.user_id == user_id,
                RawAd.date == d,
            ):
                ads_spend += float(r.spend or 0)

            comm = rev - ppvz
            tax = rev * user_tax_rate if rev > 0 else 0
            margin = rev - comm - logistics - penalties - storage - ads_spend - cogs - tax - op_expenses
            pnl_rows.append((d, rev, comm, logistics, penalties, storage, ads_spend, cogs, tax, op_expenses, margin))

        db.execute(
            delete(PnlDaily).where(
                PnlDaily.user_id == user_id,
                PnlDaily.date >= start,
                PnlDaily.date <= end,
            )
        )
        for d, rev, comm, logistics, penalties, storage, ads_spend, cogs, tax, op_expenses, margin in pnl_rows:
            db.add(PnlDaily(
                user_id=user_id,
                date=d,
                revenue=round(rev, 2),
                commission=round(comm, 2),
                logistics=round(logistics, 2),
                penalties=round(penalties, 2),
                storage=round(storage, 2),
                ads_spend=round(ads_spend, 2),
                cogs=round(cogs, 2),
                tax=round(tax, 2),
                operation_expenses=round(op_expenses, 2),
                margin=round(margin, 2),
            ))
        db.commit()
        return {"ok": True, "count": len(pnl_rows)}
    except Exception as e:
        db.rollback()
        return {"ok": False, "error": str(e)}
    finally:
        db.close()


@celery_app.task(name="recalculate_sku_daily")
def recalculate_sku_daily(user_id: str, date_from: str, date_to: str) -> dict:
    """
    Заполнить витрину sku_daily за период: агрегация из raw_sales, raw_ads, funnel_daily, articles.
    Логика как в GAS apiGetTimeSeriesPayload. Вызывать после sync_sales/sync_ads (и при смене себестоимости).
    """
    db = SessionLocal()
    try:
        user = db.get(User, user_id)
        if not user:
            return {"ok": False, "error": "user_not_found"}

        start = date.fromisoformat(date_from)
        end = date.fromisoformat(date_to)

        cost_map = {a.nm_id: (a.cost_price or 0) for a in db.query(Article).filter(Article.user_id == user_id).all()}
        user_tax_rate = float(user.tax_rate) if user.tax_rate is not None else TAX_RATE

        # (date, nm_id) -> поля витрины
        sku: dict[tuple[date, int], dict] = {}

        def get_row(d: date, nm: int) -> dict:
            key = (d, nm)
            if key not in sku:
                sku[key] = {
                    "revenue": 0, "commission": 0, "logistics": 0, "penalties": 0, "storage": 0,
                    "ads_spend": 0, "cogs": 0, "open_count": 0, "cart_count": 0, "order_count": 0, "order_sum": 0,
                }
            return sku[key]

        for r in db.query(RawSale).filter(
            RawSale.user_id == user_id,
            RawSale.date >= start,
            RawSale.date <= end,
        ):
            # Safety net: если в БД уже есть "битые" строки (nm_id<=0),
            # не даём им создавать псевдо-артикул в витрине и портить вкладку "Артикулы".
            if int(r.nm_id) <= 0:
                continue
            row = get_row(r.date, r.nm_id)
            qty = r.quantity or 1
            s = float(r.retail_price or 0)
            p = float(r.ppvz_for_pay or 0)
            cost = float(cost_map.get(r.nm_id) or 0)
            is_sale = (r.doc_type or "").strip().lower() == "продажа"
            is_return = (r.doc_type or "").strip().lower() == "возврат"
            if is_sale:
                row["revenue"] += s
                row["commission"] += s - p
                row["cogs"] += cost * qty
            elif is_return:
                row["revenue"] -= s
                row["commission"] -= s - p
                row["cogs"] -= cost * qty
            row["logistics"] += float(r.delivery_rub or 0)
            row["penalties"] += float(r.penalty or 0) + float(r.additional_payment or 0)
            row["storage"] += float(r.storage_fee or 0)

        for r in db.query(RawAd).filter(
            RawAd.user_id == user_id,
            RawAd.date >= start,
            RawAd.date <= end,
        ):
            if r.nm_id is None:
                continue
            row = get_row(r.date, r.nm_id)
            row["ads_spend"] += float(r.spend or 0)

        for r in db.query(FunnelDaily).filter(
            FunnelDaily.user_id == user_id,
            FunnelDaily.date >= start,
            FunnelDaily.date <= end,
        ):
            row = get_row(r.date, r.nm_id)
            row["open_count"] = (row["open_count"] or 0) + (r.open_count or 0)
            row["cart_count"] = (row["cart_count"] or 0) + (r.cart_count or 0)
            row["order_count"] = (row["order_count"] or 0) + (r.order_count or 0)
            row["order_sum"] = (row["order_sum"] or 0) + float(r.order_sum or 0)

        db.execute(
            delete(SkuDaily).where(
                SkuDaily.user_id == user_id,
                SkuDaily.date >= start,
                SkuDaily.date <= end,
            )
        )
        for (d, nm), row in sku.items():
            rev = row["revenue"]
            tax = rev * user_tax_rate if rev > 0 else 0
            margin = rev - row["commission"] - row["logistics"] - row["penalties"] - row["storage"] - row["ads_spend"] - row["cogs"] - tax
            db.add(SkuDaily(
                user_id=user_id,
                date=d,
                nm_id=nm,
                revenue=round(rev, 2),
                commission=round(row["commission"], 2),
                logistics=round(row["logistics"], 2),
                penalties=round(row["penalties"], 2),
                storage=round(row["storage"], 2),
                ads_spend=round(row["ads_spend"], 2),
                cogs=round(row["cogs"], 2),
                tax=round(tax, 2),
                margin=round(margin, 2),
                open_count=row["open_count"] or 0,
                cart_count=row["cart_count"] or 0,
                order_count=row["order_count"] or 0,
                order_sum=round(row["order_sum"], 2) if row["order_sum"] else None,
            ))
        db.commit()
        return {"ok": True, "count": len(sku)}
    except Exception as e:
        db.rollback()
        return {"ok": False, "error": str(e)}
    finally:
        db.close()


@celery_app.task(name="billing_send_reminders")
def billing_send_reminders() -> dict:
    db = SessionLocal()
    try:
        created = collect_due_reminders(db)
        return {"ok": True, "created": created}
    except Exception as e:
        db.rollback()
        return {"ok": False, "error": str(e)}
    finally:
        db.close()


@celery_app.task(name="generate_daily_brief", max_retries=1)
def generate_daily_brief(user_id: str, date_for_str: str | None = None) -> dict:
    """
    Сгенерировать ежедневную AI-сводку для пользователя.

    Алгоритм:
    1. Найти или создать запись DailyBrief со статусом pending.
    2. Если уже ready — пропустить (идемпотентность).
    3. Если уже generating — пропустить (защита от дублирования).
    4. Поставить статус generating.
    5. Вызвать generate_brief_text (pre-computation + LLM).
    6. Сохранить текст, статус ready, generated_at.
    7. При ошибке — статус error + error_message.
    """
    from datetime import datetime, timezone

    if not is_daily_brief_enabled():
        return {"ok": True, "message": "disabled"}

    db = SessionLocal()
    try:
        d_for = (
            date.fromisoformat(date_for_str)
            if date_for_str
            else date.today() - timedelta(days=1)
        )

        brief = (
            db.query(DailyBrief)
            .filter(
                DailyBrief.user_id == user_id,
                DailyBrief.date_for == d_for,
            )
            .first()
        )

        if brief is None:
            brief = DailyBrief(
                user_id=user_id,
                date_for=d_for,
                status="pending",
            )
            db.add(brief)
            db.commit()
            db.refresh(brief)

        if brief.status == "ready":
            return {"ok": True, "message": "already_ready", "date_for": d_for.isoformat()}

        if brief.status == "generating":
            return {"ok": True, "message": "already_generating", "date_for": d_for.isoformat()}

        brief.status = "generating"
        brief.error_message = None
        db.commit()

        text = generate_brief_text(db, user_id, d_for)

        brief.text = text
        brief.status = "ready"
        brief.generated_at = datetime.now(tz=timezone.utc)
        brief.error_message = None
        db.commit()

        return {"ok": True, "date_for": d_for.isoformat(), "length": len(text)}

    except Exception as exc:
        db.rollback()
        # Пометить запись как error
        try:
            brief_err = (
                db.query(DailyBrief)
                .filter(
                    DailyBrief.user_id == user_id,
                    DailyBrief.date_for == (
                        date.fromisoformat(date_for_str)
                        if date_for_str
                        else date.today() - timedelta(days=1)
                    ),
                )
                .first()
            )
            if brief_err:
                brief_err.status = "error"
                brief_err.error_message = str(exc)[:900]
                db.commit()
        except Exception:
            db.rollback()
        return {"ok": False, "error": str(exc)}
    finally:
        db.close()


@celery_app.task(name="generate_all_daily_briefs")
def generate_all_daily_briefs() -> dict:
    """
    Beat-задача: запускается автоматически в 07:00 каждый день.
    Ставит задачи generate_daily_brief для каждого активного пользователя.

    Retry-логика:
    - Если данных нет (пустой портфель) — сервис вернёт текст-заглушку, не упадёт.
    - Сама beat-задача не ретраится — индивидуальные задачи сами обрабатывают ошибки.
    - Если нужен ретрай (например, данные ещё не синкнулись) — используй POST /generate
      из фронта после того как данные появятся.
    """
    if not is_daily_brief_enabled():
        return {"ok": True, "message": "disabled", "users_queued": 0}
    db = SessionLocal()
    try:
        users = db.query(User).filter(User.is_active.is_(True)).all()
        queued = 0
        for user in users:
            try:
                generate_daily_brief.delay(str(user.id))
                queued += 1
            except Exception as exc:
                # Не прерываем весь батч из-за одного пользователя
                import logging
                logging.getLogger(__name__).error(
                    "generate_all_daily_briefs: failed to enqueue for user %s: %s",
                    user.id, exc,
                )
        return {"ok": True, "users_queued": queued}
    except Exception as e:
        return {"ok": False, "error": str(e)}
    finally:
        db.close()
