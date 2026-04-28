from celery_app.tasks import _intents_merge, _intents_with_lane


def test_intents_merge_recursively_unions_lists_and_overwrites_scalars():
    base = {
        "high": {"funnel_tail": True, "finance_range": {"date_from": "2026-04-01", "date_to": "2026-04-07"}},
        "low": {"finance_backfill_year": 2026, "tags": ["a", "b"]},
    }
    patch = {
        "high": {"finance_range": {"date_from": "2026-04-02", "date_to": "2026-04-08"}},
        "low": {"tags": ["b", "c"]},
    }
    out = _intents_merge(base, patch)
    # recursive overwrite for nested dict scalars
    assert out["high"]["finance_range"]["date_from"] == "2026-04-02"
    assert out["high"]["finance_range"]["date_to"] == "2026-04-08"
    # keep other keys
    assert out["high"]["funnel_tail"] is True
    # list union unique stable
    assert out["low"]["tags"] == ["a", "b", "c"]


def test_intents_with_lane_replaces_or_removes_consumed_lane():
    base = {
        "high": {"funnel_tail": True, "finance_range": {"date_from": "2026-04-01", "date_to": "2026-04-07"}},
        "low": {"finance_backfill_year": 2026},
    }

    out = _intents_with_lane(base, "high", {"funnel_tail": True})

    assert out["high"] == {"funnel_tail": True}
    assert out["low"] == {"finance_backfill_year": 2026}

    out = _intents_with_lane(out, "high", {})

    assert "high" not in out
    assert out["low"] == {"finance_backfill_year": 2026}

