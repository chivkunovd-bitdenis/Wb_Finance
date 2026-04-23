from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

from sqlalchemy.orm import Session

from app.models.pnl_daily import PnlDaily


@dataclass(frozen=True)
class DateRange:
    date_from: date
    date_to: date


def compute_missing_tail_range(
    db: Session,
    *,
    user_id: str,
    through: date,
    lookback_days: int = 45,
) -> DateRange | None:
    """
    Найти "хвостовую" дыру в pnl_daily, которая заканчивается на `through` (обычно вчера).

    Возвращает диапазон [date_from..date_to], где date_to == through.
    Если `through` уже присутствует в pnl_daily — возвращает None.

    Важно: мы намеренно НЕ пытаемся чинить "дыры в середине" при входе пользователя.
    """
    if lookback_days <= 0:
        return None
    window_from = through - timedelta(days=lookback_days)
    rows = (
        db.query(PnlDaily.date)
        .filter(
            PnlDaily.user_id == user_id,
            PnlDaily.date >= window_from,
            PnlDaily.date <= through,
        )
        .all()
    )
    present = {r[0] for r in rows if r and r[0] is not None}
    if through in present:
        return None

    # Хвостовая дыра: идём от through назад, пока дат нет.
    cursor = through
    missing_start = through
    while cursor >= window_from:
        if cursor in present:
            break
        missing_start = cursor
        cursor -= timedelta(days=1)

    # Если в окне нет НИ ОДНОЙ даты — это не warm-path, пусть это обрабатывается cold-start/backfill.
    if not present:
        return None

    return DateRange(date_from=missing_start, date_to=through)


def compute_missing_ranges_in_window(
    db: Session,
    *,
    user_id: str,
    date_from: date,
    date_to: date,
) -> list[DateRange]:
    """
    Найти все "дыры" в pnl_daily на отрезке [date_from..date_to] (включительно).

    Возвращает список диапазонов отсутствующих дней, отсортированный от более новых к более старым.
    """
    if date_from > date_to:
        return []
    rows = (
        db.query(PnlDaily.date)
        .filter(
            PnlDaily.user_id == user_id,
            PnlDaily.date >= date_from,
            PnlDaily.date <= date_to,
        )
        .all()
    )
    present = {r[0] for r in rows if r and r[0] is not None}
    if not present:
        return []

    missing: list[DateRange] = []
    cur = date_to
    run_end: date | None = None
    run_start: date | None = None
    while cur >= date_from:
        if cur not in present:
            if run_end is None:
                run_end = cur
                run_start = cur
            else:
                run_start = cur
        else:
            if run_end is not None and run_start is not None:
                missing.append(DateRange(date_from=run_start, date_to=run_end))
                run_end = None
                run_start = None
        cur -= timedelta(days=1)
    if run_end is not None and run_start is not None:
        missing.append(DateRange(date_from=run_start, date_to=run_end))

    return missing

