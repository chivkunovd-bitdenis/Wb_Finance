"""
Клиент к API Wildberries. Логика как в GAS (Code.js): те же URL, пагинация rrid.
"""
import logging
import random
import time

import requests

logger = logging.getLogger(__name__)

SALES_URL = "https://statistics-api.wildberries.ru/api/v5/supplier/reportDetailByPeriod"


def _wb_header_int(resp: requests.Response, name: str) -> int | None:
    v = resp.headers.get(name)
    if v is None:
        return None
    try:
        return int(str(v).strip())
    except Exception:
        return None


def _wb_error_detail(resp: requests.Response) -> tuple[str | None, str | None, str | None]:
    """
    Extract (title, detail, request_id) from WB JSON error if present.
    WB often returns: {"title": "...", "detail": "...", "requestId": "..."} on 4xx/5xx.
    """
    try:
        j = resp.json()
    except Exception:
        return None, None, None
    if not isinstance(j, dict):
        return None, None, None
    title = j.get("title")
    detail = j.get("detail")
    request_id = j.get("requestId") or j.get("request_id")
    return (
        str(title)[:200] if title else None,
        str(detail)[:600] if detail else None,
        str(request_id)[:120] if request_id else None,
    )


def _log_wb_http_error(
    *,
    resp: requests.Response,
    op: str,
    url: str,
    log_context: str | None = None,
    extra: dict[str, object] | None = None,
    level: int = logging.WARNING,
) -> None:
    """
    Log WB HTTP error in a diagnosis-friendly way:
    - http code + url/op + optional period/rrid/chunk
    - rate limit headers + requestId/detail from body (if any)
    """
    status = int(resp.status_code)
    limit = _wb_header_int(resp, "X-RateLimit-Limit")
    remaining = _wb_header_int(resp, "X-RateLimit-Remaining")
    reset = _wb_header_int(resp, "X-RateLimit-Reset")
    retry_after = resp.headers.get("Retry-After")
    title, detail, request_id = _wb_error_detail(resp)
    try:
        body_preview = (resp.text or "")[:300].replace("\n", "\\n")
    except Exception:
        body_preview = "<no-body>"

    parts: list[str] = [f"wb_http op={op}", f"http={status}", f"url={url}"]
    if log_context:
        parts.append(str(log_context)[:300])
    if limit is not None:
        parts.append(f"limit={limit}")
    if remaining is not None:
        parts.append(f"remaining={remaining}")
    if reset is not None:
        parts.append(f"reset_sec={reset}")
    if retry_after:
        parts.append(f"retry_after={str(retry_after)[:32]}")
    if request_id:
        parts.append(f"request_id={request_id}")
    if title:
        parts.append(f"title={title}")
    if detail:
        parts.append(f"detail={detail}")
    if extra:
        for k, v in extra.items():
            if v is None:
                continue
            parts.append(f"{k}={str(v)[:200]}")
    parts.append(f"body_preview={body_preview}")

    logger.log(level, " ".join(parts))


def _parse_date(value) -> str | None:
    """Привести к YYYY-MM-DD."""
    if not value:
        return None
    if hasattr(value, "split"):
        return value.split("T")[0].split(" ")[0]
    return str(value)[:10]


def fetch_sales(date_from: str, date_to: str, wb_api_key: str) -> list[dict]:
    """
    Загрузить продажи за период. Пагинация по rrid (как в GAS fetchWbWithRrid).
    Возвращает список словарей с ключами: date, nm_id, doc_type, retail_price, ppvz_for_pay,
    delivery_rub, penalty, additional_payment, storage_fee, quantity.
    """
    headers = {"Authorization": wb_api_key}
    all_rows = []
    rrid = 0

    while True:
        url = f"{SALES_URL}?dateFrom={date_from}&dateTo={date_to}&period=daily&limit=100000&rrid={rrid}"
        resp = requests.get(url, headers=headers, timeout=120)
        if resp.status_code != 200:
            _log_wb_http_error(
                resp=resp,
                op="sales",
                url=SALES_URL,
                extra={"date_from": date_from, "date_to": date_to, "rrid": rrid},
                level=logging.WARNING if resp.status_code in {429, 500, 502, 503, 504} else logging.ERROR,
            )
            resp.raise_for_status()
        data = resp.json()
        if not data or not isinstance(data, list):
            break

        for row in data:
            # WB может отдавать date_from или date, doc_type_name или doc_type
            d = _parse_date(row.get("date_from") or row.get("date"))
            if not d:
                continue
            all_rows.append({
                "date": d,
                "nm_id": row.get("nm_id") or row.get("nmId"),
                "doc_type": row.get("doc_type_name") or row.get("doc_type") or "",
                "retail_price": row.get("retail_price"),
                "ppvz_for_pay": row.get("ppvz_for_pay"),
                "delivery_rub": row.get("delivery_rub"),
                "penalty": row.get("penalty"),
                "additional_payment": row.get("additional_payment"),
                "storage_fee": row.get("storage_fee"),
                "quantity": row.get("quantity", 1),
                # В отчёте WB может присутствовать название категории/предмета.
                # Мы пробуем несколько известных ключей и храним это в Article.subject_name при наличии.
                "subject_name": row.get("subject_name")
                or row.get("subjectName")
                or row.get("subject")
                or row.get("subject_title"),
            })

        if len(data) >= 100000 and data[-1].get("rrid"):
            rrid = data[-1]["rrid"]
        else:
            break

    return all_rows


ADS_UPD_URL = "https://advert-api.wildberries.ru/adv/v1/upd"
ADS_ADVERTS_URL = "https://advert-api.wildberries.ru/api/advert/v2/adverts"


def _fetch_campaigns_details(wb_api_key: str, campaign_ids: list[int]) -> dict[int, list[int]]:
    """Артикулы (nm_id) по каждой кампании. Чанки по 50, пауза 300 мс (как в GAS)."""
    headers = {"Authorization": wb_api_key}
    result: dict[int, list[int]] = {}
    for i in range(0, len(campaign_ids), 50):
        chunk = campaign_ids[i : i + 50]
        url = f"{ADS_ADVERTS_URL}?ids={','.join(str(x) for x in chunk)}"
        resp = requests.get(url, headers=headers, timeout=30)
        if resp.status_code != 200:
            continue
        data = resp.json()
        adverts = data if isinstance(data, list) else (data.get("adverts") or [])
        for c in adverts:
            cid = c.get("id") or c.get("advertId")
            if cid is None:
                continue
            nms = []
            for s in (c.get("nm_settings") or []):
                nm = s.get("nm_id")
                if nm is not None:
                    nms.append(int(nm))
            result[cid] = nms
        time.sleep(0.3)
    return result


def fetch_ads(date_from: str, date_to: str, wb_api_key: str) -> list[dict]:
    """
    Загрузить рекламу за период: adv/v1/upd, затем детали кампаний (v2/adverts).
    Расход по кампании делим поровну между nm_id (как в GAS).
    Возвращает список: date, nm_id, campaign_id, spend.
    """
    headers = {"Authorization": wb_api_key}
    url = f"{ADS_UPD_URL}?from={date_from}&to={date_to}"
    resp = requests.get(url, headers=headers, timeout=60)
    if resp.status_code != 200:
        _log_wb_http_error(
            resp=resp,
            op="ads_upd",
            url=ADS_UPD_URL,
            extra={"date_from": date_from, "date_to": date_to},
            level=logging.WARNING if resp.status_code in {429, 500, 502, 503, 504} else logging.ERROR,
        )
        if resp.status_code in {429, 500, 502, 503, 504}:
            raise requests.HTTPError(
                f"{resp.status_code} {resp.reason} for {ADS_UPD_URL}",
                response=resp,
            )
        return []
    data = resp.json()
    if not data or not isinstance(data, list):
        return []

    ids = list({int(x["advertId"]) for x in data if x.get("advertId") is not None})
    details = _fetch_campaigns_details(wb_api_key, ids) if ids else {}

    rows = []
    for ad in data:
        cid = ad.get("advertId")
        if cid is not None:
            cid = int(cid)
        upd_sum = float(ad.get("updSum") or 0)
        d = _parse_date(ad.get("updTime") or ad.get("date") or date_to)
        if not d:
            continue
        # Только записи в запрошенном периоде (API может отдать граничные по времени/часовому поясу)
        if d < date_from or d > date_to:
            continue
        nms = details.get(cid) or []
        if nms:
            spend_each = upd_sum / len(nms)
            for nm in nms:
                rows.append({"date": d, "nm_id": nm, "campaign_id": cid, "spend": spend_each})
        else:
            rows.append({"date": d, "nm_id": None, "campaign_id": cid, "spend": upd_sum})
    return rows


FUNNEL_URL = "https://seller-analytics-api.wildberries.ru/api/analytics/v3/sales-funnel/products/history"
FUNNEL_PRODUCTS_URL = "https://seller-analytics-api.wildberries.ru/api/analytics/v3/sales-funnel/products"
FUNNEL_CHUNK_SIZE = 20
FUNNEL_SLEEP_SEC = 25
# Сколько раз подряд повторять один и тот же чанк при 429/5xx прежде чем пробросить ошибку наверх (Celery сделает отложенный retry).
FUNNEL_CHUNK_MAX_ATTEMPTS = 12
FUNNEL_RETRYABLE_HTTP = frozenset({429, 500, 502, 503, 504})


def _funnel_chunk_backoff_sec(attempt: int, http_code: int) -> float:
    """Экспоненциальная пауза между попытками одного чанка (как в celery funnel YTD)."""
    base = 45 if http_code >= 500 else 90
    core = min(1800, base * (2 ** max(0, attempt - 1)))
    return float(core + random.randint(0, 30))


def _funnel_wb_msg(log_context: str | None, body: str) -> str:
    """Единый префикс для grep: `funnel_wb` + опционально user_id=… из Celery."""
    if log_context:
        return f"funnel_wb {log_context} {body}"
    return f"funnel_wb {body}"


def _int_nm(nm_raw: object) -> int | None:
    try:
        if nm_raw is None:
            return None
        v = int(str(nm_raw))
    except (TypeError, ValueError):
        return None
    return v if v > 0 else None


def fetch_funnel_products_for_day(day: str, nm_ids: list[int], wb_api_key: str) -> list[dict]:
    """
    Воронка за один календарный день через POST /sales-funnel/products (агрегаты за день на товар).
    selectedPeriod и pastPeriod — один и тот же день, как допускает WB для «по дню».
    """
    if not nm_ids:
        return []
    headers = {
        "Authorization": wb_api_key,
        "Content-Type": "application/json",
    }
    payload: dict = {
        "selectedPeriod": {"start": day, "end": day},
        "nmIds": nm_ids,
        "skipDeletedNm": True,
        "limit": max(100, len(nm_ids) * 4),
        "offset": 0,
    }
    resp = requests.post(FUNNEL_PRODUCTS_URL, json=payload, headers=headers, timeout=120)
    if resp.status_code != 200:
        _log_wb_http_error(
            resp=resp,
            op="funnel_products",
            url=FUNNEL_PRODUCTS_URL,
            extra={"day": day, "nm_in_chunk": len(nm_ids)},
            level=logging.WARNING if resp.status_code in {429, 500, 502, 503, 504} else logging.ERROR,
        )
        raise requests.HTTPError(
            f"{resp.status_code} {resp.reason} for {FUNNEL_PRODUCTS_URL}",
            response=resp,
        )
    body = resp.json()
    block = (body or {}).get("data") or {}
    products = block.get("products") or []
    out: list[dict] = []

    for entry in products:
        product = entry.get("product") or {}
        stat_block = entry.get("statistic") or {}
        sel = stat_block.get("selected") or {}
        nm_raw = product.get("nmId") or product.get("nm_id")
        nm_id = _int_nm(nm_raw)
        if nm_id is None:
            continue

        vendor_code = product.get("vendorCode") or product.get("vendor_code")
        vendor_code = str(vendor_code)[:255] if vendor_code else None

        subject_name = (
            product.get("subjectName")
            or product.get("subject_name")
            or product.get("subject")
        )
        subject_name = str(subject_name)[:500] if subject_name else None

        conv = sel.get("conversions") or {}
        wb_club = sel.get("wbClub") or {}

        add_pct = conv.get("addToCartPercent")
        co_pct = conv.get("cartToOrderPercent")
        buy_pct = conv.get("buyoutPercent")
        if buy_pct is None or (isinstance(buy_pct, (int, float)) and float(buy_pct) == 0):
            b2 = wb_club.get("buyoutPercent")
            if b2 is not None:
                buy_pct = b2

        cr_to_cart = float(add_pct) / 100.0 if add_pct is not None else None
        cr_to_order = float(co_pct) / 100.0 if co_pct is not None else None
        buyout_f = float(buy_pct) if buy_pct is not None else None

        out.append({
            "date": day,
            "nm_id": nm_id,
            "vendor_code": vendor_code,
            "open_count": int(sel.get("openCount") or 0),
            "cart_count": int(sel.get("cartCount") or 0),
            "order_count": int(sel.get("orderCount") or 0),
            "order_sum": float(sel.get("orderSum") or 0),
            "buyout_percent": buyout_f,
            "cr_to_cart": cr_to_cart,
            "cr_to_order": cr_to_order,
            "subject_name": subject_name,
        })

    return out


def fetch_funnel_products_for_day_with_retry(
    day: str,
    nm_ids: list[int],
    wb_api_key: str,
    *,
    max_attempts: int = FUNNEL_CHUNK_MAX_ATTEMPTS,
    log_context: str | None = None,
) -> list[dict]:
    """
    Один POST /sales-funnel/products за день на набор nm_id.
    Повторяет запрос только при 429/5xx; при исчерпании попыток — HTTPError наверх.
    Успех дня по чанкам в Celery: все вызовы этой функции для чанков должны завершиться без исключения.
    """
    tries = 0
    last_exc: requests.HTTPError | None = None
    while tries < max_attempts:
        tries += 1
        try:
            out = fetch_funnel_products_for_day(day, nm_ids, wb_api_key)
            logger.info(
                _funnel_wb_msg(
                    log_context,
                    "api=products day=%s nm_in_chunk=%s http=200 rows=%s tries=%s",
                ),
                day,
                len(nm_ids),
                len(out),
                tries,
            )
            return out
        except requests.HTTPError as exc:
            last_exc = exc
            code = exc.response.status_code if exc.response is not None else None
            if code not in FUNNEL_RETRYABLE_HTTP:
                logger.error(
                    _funnel_wb_msg(
                        log_context,
                        "api=products day=%s nm_in_chunk=%s fatal_http=%s tries=%s err=%s",
                    ),
                    day,
                    len(nm_ids),
                    code,
                    tries,
                    str(exc)[:400],
                )
                raise
            if tries >= max_attempts:
                break
            delay = _funnel_chunk_backoff_sec(tries, int(code or 429))
            logger.warning(
                _funnel_wb_msg(
                    log_context,
                    "api=products day=%s nm_in_chunk=%s http=%s tries=%s/%s sleep_sec=%.1f err=%s",
                ),
                day,
                len(nm_ids),
                code,
                tries,
                max_attempts,
                delay,
                str(exc)[:400],
            )
            time.sleep(delay)
    assert last_exc is not None
    logger.error(
        _funnel_wb_msg(
            log_context,
            "api=products day=%s nm_in_chunk=%s exhausted_tries=%s last_err=%s",
        ),
        day,
        len(nm_ids),
        max_attempts,
        str(last_exc)[:400],
    )
    raise last_exc


def _parse_funnel_history_response(data: object, _date_from: str, _date_to: str) -> list[dict]:
    """Разбор тела ответа history-API в плоские строки по дням (как в fetch_funnel)."""
    all_rows: list[dict] = []
    if isinstance(data, list):
        items = data
    elif isinstance(data, dict):
        items = data.get("data") or data.get("cards") or []
    else:
        items = []

    for item in items:
        nm_raw = item.get("nmId") or (item.get("product") or {}).get("nmId")
        nm_parsed = _int_nm(nm_raw)
        if nm_parsed is None:
            continue
        subject_name = (
            item.get("subjectName")
            or item.get("subject_name")
            or (item.get("product") or {}).get("subjectName")
            or (item.get("product") or {}).get("subject_name")
            or (item.get("product") or {}).get("subject")
            or item.get("subject")
        )
        vendor_code = (
            item.get("vendorCode")
            or (item.get("product") or {}).get("vendorCode")
            or ""
        )
        vendor_code = str(vendor_code)[:255] if vendor_code else None
        history = item.get("history") or []
        for h in history:
            d = _parse_date(h.get("date") or h.get("dt"))
            if not d:
                continue
            all_rows.append({
                "date": d,
                "nm_id": nm_parsed,
                "vendor_code": vendor_code,
                "open_count": int(h.get("openCount") or 0),
                "cart_count": int(h.get("cartCount") or 0),
                "order_count": int(h.get("orderCount") or 0),
                "order_sum": float(h.get("orderSum") or 0),
                "buyout_percent": float(h.get("buyoutPercent") or 0),
                "cr_to_cart": float(h.get("addToCartConversion") or h.get("cr1") or 0),
                "cr_to_order": float(h.get("cartToOrderConversion") or h.get("cr2") or 0),
                "subject_name": str(subject_name)[:500] if subject_name else None,
            })
    return all_rows


def fetch_funnel(
    date_from: str,
    date_to: str,
    nm_ids: list[int],
    wb_api_key: str,
    *,
    log_context: str | None = None,
    sleep_on_retry: bool = True,
) -> list[dict]:
    """
    Загрузить воронку продаж по артикулам за период.
    nm_ids запрашиваются чанками по 20, между запросами пауза 25 сек (как в GAS).
    Каждый чанк: только HTTP 200 считается успехом; при 429/5xx повторяем тот же чанк,
    не переходим к следующему. Прочие коды — сразу ошибка (не глотаем «тихим» continue).
    Возвращает список словарей: date, nm_id, vendor_code, open_count, cart_count,
    order_count, order_sum, buyout_percent, cr_to_cart, cr_to_order.
    """
    headers = {
        "Authorization": wb_api_key,
        "Content-Type": "application/json",
    }
    all_rows: list[dict] = []
    n_chunks = max(1, (len(nm_ids) + FUNNEL_CHUNK_SIZE - 1) // FUNNEL_CHUNK_SIZE)
    chunk_no = 0

    for i in range(0, len(nm_ids), FUNNEL_CHUNK_SIZE):
        chunk_no += 1
        chunk = nm_ids[i : i + FUNNEL_CHUNK_SIZE]
        payload = {
            "selectedPeriod": {"start": date_from, "end": date_to},
            "nmIds": chunk,
            "skipDeletedNm": True,
            "aggregationLevel": "day",
        }
        tries = 0
        while True:
            tries += 1
            resp = requests.post(FUNNEL_URL, json=payload, headers=headers, timeout=60)
            if resp.status_code == 200:
                try:
                    data = resp.json()
                except Exception as exc:
                    logger.exception(
                        _funnel_wb_msg(
                            log_context,
                            "api=history period=%s..%s chunk=%s/%s invalid_json tries=%s",
                        ),
                        date_from,
                        date_to,
                        chunk_no,
                        n_chunks,
                        tries,
                    )
                    raise requests.HTTPError(
                        f"Invalid JSON for {FUNNEL_URL}: {exc}",
                        response=resp,
                    ) from exc
                parsed = _parse_funnel_history_response(data, date_from, date_to)
                all_rows.extend(parsed)
                logger.info(
                    _funnel_wb_msg(
                        log_context,
                        "api=history period=%s..%s chunk=%s/%s nm_in_chunk=%s http=200 "
                        "parsed_rows=%s tries=%s",
                    ),
                    date_from,
                    date_to,
                    chunk_no,
                    n_chunks,
                    len(chunk),
                    len(parsed),
                    tries,
                )
                break
            if resp.status_code not in FUNNEL_RETRYABLE_HTTP:
                _log_wb_http_error(
                    resp=resp,
                    op="funnel_history",
                    url=FUNNEL_URL,
                    log_context=log_context,
                    extra={
                        "date_from": date_from,
                        "date_to": date_to,
                        "chunk": f"{chunk_no}/{n_chunks}",
                        "nm_in_chunk": len(chunk),
                        "tries": tries,
                        "fatal": True,
                    },
                    level=logging.ERROR,
                )
                logger.error(
                    _funnel_wb_msg(
                        log_context,
                        "api=history period=%s..%s chunk=%s/%s fatal_http=%s tries=%s",
                    ),
                    date_from,
                    date_to,
                    chunk_no,
                    n_chunks,
                    resp.status_code,
                    tries,
                )
                resp.raise_for_status()
            if tries >= FUNNEL_CHUNK_MAX_ATTEMPTS:
                _log_wb_http_error(
                    resp=resp,
                    op="funnel_history",
                    url=FUNNEL_URL,
                    log_context=log_context,
                    extra={
                        "date_from": date_from,
                        "date_to": date_to,
                        "chunk": f"{chunk_no}/{n_chunks}",
                        "nm_in_chunk": len(chunk),
                        "tries": tries,
                        "exhausted": True,
                    },
                    level=logging.ERROR,
                )
                logger.error(
                    _funnel_wb_msg(
                        log_context,
                        "api=history period=%s..%s chunk=%s/%s exhausted_http=%s tries=%s",
                    ),
                    date_from,
                    date_to,
                    chunk_no,
                    n_chunks,
                    resp.status_code,
                    tries,
                )
                resp.raise_for_status()

            # Non-blocking режим для фоновых задач (Celery):
            # не делаем time.sleep внутри воркера на 429/5xx, чтобы не блокировать пул.
            # Вместо этого даём вызывающему коду (celery task) поставить retry через countdown.
            if not sleep_on_retry:
                _log_wb_http_error(
                    resp=resp,
                    op="funnel_history",
                    url=FUNNEL_URL,
                    log_context=log_context,
                    extra={
                        "date_from": date_from,
                        "date_to": date_to,
                        "chunk": f"{chunk_no}/{n_chunks}",
                        "nm_in_chunk": len(chunk),
                        "tries": f"{tries}/{FUNNEL_CHUNK_MAX_ATTEMPTS}",
                        "non_blocking_retry": True,
                    },
                    level=logging.WARNING,
                )
                resp.raise_for_status()
            delay = _funnel_chunk_backoff_sec(tries, int(resp.status_code))
            _log_wb_http_error(
                resp=resp,
                op="funnel_history",
                url=FUNNEL_URL,
                log_context=log_context,
                extra={
                    "date_from": date_from,
                    "date_to": date_to,
                    "chunk": f"{chunk_no}/{n_chunks}",
                    "nm_in_chunk": len(chunk),
                    "tries": f"{tries}/{FUNNEL_CHUNK_MAX_ATTEMPTS}",
                    "sleep_sec": f"{delay:.1f}",
                },
                level=logging.WARNING,
            )
            logger.warning(
                _funnel_wb_msg(
                    log_context,
                    "api=history period=%s..%s chunk=%s/%s nm_in_chunk=%s http=%s tries=%s/%s "
                    "sleep_sec=%.1f",
                ),
                date_from,
                date_to,
                chunk_no,
                n_chunks,
                len(chunk),
                resp.status_code,
                tries,
                FUNNEL_CHUNK_MAX_ATTEMPTS,
                delay,
            )
            time.sleep(delay)

        if i + FUNNEL_CHUNK_SIZE < len(nm_ids):
            time.sleep(FUNNEL_SLEEP_SEC)

    return all_rows
