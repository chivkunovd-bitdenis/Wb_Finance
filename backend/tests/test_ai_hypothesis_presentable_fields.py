from __future__ import annotations

from app.services.ai_daily_analytics_service import presentable_hypothesis_fields


def test_presentable_hypothesis_repairs_legacy_funnel_technical_line() -> None:
    t, d, tr = presentable_hypothesis_fields(
        hypothesis_type="content_change",
        title="Поменять внутренний контент карточки",
        description="Воронка ниже медианы конкурентов на 20%+",
        trigger_reason="funnel_cart: 40.0 vs median 200.0 (-80.0%) funnel_order: 15.0 vs median 100.0 (-85.0%)",
        competitor_median_metrics={
            "funnel_cart": {"our_value": 40.0, "competitor_median_value": 200.0, "unit": "%"},
            # вторая воронка «нормальная», но не ниже порога 20% — чтобы не подменялось на человеческий текст по ней
            "funnel_order": {"our_value": 50.0, "competitor_median_value": 48.0, "unit": "%"},
        },
    )
    assert "funnel_cart" not in (tr or "").lower()
    assert "vs median" not in (tr or "").lower()
    assert "200" in (d or "") or "недостоверно" in (d or "").lower()


def test_presentable_hypothesis_repairs_human_copy_when_median_is_ceiling_100() -> None:
    """Новый формат trigger_reason без «vs median»; медиана 100 п.п. — в API не показываем как факт."""
    t, d, tr = presentable_hypothesis_fields(
        hypothesis_type="content_change",
        title="Обновить текст и медиа внутри карточки",
        description="Конверсии в корзину или в заказ заметно слабее…",
        trigger_reason=(
            "Рекомендуется обновить наполнение карточки, потому что конверсия в заказ — ниже, "
            "чем у конкурентов в сравнении примерно на 85% (у вас 15.0, по медиане конкурентов 100.0)."
        ),
        competitor_median_metrics={
            "funnel_order": {"our_value": 15.0, "competitor_median_value": 100.0, "unit": "%"},
        },
    )
    assert "100.0" not in (tr or "")
    assert "по медиане конкурентов 100" not in (tr or "").lower()
    assert "недостоверно" in (d or "").lower()


def test_presentable_hypothesis_rebuilds_human_text_when_metrics_sane() -> None:
    t, d, tr = presentable_hypothesis_fields(
        hypothesis_type="content_change",
        title="Поменять внутренний контент карточки",
        description="old",
        trigger_reason="funnel_cart: 1 vs 2",
        competitor_median_metrics={
            "funnel_cart": {"our_value": 8.0, "competitor_median_value": 14.0, "unit": "%"},
        },
    )
    assert t == "Обновить текст и медиа внутри карточки"
    assert tr and "Рекомендуется" in tr
    assert "funnel_cart" not in tr.lower()
