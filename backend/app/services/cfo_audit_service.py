"""
Полный аудит AI CFO по SKU (порт из оригинальной версии продукта на Google Apps Script,
см. Index.html в корне репозитория, функция askAi / customPrompt).

В отличие от daily_brief_service (короткая ежедневная сводка по "Запуск"/"Рабочий" сегментам),
здесь модели отдаётся сырая ежедневная time-series по каждому SKU за выбранный пользователем
период — и системный промпт просит найти конкретные аномалии (9-пунктовый чек-лист), а не
готовит агрегаты заранее. Арифметика (округление, отбор топ-SKU) всё равно делается в Python —
модель только интерпретирует уже посчитанные числа, тот же принцип, что и в daily_brief_service.

Вызов LLM — OpenAI Responses API (POST {AI_API_BASE_URL}/responses через httpx), те же env,
что и у единого ИИ-чата (assistant_agent_service.py): AI_API_KEY, AI_API_BASE_URL. Модель —
отдельная переменная AI_CFO_MODEL (по умолчанию "gpt-4.1"), т.к. это тяжёлый разовый анализ,
а не диалоговый агент. Без tools — это не агентский цикл, один запрос → один ответ.
"""
from __future__ import annotations

import json
import logging
import os
import re
from datetime import date, timedelta
from typing import Any

import httpx
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.article import Article
from app.models.funnel_daily import FunnelDaily
from app.models.sku_daily import SkuDaily

logger = logging.getLogger(__name__)

_AI_API_BASE = (os.getenv("AI_API_BASE_URL") or "https://api.openai.com/v1").rstrip("/")
_AI_API_KEY = os.getenv("AI_API_KEY") or ""
_AI_CFO_MODEL = os.getenv("AI_CFO_MODEL") or "gpt-4.1"
_HTTP_TIMEOUT_SEC = 240.0

# Если строк за период больше этого порога — обрезаем до топ-SKU (см. _cap_to_top_skus),
# чтобы не разносить контекст модели и не улетать по цене/времени запроса.
MAX_ROWS = 3000
TOP_SKUS_ON_CAP = 40

_THINKING_RE = re.compile(r"<thinking>.*?</thinking>", re.IGNORECASE | re.DOTALL)


def _safe_float(v: Any) -> float:
    try:
        return float(v) if v is not None else 0.0
    except (TypeError, ValueError):
        return 0.0


def _money(v: Any) -> int:
    return int(round(_safe_float(v)))


def _cap_to_top_skus(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """
    Оставляем только TOP_SKUS_ON_CAP артикулов, ранжированных по вкладу
    (|сумма margin| + сумма sales + сумма adsSum) за весь период, и добавляем
    в конец массива служебную запись с полем "note" — модель должна знать,
    что видит не весь портфель.
    """
    scores: dict[int, float] = {}
    for r in rows:
        nm = int(r["nm_id"])
        scores[nm] = scores.get(nm, 0.0) + abs(r["margin"]) + r["sales"] + r["adsSum"]

    ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
    top_ids = {nm for nm, _ in ranked[:TOP_SKUS_ON_CAP]}

    filtered = [r for r in rows if int(r["nm_id"]) in top_ids]
    note = (
        f"Данные урезаны: исходно {len(rows)} строк по {len(scores)} SKU за период, "
        f"показаны только топ-{TOP_SKUS_ON_CAP} SKU по вкладу (|чистая прибыль| + выручка + "
        f"реклама) — {len(filtered)} строк."
    )
    filtered.append({"note": note})
    return filtered


def _resolve_vendor_codes(db: Session, *, store_owner_id: str) -> dict[int, str]:
    """
    nm_id -> артикул продавца. articles.vendor_code — основной источник, но на проде
    у части продавцов он не заполнен (карточка ещё не синхронизирована/не привязана).
    funnel_daily.vendor_code — тот же артикул, приходит вместе с воронкой и на практике
    заполнен чаще, поэтому используем его как fallback (последнее ненулевое значение
    по датам), прежде чем показывать голый nm_id.
    """
    vendor_codes: dict[int, str] = {
        int(a.nm_id): str(a.vendor_code)
        for a in db.query(Article).filter(Article.user_id == store_owner_id).all()
        if a.vendor_code
    }

    funnel_rows: list[FunnelDaily] = (
        db.query(FunnelDaily)
        .filter(FunnelDaily.user_id == store_owner_id)
        .order_by(FunnelDaily.date.desc())
        .all()
    )
    for r in funnel_rows:
        nm = int(r.nm_id)
        if nm in vendor_codes or not r.vendor_code:
            continue
        vendor_codes[nm] = r.vendor_code  # первое совпадение (desc по дате) = самое свежее

    return vendor_codes


def build_cfo_timeseries(
    db: Session, *, store_owner_id: str, date_from: date, date_to: date
) -> list[dict[str, Any]]:
    """
    Per-SKU per-day витрина из sku_daily для конкретного продавца, обогащённая
    артикулом продавца (vendor_code): articles -> funnel_daily (fallback) -> nm_id.
    Формат строк — как ждёт промпт CFO-аудита (см. build_cfo_audit_prompt): sales/margin/
    openCount/cartCount/orderCount/logSum/adsSum/storage/penalties/cogs, деньги — целые рубли.
    """
    rows: list[SkuDaily] = (
        db.query(SkuDaily)
        .filter(
            SkuDaily.user_id == store_owner_id,
            SkuDaily.date >= date_from,
            SkuDaily.date <= date_to,
        )
        .order_by(SkuDaily.date)
        .all()
    )

    vendor_codes = _resolve_vendor_codes(db, store_owner_id=store_owner_id)

    out: list[dict[str, Any]] = []
    for r in rows:
        sales = _money(r.revenue)
        margin = _money(r.margin)
        open_count = int(r.open_count or 0)
        cart_count = int(r.cart_count or 0)
        order_count = int(r.order_count or 0)
        log_sum = _money(r.logistics)
        ads_sum = _money(r.ads_spend)
        storage = _money(r.storage)
        penalties = _money(r.penalties)
        cogs = _money(r.cogs)

        if not any(
            [sales, margin, open_count, cart_count, order_count, log_sum, ads_sum, storage, penalties, cogs]
        ):
            continue

        nm = int(r.nm_id)
        vendor_code = vendor_codes.get(nm)
        out.append(
            {
                "date": r.date.isoformat(),
                "nm_id": nm,
                "supplierArticle": vendor_code if vendor_code else f"ID: {nm}",
                "sales": sales,
                "margin": margin,
                "openCount": open_count,
                "cartCount": cart_count,
                "orderCount": order_count,
                "logSum": log_sum,
                "adsSum": ads_sum,
                "storage": storage,
                "penalties": penalties,
                "cogs": cogs,
            }
        )

    if len(out) > MAX_ROWS:
        out = _cap_to_top_skus(out)

    return out


def resolve_default_date_to(db: Session, *, store_owner_id: str) -> date:
    """Последняя дата, на которую есть sku_daily у продавца; иначе — вчера."""
    max_date = db.query(func.max(SkuDaily.date)).filter(SkuDaily.user_id == store_owner_id).scalar()
    if max_date is not None:
        return max_date
    return date.today() - timedelta(days=1)


def build_cfo_audit_prompt(*, date_from: date, date_to: date, rows: list[dict[str, Any]]) -> str:
    """
    Системный промпт CFO-аудита — ВЕРБАТИМ порт customPrompt из Index.html (строки ~1212-1264),
    с точечными адаптациями под добавленные поля: storage/penalties/cogs в словаре терминов и
    в списке запрещённых английских слов, период анализа и сегодняшняя дата перед задачей,
    сама выгрузка данных — компактный JSON без ensure_ascii-эскейпинга кириллицы.
    """
    note = None
    if rows and set(rows[-1].keys()) == {"note"}:
        note = rows[-1]["note"]

    data_json = json.dumps(rows, ensure_ascii=False, separators=(",", ":"))

    note_block = f"\n  ПРИМЕЧАНИЕ ПО ДАННЫМ: {note}\n" if note else ""

    span_days = (date_to - date_from).days + 1

    return f"""
  АНАЛИЗИРУЕМЫЙ ПЕРИОД: {span_days} дней, с {date_from:%d.%m.%Y} по {date_to:%d.%m.%Y}. Сегодняшняя дата: {date.today():%d.%m.%Y}.
  Весь массив данных ниже — это именно эти {span_days} дней, не путай период с "неделей" или любым другим окном.

  Ты — Финансовый Директор (CFO) и Data Scientist e-commerce бизнеса на Wildberries.
  Твоя задача: найти скрытые закономерности, точки потерь и драйверы роста на основе ежедневной динамики продаж и юнит-экономики по артикулам (SKU).

  СЛОВАРЬ ТЕРМИНОВ (ОБЯЗАТЕЛЬНО К ПРИМЕНЕНИЮ):
  - margin: В предоставленных данных это СТРОГО ЧИСТАЯ ПРИБЫЛЬ в рублях. Это финальные деньги, которые остаются после вычета абсолютно ВСЕХ расходов (себестоимость, логистика, комиссия ВБ, налоги, хранение, штрафы и реклама).
  - sales: Грязная выручка (сумма заказов/выкупов до вычетов).
  - storage: Расходы на хранение товара на складе WB за день, в рублях.
  - penalties: Штрафы от WB за день, в рублях.
  - cogs: Себестоимость проданного товара (закупка/производство) за день, в рублях.

  КРИТИЧЕСКОЕ ТРЕБОВАНИЕ К ЯЗЫКУ:
  КАТЕГОРИЧЕСКИ ЗАПРЕЩЕНО использовать английские слова из JSON (margin, sales, openCount, cartCount, orderCount, logSum, adsSum, storage, penalties, cogs).
  Используй ТОЛЬКО русские бизнес-термины: Чистая прибыль, Выручка, Переходы в карточку, Добавления в корзину, Заказы, Логистика, Реклама, Хранение, Штрафы, Себестоимость. Текст должен звучать естественно для русскоязычного селлера.
{note_block}
  МАССИВ ДАННЫХ ДЛЯ АНАЛИЗА:
  {data_json}

  ПРАВИЛА АНАЛИЗА (ЖЕСТКИЕ ОГРАНИЧЕНИЯ):
  - ЗАПРЕТ НА ФАНТАЗИИ: Опирайся ТОЛЬКО на цифры. Запрещено ссылаться на "сезонность", "алгоритмы ВБ" или "погоду".
  - ТОЧНОСТЬ: Любой вывод обязан подкрепляться конкретной датой, SKU и цифрой.
  - ФОРМАТИРОВАНИЕ: Выделяй важные цифры и ключевые термины жирным шрифтом (используй **текст**). Обязательно разделяй мысли на абзацы для легкости чтения. Исключи воду.

  ОБЯЗАТЕЛЬНЫЙ ЧЕКЛИСТ ДЛЯ ПОИСКА АНОМАЛИЙ (Проверь каждый пункт):
  1. Выгорание рекламы: Растет ли сумма рекламы при стагнирующих переходах/заказах?
  2. Каннибализация логистикой: Есть ли дни с резким скачком логистики при неизменных заказах?
  3. Токсичный трафик: Всплески переходов без роста корзин на фоне трат на рекламу.
  4. Эластичность цены: Дало ли снижение чистой прибыли (скидка) математически оправданный прирост заказов?
  5. Дырявая корзина: Падает ли конверсия в заказ при высокой корзине?
  6. Донор-Реципиент: Какой SKU сжигает чистую прибыль других артикулов на рекламу без ROI?
  7. Кассовый разрыв выкупа: Растет ли логистика при падении заказов через несколько дней?
  8. Спираль хранения: Растет ли ежедневно хранение при падающих заказах?
  9. Точка безубыточности: Превышен ли предельный % рекламы, уводящий дневную прибыль в минус?

  ОТВЕТЬ СТРОГО ПО ПУНКТАМ:
  1. КРИТИЧЕСКИЕ АНОМАЛИИ (Где теряем деньги прямо сейчас).
  2. ТОЧКИ СВЕРХПРИБЫЛИ (Что масштабировать).
  3. СТРАТЕГИЧЕСКИЙ ВЫВОД.
  4. ACTION PLAN (3 жестких указания к действию на сегодня).

ПРАВИЛА ОФОРМЛЕНИЯ И ЛОГИКИ:
1. Обязательный блок размышлений: Перед генерацией самого отчета ты ОБЯЗАН написать свои мысли в тегах <thinking>...</thinking>. В этом блоке сопоставь метрики (расход на рекламу vs прибыль, конверсии) и найди аномалии. Этот блок должен быть скрыт от финального читателя, но обязателен для твоего процесса.
2. Использование артикулов: В финальном отчете СТРОГО запрещено использовать числовые идентификаторы Wildberries (nm_id / nmId) как основные. Если в данных есть "Артикул продавца" (supplierArticle), используй ТОЛЬКО его (например: LEGGINGS-STIRRUPS-BLK). Если передан только nm_id, оформляй его так: [SKU: 12345678].
3. Форматирование (Strict Markdown):
   - Не используй сплошное полотно текста.
   - Используй жирный шрифт (**текст**) для выделения всех сумм (в рублях), процентов и ключевых метрик.
   - Используй эмодзи только как визуальные якоря для разделов (🔴 для убытков/критичного, 🟢 для точек роста, 📊 для заголовка, ⚡ для экшен-плана).
   - Разделяй смысловые блоки горизонтальной линией (---).
   - Заголовки оформляй жирным капсом, а не через ##.
4. Структура отчета:
   - EXECUTIVE SUMMARY (краткая выжимка ситуации на 2 предложения).
   - 🔴 ЗОНЫ КРИТИЧЕСКИХ ПОТЕРЬ (где бизнес теряет деньги прямо сейчас: убыточная реклама, дорогая логистика при нулевой выручке, низкая конверсия).
   - 🟢 ТОЧКИ СВЕРХПРИБЫЛИ (товары с идеальной юнит-экономикой, которые приносят прибыль без рекламы).
   - ⚡ ACTION PLAN (3-4 жестких, конкретных указания к действию на сегодня с прогнозом экономии/заработка).
5. Тон: Профессиональный, прямой, без воды. Называй вещи своими именами (не "возможно, стоит снизить", а "немедленно отключить", "каннибализация прибыли").
"""


def _strip_thinking(text: str) -> str:
    return _THINKING_RE.sub("", text).strip()


def _call_cfo_model(prompt: str) -> str:
    if not _AI_API_KEY:
        raise ValueError("AI_API_KEY не задан (нужен для работы AI CFO).")

    url = f"{_AI_API_BASE}/responses"
    headers = {"Authorization": f"Bearer {_AI_API_KEY}", "Content-Type": "application/json"}
    body = {
        "model": _AI_CFO_MODEL,
        "input": [{"role": "user", "content": prompt}],
    }
    resp = httpx.post(url, headers=headers, json=body, timeout=_HTTP_TIMEOUT_SEC)
    resp.raise_for_status()
    data = resp.json()

    output_items = list(data.get("output") or [])
    text_parts: list[str] = []
    for item in output_items:
        if item.get("type") != "message":
            continue
        for content in item.get("content") or []:
            if content.get("type") not in ("output_text", "text"):
                continue
            t = str(content.get("text") or "")
            if t:
                text_parts.append(t)
    return "\n".join(text_parts).strip()


def run_cfo_audit(db: Session, *, store_owner_id: str, date_from: date, date_to: date) -> str:
    rows = build_cfo_timeseries(db, store_owner_id=store_owner_id, date_from=date_from, date_to=date_to)
    if not rows:
        return f"Нет данных по артикулам за период {date_from}–{date_to} — анализ AI CFO не из чего строить."

    prompt = build_cfo_audit_prompt(date_from=date_from, date_to=date_to, rows=rows)
    logger.info(
        "cfo_audit: running for store_owner_id=%s date_from=%s date_to=%s rows=%d",
        store_owner_id, date_from, date_to, len(rows),
    )
    raw = _call_cfo_model(prompt)
    return _strip_thinking(raw)
