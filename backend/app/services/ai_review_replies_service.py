from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any

import httpx
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.ai_review_reply import AiReviewReply

logger = logging.getLogger(__name__)


WB_FEEDBACKS_URL = "https://feedbacks-api.wildberries.ru/api/v1/feedbacks"
WB_FEEDBACKS_ANSWER_URL = "https://feedbacks-api.wildberries.ru/api/v1/feedbacks/answer"

# Reuse existing "OpenAI-compatible" LLM config used by daily_brief/offer_rag
_AI_API_BASE = (os.getenv("AI_API_BASE_URL") or "https://api.openai.com/v1").rstrip("/")
_AI_API_KEY = os.getenv("AI_API_KEY") or ""
_AI_MODEL = os.getenv("AI_MODEL") or "gpt-4o-mini"
_AI_TIMEOUT_SEC = float(os.getenv("AI_TIMEOUT_SEC") or "60")
_AI_MAX_TOKENS = int(os.getenv("AI_MAX_TOKENS") or "220")


@dataclass(frozen=True)
class SyncResult:
    fetched: int
    upserted: int
    suggested_generated: int
    pending_total: int


def _require_ai_key() -> None:
    if not _AI_API_KEY.strip():
        raise ValueError("AI_API_KEY is not set")


def _build_reply_prompt(*, product_name: str | None, rating: str | None, review_text: str | None) -> str:
    prod = (product_name or "—").strip()
    txt = (review_text or "").strip() or "(без текста)"
    r = (rating or "").strip() or "—"
    return (
        "Ты помощник продавца на Wildberries. Напиши вежливый, короткий и живой ответ на отзыв покупателя.\n"
        "Правила:\n"
        "- 2–3 предложения.\n"
        "- Без шаблонных фраз типа «Уважаемый покупатель».\n"
        "- Без обещаний, которые нельзя гарантировать.\n"
        "- Если отзыв негативный — признаём проблему и предлагаем решение кратко.\n"
        "- Не упоминай, что ты ИИ.\n\n"
        f"Товар: {prod}\n"
        f"Оценка: {r}\n"
        f"Отзыв: {txt}\n"
    ).strip()


def _call_ai(*, prompt: str) -> str:
    _require_ai_key()
    url = f"{_AI_API_BASE}/chat/completions"
    headers = {
        "Authorization": f"Bearer {_AI_API_KEY}",
        "Content-Type": "application/json",
    }
    body = {
        "model": _AI_MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": _AI_MAX_TOKENS,
        "temperature": 0.7,
    }
    resp = httpx.post(url, headers=headers, json=body, timeout=_AI_TIMEOUT_SEC)
    resp.raise_for_status()
    data = resp.json()
    choices = data.get("choices") or []
    if not choices:
        raise ValueError("Empty AI response")
    text: str = choices[0].get("message", {}).get("content") or ""
    return text.strip()


def fetch_unanswered_reviews(*, wb_api_key: str, take: int = 20) -> list[dict[str, Any]]:
    """
    Fetch unanswered reviews from WB feedbacks API.

    We keep this function minimal and resilient:
    - only `isAnswered=false`
    - small `take` to avoid heavy calls from UI-triggered sync
    """
    take = max(1, min(int(take or 20), 50))
    params = {"isAnswered": "false", "take": str(take), "skip": "0"}
    headers = {"Authorization": wb_api_key}
    try:
        resp = httpx.get(WB_FEEDBACKS_URL, headers=headers, params=params, timeout=30.0)
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(f"WB feedbacks request failed: {exc}") from exc
    if resp.status_code != 200:
        raise RuntimeError(f"WB feedbacks request failed: http={resp.status_code} body={resp.text[:300]}")
    data = resp.json()
    if not isinstance(data, dict):
        return []
    block = data.get("data") or {}
    fbs = block.get("feedbacks") or []
    if not isinstance(fbs, list):
        return []
    return [x for x in fbs if isinstance(x, dict)]


def _upsert_review_row(
    *,
    db: Session,
    user_id: str,
    today: date,
    fb: dict[str, Any],
) -> tuple[AiReviewReply, bool]:
    feedback_id = str(fb.get("id") or "").strip()
    if not feedback_id:
        raise ValueError("feedback_id is missing")

    product_name = None
    pd = fb.get("productDetails") or {}
    if isinstance(pd, dict):
        pn = pd.get("productName")
        if pn:
            product_name = str(pn)[:512]
    author = fb.get("userName")
    author_s = str(author)[:255] if author else None
    rating = fb.get("productValuation")
    rating_s = str(rating)[:16] if rating is not None else None
    review_text = fb.get("text")
    review_text_s = str(review_text) if review_text is not None else None

    row = (
        db.query(AiReviewReply)
        .filter(AiReviewReply.user_id == user_id, AiReviewReply.feedback_id == feedback_id)
        .first()
    )
    created = False
    if row is None:
        row = AiReviewReply(
            user_id=user_id,
            feedback_id=feedback_id,
            product_name=product_name,
            author=author_s,
            rating=rating_s,
            review_text=review_text_s,
            suggested_reply=None,
            edited_reply=None,
            status="pending",
            last_error=None,
            first_seen_date=today,
            published_at=None,
        )
        db.add(row)
        created = True
    else:
        # Keep status if already published/skipped; still refresh snapshot fields.
        row.product_name = product_name
        row.author = author_s
        row.rating = rating_s
        row.review_text = review_text_s
        if row.status == "error":
            # allow recovering back to pending if WB still shows it unanswered
            row.status = "pending"
            row.last_error = None
        db.add(row)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        # Race-safe: re-read after concurrent insert.
        row = (
            db.query(AiReviewReply)
            .filter(AiReviewReply.user_id == user_id, AiReviewReply.feedback_id == feedback_id)
            .first()
        )
        if row is None:
            raise
        created = False
    db.refresh(row)
    return row, created


def sync_review_replies_for_user(
    *,
    db: Session,
    user_id: str,
    wb_api_key: str,
    take: int = 20,
    generate_missing_suggestions_limit: int = 10,
) -> SyncResult:
    today = date.today()
    feedbacks = fetch_unanswered_reviews(wb_api_key=wb_api_key, take=take)
    fetched = len(feedbacks)
    upserted = 0
    generated = 0

    rows: list[AiReviewReply] = []
    for fb in feedbacks:
        try:
            row, _created = _upsert_review_row(db=db, user_id=user_id, today=today, fb=fb)
        except Exception as exc:  # noqa: BLE001
            logger.warning("ai_review_replies: upsert failed user=%s err=%s", user_id, str(exc)[:300])
            continue
        rows.append(row)
        upserted += 1

    # Generate suggested replies for some pending rows without suggestion yet.
    missing = [
        r for r in rows
        if r.status == "pending" and (r.suggested_reply is None or not str(r.suggested_reply).strip())
    ]
    for r in missing[: max(0, int(generate_missing_suggestions_limit or 0))]:
        try:
            prompt = _build_reply_prompt(product_name=r.product_name, rating=r.rating, review_text=r.review_text)
            r.suggested_reply = _call_ai(prompt=prompt)
            r.last_error = None
            db.add(r)
            db.commit()
            db.refresh(r)
            generated += 1
        except Exception as exc:  # noqa: BLE001
            db.rollback()
            r.last_error = str(exc)[:800]
            r.status = "error"
            db.add(r)
            db.commit()
            generated += 0

    pending_total = (
        db.query(AiReviewReply.id)
        .filter(AiReviewReply.user_id == user_id, AiReviewReply.status == "pending")
        .count()
    )
    return SyncResult(
        fetched=fetched,
        upserted=upserted,
        suggested_generated=generated,
        pending_total=int(pending_total),
    )


def publish_review_reply(
    *,
    db: Session,
    user_id: str,
    wb_api_key: str,
    feedback_id: str,
    text: str,
) -> AiReviewReply:
    fid = str(feedback_id or "").strip()
    if not fid:
        raise ValueError("feedback_id is required")
    reply = (text or "").strip()
    if not reply:
        raise ValueError("reply text is required")

    row = (
        db.query(AiReviewReply)
        .filter(AiReviewReply.user_id == user_id, AiReviewReply.feedback_id == fid)
        .first()
    )
    if row is None:
        raise ValueError("Review not found (sync first)")
    if row.status == "published":
        return row

    headers = {"Authorization": wb_api_key, "Content-Type": "application/json"}
    payload = {"id": fid, "text": reply}
    # WB contract (Customer Communication → Feedbacks): publish via POST /feedbacks/answer
    resp = httpx.post(WB_FEEDBACKS_ANSWER_URL, headers=headers, json=payload, timeout=30.0)
    if resp.status_code != 200:
        msg = f"WB publish failed: http={resp.status_code} body={resp.text[:300]}"
        row.status = "error"
        row.last_error = msg[:800]
        db.add(row)
        db.commit()
        db.refresh(row)
        raise RuntimeError(msg)

    row.edited_reply = reply
    row.status = "published"
    row.last_error = None
    row.published_at = datetime.now(UTC)
    db.add(row)
    db.commit()
    db.refresh(row)
    return row

